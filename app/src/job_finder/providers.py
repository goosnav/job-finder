"""Bounded adapters for a user's installed Codex or Claude Code CLI.

The adapter owns process lifecycle and transport, not the search policy. The
application validates returned data with its Pydantic models before saving it.
No command is executed through a shell and no provider credentials are copied.
"""

from __future__ import annotations

import json
import math
import ntpath
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentResult:
    data: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class ProviderError(Exception):
    """Stable, safe-to-display failure; never includes raw provider output."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


_PROVIDERS = ("codex", "claude")
_NPM_ENTRIES = {"codex": "@openai/codex/bin/codex.js", "claude": "@anthropic-ai/claude-code/cli.js"}


def _install_directories() -> list[Path]:
    home = Path(os.getenv("USERPROFILE") or Path.home())
    roots = [home / ".local/bin", home / ".npm-global/bin", home / ".volta/bin"]
    for variable, suffix in (
        ("APPDATA", "npm"),
        ("LOCALAPPDATA", "npm"),
        ("LOCALAPPDATA", "Microsoft/WinGet/Links"),
        ("LOCALAPPDATA", "Programs/nodejs"),
        ("LOCALAPPDATA", "Volta/bin"),
        ("ProgramFiles", "nodejs"),
        ("ProgramFiles(x86)", "nodejs"),
        ("ProgramFiles", "Git/bin"),
        ("ProgramFiles", "Git/cmd"),
        ("ProgramFiles(x86)", "Git/bin"),
        ("ProgramFiles(x86)", "Git/cmd"),
        ("LOCALAPPDATA", "Programs/Git/bin"),
        ("SystemRoot", "System32"),
        ("SystemRoot", "System32/WindowsPowerShell/v1.0"),
        ("NVM_SYMLINK", ""),
        ("VOLTA_HOME", "bin"),
    ):
        if value := os.getenv(variable):
            roots.append(Path(value) / suffix)
    roots += [home / "AppData/Roaming/npm", home / "AppData/Local/Microsoft/WinGet/Links"]
    if sys.platform == "win32":
        try:
            import winreg
        except ImportError:
            pass
        else:
            for hive, key in (
                (winreg.HKEY_CURRENT_USER, "Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as environment:
                        value, _ = winreg.QueryValueEx(environment, "Path")
                    if isinstance(value, str):
                        roots += [Path(path) for part in ntpath.expandvars(value).split(";") if (path := part.strip().strip('"'))]
                except OSError:
                    continue
    else:
        roots += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin")]
    return list(dict.fromkeys(roots))


def _agent_environment(command: list[str]) -> dict[str, str]:
    """Carry recovered Windows tool locations into the agent and its children.

    Explorer may still have the PATH from before Node/Git/the CLI was installed.
    Finding the agent by absolute path alone does not repair its tool environment.
    Keep sign-in, proxy, certificate and provider settings; change only this child.
    """
    environment = os.environ.copy()
    if sys.platform != "win32":
        return environment
    paths = [part for part in environment.get("PATH", "").split(os.pathsep) if part]
    seen = {ntpath.normcase(part.strip('"')) for part in paths}
    directories = [Path(command[0]).parent, *_install_directories()]
    for directory in directories:
        value = str(directory)
        if directory.is_dir() and ntpath.normcase(value) not in seen:
            paths.append(value)
            seen.add(ntpath.normcase(value))
    environment["PATH"] = os.pathsep.join(paths)
    if not environment.get("CLAUDE_CODE_GIT_BASH_PATH"):
        for directory in directories:
            # Registry PATH usually contains Git/cmd. Git Bash lives in Git/bin.
            # Do not select System32/bash.exe (WSL) as a Git Bash installation.
            bash = next(
                (
                    path
                    for path in (directory / "bash.exe", directory.parent / "bin/bash.exe")
                    if path.is_file() and (path.parent / "git.exe").is_file()
                ),
                None,
            )
            if bash:
                environment["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
                break
    return environment


def _resolve_executable(provider: str, executable: str | None = None) -> list[str]:
    """Resolve native CLIs or known npm entrypoints without executing a shell shim."""
    if provider not in _PROVIDERS:
        raise ProviderError("unsupported_provider", "Choose Codex or Claude Code.")
    override = executable or os.getenv(f"JOB_FINDER_{provider.upper()}_BIN")
    directories = _install_directories()
    candidates = [
        Path(found) for name in (provider, provider + ".exe") if (found := shutil.which(name))
    ]
    if override:
        found = shutil.which(override)
        candidates = [Path(found)] if found else []
        directories = [Path(override).parent]
    else:
        # Resolve the current PATH entry fully before trying stale-environment fallbacks.
        for candidate in candidates:
            try:
                return _resolve_executable(provider, str(candidate))
            except ProviderError:
                continue
        candidates += [
            directory / (provider + suffix)
            for directory in directories
            for suffix in (".exe", "", ".cmd")
        ]
    candidates = [
        candidate.with_suffix(".exe")
        for candidate in candidates
        if candidate.suffix.lower() in {".cmd", ".bat", ".ps1"}
    ] + candidates
    package_roots = list(
        dict.fromkeys([candidate.parent for candidate in candidates] + directories)
    )
    entries = []
    for root in package_roots:
        for node_modules in (root / "node_modules", root.parent / "lib/node_modules"):
            entries.append(node_modules / _NPM_ENTRIES[provider])
            if sys.platform == "win32":
                architectures = [
                    ("x64", "x86_64-pc-windows-msvc"),
                    ("arm64", "aarch64-pc-windows-msvc"),
                ]
                processor = os.getenv("PROCESSOR_ARCHITEW6432") or os.getenv("PROCESSOR_ARCHITECTURE", "")
                if processor.upper() == "ARM64":
                    architectures.reverse()
                for arch, triple in architectures:
                    if provider == "codex":
                        candidates += [
                            node_modules / package / "vendor" / triple / folder / "codex.exe"
                            for package in ("@openai/codex", f"@openai/codex-win32-{arch}")
                            for folder in ("bin", "codex")
                        ]
                    else:
                        candidates.append(node_modules / f"@anthropic-ai/claude-code-win32-{arch}/claude.exe")
                if provider == "claude":
                    candidates.append(node_modules / "@anthropic-ai/claude-code/bin/claude.exe")
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() not in {
            ".cmd",
            ".bat",
            ".ps1",
            ".js",
            ".mjs",
            ".cjs",
        }:
            resolved = candidate.resolve()
            if resolved.suffix.lower() in {".cmd", ".bat", ".ps1"}:
                continue
            if sys.platform == "win32" and resolved.suffix.lower() != ".exe":
                continue
            if resolved.suffix.lower() not in {".js", ".mjs", ".cjs"}:
                return [str(resolved)]
            entries.insert(0, resolved)
    entries += [
        candidate.resolve()
        for candidate in candidates
        if candidate.is_file() and candidate.suffix.lower() in {".js", ".mjs", ".cjs"}
    ]
    for entry in dict.fromkeys(entries):
        if not entry.is_file() or not entry.as_posix().endswith(_NPM_ENTRIES[provider]):
            continue
        nodes = [Path(found) for name in ("node", "node.exe") if (found := shutil.which(name))]
        nodes += [
            directory / name
            for directory in package_roots + _install_directories()
            for name in ("node.exe", "node")
        ]
        node = next(
            (
                path.resolve()
                for path in nodes
                if path.is_file() and path.resolve().suffix.lower() not in {".cmd", ".bat", ".ps1"}
                and (sys.platform != "win32" or path.resolve().suffix.lower() == ".exe")
            ),
            None,
        )
        if node:
            return [str(node), str(entry.resolve())]
        raise ProviderError(
            "node_missing",
            "The installed npm CLI needs Node.js. Repair or reinstall Node.js, then retry.",
        )
    raise ProviderError(
        "not_installed",
        f"Could not find an installed {provider} CLI. Install it, then check again.",
    )


def recovery_for(provider: str, code: str) -> dict[str, Any]:
    if provider not in _PROVIDERS:
        raise ProviderError("unsupported_provider", "Choose Codex or Claude Code.")
    login = "codex login" if provider == "codex" else "claude auth login"
    url = (
        "https://developers.openai.com/codex/cli/"
        if provider == "codex"
        else "https://code.claude.com/docs/en/setup"
    )
    recovery = {
        "not_installed": (
            "Install your agent",
            ["Install the CLI on this computer, then check again."],
            None,
            url,
        ),
        "node_missing": (
            "Repair Node.js",
            ["The npm CLI was found, but its Node.js runtime was not. Repair Node.js, then retry."],
            None,
            "https://nodejs.org/en/download",
        ),
        "launch_failed": (
            "Repair your agent installation",
            ["The installed CLI could not start. Repair its installation, then retry."],
            None,
            url,
        ),
        "login_unavailable": (
            "Open CLI sign-in",
            ["Open a terminal, run the sign-in command, then return and retry."],
            login,
            None,
        ),
        "auth_required": (
            "Sign in to your agent",
            ["Open sign-in and complete the CLI login.", "Return here and retry the saved task."],
            login,
            None,
        ),
        "consent_required": (
            "Approve agent access",
            ["Review and approve agent access in Job Finder before running a task."],
            None,
            None,
        ),
        "permission_denied": (
            "Check agent permissions",
            [
                "The agent or a tool denied access. Review your CLI and organization permission settings.",
                "Retry the saved task after access is allowed.",
            ],
            None,
            None,
        ),
        "launch_permission_denied": (
            "Allow the CLI to open",
            [
                "The operating system blocked the installed CLI. Check its file permissions or security prompt, then retry."
            ],
            None,
            None,
        ),
        "unsupported_cli": (
            "Update your agent",
            ["The actual CLI command rejected an option. Update the installed CLI, then retry."],
            "codex --version" if provider == "codex" else "claude update",
            url,
        ),
        "model_unavailable": (
            "Choose a supported model",
            [
                "In Inputs → Agent settings, choose a model supported by your CLI account.",
                "Save the inputs, then retry the saved task.",
            ],
            None,
            None,
        ),
        "network_error": (
            "Check the connection",
            ["Check your internet, proxy, or service status, then retry the saved task."],
            None,
            None,
        ),
        "rate_limited": (
            "Agent usage limit reached",
            ["Wait for your provider allowance to reset or review your plan, then retry."],
            None,
            None,
        ),
        "agent_budget": (
            "Task limit reached",
            ["Increase the task turn or cost limit, or narrow the search, then retry."],
            None,
            None,
        ),
        "timeout": (
            "Task timed out",
            ["Increase the task time limit or narrow the search, then retry."],
            None,
            None,
        ),
    }
    title, steps, command, link = recovery.get(
        code,
        (
            "Retry your agent",
            ["Retry the saved task. If it fails again, check the CLI and provider service status."],
            None,
            None,
        ),
    )
    return {
        "title": title,
        "steps": steps,
        **({"command": command} if command else {}),
        **({"url": link} if link else {}),
    }


def open_provider_login(provider: str) -> dict[str, Any]:
    command = _resolve_executable(provider) + (
        ["login"] if provider == "codex" else ["auth", "login"]
    )
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                command, shell=False, env=_agent_environment(command),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
            )
        elif sys.platform == "darwin":
            shell_command = shlex.join(command)
            escaped = shell_command.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    f'tell application "Terminal" to do script "{escaped}"',
                ],
                check=True,
                timeout=10,
                capture_output=True,
            )
        else:
            terminal = next(
                (
                    (path, args)
                    for name, args in (
                        ("x-terminal-emulator", ["-e"]),
                        ("gnome-terminal", ["--"]),
                        ("konsole", ["-e"]),
                        ("xterm", ["-e"]),
                    )
                    if (path := shutil.which(name))
                ),
                None,
            )
            if not terminal:
                raise ProviderError(
                    "login_unavailable",
                    f"Open a terminal and run {recovery_for(provider, 'auth_required')['command']}, then retry.",
                )
            subprocess.Popen(
                [terminal[0], *terminal[1], *command], shell=False, start_new_session=True
            )
    except PermissionError as exc:
        raise ProviderError(
            "launch_permission_denied", "The operating system blocked the sign-in window."
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderError(
            "login_unavailable",
            f"Could not open sign-in. Run {recovery_for(provider, 'auth_required')['command']} in your terminal, then retry.",
        ) from exc
    return {"opened": True, "provider": provider}


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate this run's children, including a CLI blocked on a tool."""
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        # Every call creates a new session. Never address the parent's group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        # Also kill descendants if the group leader exited after SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _execute(
    argv: list[str],
    *,
    cwd: str,
    stdin: str = "",
    timeout: float,
    max_bytes: int,
    cancel: threading.Event | None = None,
    result_path: Path | None = None,
) -> tuple[int, bytes, bytes]:
    """Drain both pipes concurrently; cap their combined in-memory allocation."""
    if cancel and cancel.is_set():
        raise ProviderError("cancelled", "Agent run cancelled.")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=_agent_environment(argv),
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            ),
        )
    except PermissionError as exc:
        raise ProviderError(
            "launch_permission_denied",
            "The operating system blocked the installed agent executable.",
        ) from exc
    except OSError as exc:
        raise ProviderError(
            "launch_failed",
            "Could not start the installed agent. Repair its installation, then retry.",
        ) from exc

    buffers = [bytearray(), bytearray()]
    lock = threading.Lock()
    over_limit = threading.Event()
    total_bytes = 0

    def read_pipe(index: int) -> None:
        nonlocal total_bytes
        pipe = process.stdout if index == 0 else process.stderr
        assert pipe is not None
        try:
            while chunk := pipe.read(16384):
                with lock:
                    room = max(0, max_bytes - total_bytes)
                    buffers[index].extend(chunk[:room])
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        over_limit.set()
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def write_prompt() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(stdin.encode("utf-8"))
            process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            process.stdin.close()

    readers = [threading.Thread(target=read_pipe, args=(i,), daemon=True) for i in (0, 1)]
    writer = threading.Thread(target=write_prompt, daemon=True)
    for thread in [*readers, writer]:
        thread.start()
    deadline = time.monotonic() + timeout
    error: ProviderError | None = None
    try:
        while process.poll() is None:
            if cancel and cancel.is_set():
                error = ProviderError("cancelled", "Agent run cancelled.")
                break
            if time.monotonic() >= deadline:
                error = ProviderError(
                    "timeout",
                    "Agent time limit reached. Progress is saved; resume to retry this item.",
                )
                break
            file_too_large = (
                result_path is not None
                and result_path.exists()
                and result_path.stat().st_size > max_bytes
            )
            if over_limit.is_set() or file_too_large:
                error = ProviderError("output_limit", "Agent output exceeded the size limit.")
                break
            time.sleep(0.04)
    except BaseException:
        _terminate_tree(process)
        raise
    finally:
        if process.poll() is None:
            _terminate_tree(process)
        for thread in [*readers, writer]:
            thread.join(timeout=0.5)
        if any(thread.is_alive() for thread in readers):
            # A descendant may still hold a pipe after its parent exits.
            _terminate_tree(process)
            for thread in readers:
                thread.join(timeout=0.5)
    if error:
        raise error
    if over_limit.is_set():
        raise ProviderError("output_limit", "Agent output exceeded the size limit.")
    if cancel and cancel.is_set():
        raise ProviderError("cancelled", "Agent run cancelled.")
    return process.returncode or 0, bytes(buffers[0]), bytes(buffers[1])


def _probe(provider: str, command: list[str]) -> str:
    with tempfile.TemporaryDirectory(prefix="job-finder-check-") as directory:
        code, out, _ = _execute(
            [*command, "--version"], cwd=directory, timeout=8, max_bytes=128_000
        )
        version = re.search(r"\b\d+\.\d+\.\d+(?:[-+][\w.]+)?\b", out.decode("utf-8", "replace"))
        if code or not version:
            raise ProviderError(
                "version_unavailable",
                "Version check was unavailable. The actual task can still be tried.",
            )
        return version.group(0)


def inspect_providers() -> list[dict[str, Any]]:
    """Discover installations without invoking a model or reading credentials."""
    result = []
    for provider in _PROVIDERS:
        try:
            command = _resolve_executable(provider)
        except ProviderError as exc:
            result.append(
                {
                    "id": provider,
                    "installed": exc.code == "node_missing",
                    "available": False,
                    "path": None,
                    "version": None,
                    "advisory": "",
                    "message": exc.message,
                    "code": exc.code,
                    "recovery": recovery_for(provider, exc.code),
                }
            )
            continue
        version, advisory = None, ""
        try:
            version = _probe(provider, command)
        except ProviderError as exc:
            advisory = exc.message
        result.append(
            {
                "id": provider,
                "installed": True,
                "available": True,
                "path": command[-1],
                "version": version,
                "advisory": advisory,
                "message": "Installed. Sign-in and access are checked by the actual task.",
            }
        )
    return result


def _failure_text(output: bytes) -> tuple[str, bool]:
    text = output.decode("utf-8", "replace")
    try:
        documents = [json.loads(text)]
    except (ValueError, UnicodeDecodeError):
        documents = []
        for line in text.splitlines():
            try:
                documents.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                pass
    if not documents:
        return text.lower(), False
    messages, denied = [], False

    def errors(value):
        if isinstance(value, str):
            messages.append(value)
        elif isinstance(value, list):
            for item in value:
                errors(item)
        elif isinstance(value, dict):
            for key in ("message", "error", "errors", "code", "type", "status_code"):
                if key in value:
                    errors(value[key])
        elif isinstance(value, int) and not isinstance(value, bool):
            messages.append(str(value))

    for document in documents:
        if not isinstance(document, dict):
            continue
        denied |= bool(document.get("permission_denials"))
        for key in ("error", "errors", "message", "subtype", "status_code"):
            if key in document:
                errors(document[key])
        if document.get("is_error") and isinstance(document.get("result"), str):
            errors(document["result"])
    return "\n".join(messages).lower(), denied


def _provider_failure(provider: str, output: bytes, exit_code: int = 0) -> ProviderError:
    # Query/tool inputs and private result objects are never diagnostic evidence.
    lowered, denied = _failure_text(output)
    if any(
        term in lowered
        for term in (
            "unknown option",
            "unexpected argument",
            "unrecognized argument",
            "unknown feature",
            "unknown field",
        )
    ):
        return ProviderError(
            "unsupported_cli",
            f"The installed {provider} rejected a task option. Update the CLI, then retry.",
        )
    if any(term in lowered for term in ("model_not_found", "unsupported_model", "model_access_denied")) or (
        re.search(r"\bmodels?\b", lowered)
        and any(
            term in lowered
            for term in (
                "not_found_error", "not found", "does not exist", "not supported", "unsupported",
                "not available", "not authorized", "do not have access", "does not have access",
                "don't have access",
            )
        )
    ):
        return ProviderError(
            "model_unavailable",
            "The selected model is unavailable for this CLI account. Choose a supported model in Inputs → Agent settings, then retry.",
        )
    if any(
        term in lowered
        for term in (
            "not logged",
            "unauthorized",
            "authentication failed",
            "authentication required",
            "authentication_error",
            "invalid api key",
            "missing api key",
            "login required",
            "please log in",
        )
    ) or re.search(r"\b401\b", lowered):
        command = "codex login" if provider == "codex" else "claude auth login"
        return ProviderError("auth_required", f"Sign in with {command}, then retry the saved task.")
    if (
        denied
        or any(
            term in lowered
            for term in (
                "permission denied",
                "permission_denied",
                "permission denial",
                "not permitted",
                "not allowed",
                "access denied",
                "access is denied",
                "operation not permitted",
                "requires approval",
            )
        )
        or re.search(r"\b403\b", lowered)
    ):
        return ProviderError(
            "permission_denied",
            "The agent or a tool denied access. Review its permission settings, then retry.",
        )
    if any(term in lowered for term in ("rate limit", "usage limit", "quota")) or re.search(
        r"\b429\b", lowered
    ):
        return ProviderError(
            "rate_limited", "Agent usage limit reached. Wait for your limit to reset, then retry."
        )
    if any(
        term in lowered
        for term in ("max_turns", "max turns", "max_budget", "max budget", "budget limit")
    ):
        return ProviderError(
            "agent_budget",
            "Agent turn or cost limit reached. Increase the limit or narrow the request, then retry.",
        )
    if any(term in lowered for term in ("connection", "network", "timed out", "dns", "tls")):
        return ProviderError(
            "network_error",
            "The agent could not reach its service. Check the connection, then retry.",
        )
    return ProviderError(
        "provider_failed",
        f"{provider.title()} could not complete this item"
        + (f" (exit {exit_code})" if exit_code else "")
        + ". Retry the saved task or check the provider service status.",
    )


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    if not (-float("inf") < value < float("inf")):
        return None
    return int(value) if integer else float(value)


class AgentRunner:
    def __init__(
        self,
        provider: str,
        model: str = "",
        timeout: int = 180,
        max_turns: int = 8,
        max_cost_usd: float = 1.0,
        max_output_bytes: int = 2_000_000,
        executable: str | None = None,
        allow_tools: bool = False,
    ):
        if provider not in _PROVIDERS:
            raise ProviderError("unsupported_provider", "Choose Codex or Claude Code.")
        if (
            not math.isfinite(timeout)
            or not math.isfinite(max_cost_usd)
            or timeout <= 0
            or max_turns < 1
            or max_cost_usd <= 0
            or max_output_bytes < 1024
        ):
            raise ProviderError("invalid_limits", "Agent limits must be positive.")
        self.provider = provider
        self.model = model
        self.timeout = timeout
        self.max_turns = max_turns
        self.max_cost_usd = max_cost_usd
        self.max_output_bytes = max_output_bytes
        self.executable = executable
        self.allow_tools = allow_tools

    def run(
        self,
        prompt: str,
        schema: dict,
        *,
        web: bool = True,
        cancel: threading.Event | None = None,
    ) -> AgentResult:
        if not self.allow_tools:
            raise ProviderError(
                "consent_required", "Approve agent tool access in Job Finder before running a task."
            )
        if len(prompt.encode("utf-8")) > 400_000:
            raise ProviderError(
                "input_limit", "The agent input is too large. Reduce the source material."
            )
        if cancel and cancel.is_set():
            raise ProviderError("cancelled", "Agent run cancelled.")
        command = _resolve_executable(self.provider, self.executable)
        with tempfile.TemporaryDirectory(prefix="job-finder-agent-") as directory:
            schema_path = Path(directory) / "schema.json"
            result_path = Path(directory) / "result.json"
            schema_json = json.dumps(schema, ensure_ascii=False, allow_nan=False)
            if len(schema_json.encode("utf-8")) > 100_000:
                raise ProviderError("input_limit", "The output schema is too large.")
            if self.provider == "codex":
                schema_path.write_text(schema_json, encoding="utf-8")
                argv = [
                    *command,
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--json",
                    "--color",
                    "never",
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(result_path),
                ]
                if web:
                    argv += ["-c", 'web_search="live"']
                if self.model:
                    argv += ["--model", self.model]
                argv += ["-"]
            else:
                argv = [
                    *command,
                    "--dangerously-skip-permissions",
                    "--settings",
                    '{"sandbox":{"enabled":false}}',
                    "-p",
                    "--output-format",
                    "json",
                    "--json-schema",
                    schema_json,
                    "--no-session-persistence",
                    "--max-turns",
                    str(self.max_turns),
                    "--max-budget-usd",
                    str(self.max_cost_usd),
                ]
                if self.model:
                    argv += ["--model", self.model]
            code, stdout, stderr = _execute(
                argv,
                cwd=directory,
                stdin=prompt,
                timeout=self.timeout,
                max_bytes=self.max_output_bytes,
                cancel=cancel,
                result_path=result_path,
            )
            if code:
                raise _provider_failure(self.provider, stdout + stderr, code)
            if self.provider == "codex":
                return self._parse_codex(stdout, result_path)
            return self._parse_claude(stdout)

    def _parse_codex(self, stdout: bytes, result_path: Path) -> AgentResult:
        usage: dict[str, Any] = {}
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ProviderError(
                    "invalid_response", "Codex returned an invalid event stream."
                ) from exc
            if not isinstance(event, dict):
                raise ProviderError("invalid_response", "Codex returned an invalid event stream.")
            if event.get("type") in {"error", "turn.failed"}:
                raise _provider_failure("codex", line)
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
        if not result_path.is_file():
            raise ProviderError(
                "missing_result", "Codex returned no structured result. Resume to retry this item."
            )
        if result_path.stat().st_size > self.max_output_bytes:
            raise ProviderError("output_limit", "Agent output exceeded the size limit.")
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            raise ProviderError(
                "invalid_response", "Codex returned invalid structured data."
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "The agent result must be a JSON object.")
        return AgentResult(
            data,
            input_tokens=_number(usage.get("input_tokens"), integer=True),
            output_tokens=_number(usage.get("output_tokens"), integer=True),
        )

    def _parse_claude(self, stdout: bytes) -> AgentResult:
        try:
            envelope = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(
                "invalid_response", "Claude returned invalid structured data."
            ) from exc
        if not isinstance(envelope, dict):
            raise ProviderError("invalid_response", "Claude returned an invalid result envelope.")
        if envelope.get("is_error") or str(envelope.get("subtype", "")).startswith("error"):
            raise _provider_failure("claude", stdout)
        data = envelope.get("structured_output")
        if not isinstance(data, dict):
            if envelope.get("permission_denials"):
                raise _provider_failure("claude", stdout)
            raise ProviderError(
                "missing_result", "Claude returned no structured result. Resume to retry this item."
            )
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return AgentResult(
            data,
            input_tokens=_number(usage.get("input_tokens"), integer=True),
            output_tokens=_number(usage.get("output_tokens"), integer=True),
            cost_usd=_number(envelope.get("total_cost_usd")),
        )
