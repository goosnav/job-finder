"""Executable contract tests. These never invoke a paid model or use credentials."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from job_finder import providers
from job_finder.providers import AgentRunner, ProviderError, inspect_providers

SCHEMA = {
    "type": "object",
    "properties": {"company": {"type": "string"}},
    "required": ["company"],
    "additionalProperties": False,
}

FAKE_CLI = r'''
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")
if "--version" in args:
    if os.environ.get("FAKE_VERSION_FAIL"):
        raise SystemExit(1)
    print("codex-cli 0.163.0")
    raise SystemExit(0)
if "--help" in args:
    flags = """--ignore-user-config --ephemeral --skip-git-repo-check --sandbox
    --json --config --strict-config --output-schema --output-last-message
    --safe-mode --output-format --json-schema --tools --allowedTools
    --disallowedTools --strict-mcp-config --mcp-config --permission-mode
    --no-session-persistence --max-turns --max-budget-usd"""
    if os.environ.get("FAKE_OLD"):
        flags = flags.replace("--safe-mode", "").replace("--ignore-user-config", "")
    print(flags)
    raise SystemExit(0)
prompt = sys.stdin.read()
Path(os.environ["FAKE_LOG"]).write_text(json.dumps({"argv": args, "prompt": prompt, "cwd": os.getcwd()}))
mode = os.environ.get("FAKE_MODE", "success")
if mode == "unsupported":
    print("error: unknown option --max-turns", file=sys.stderr)
    raise SystemExit(1)
if mode == "permission":
    print(json.dumps({"is_error": True, "errors": ["WebSearch permission denied"], "permission_denials": [{"tool_input": {"query": "authentication engineer 1401234"}}]}))
    raise SystemExit(1)
if mode == "auth":
    print("Authentication failed; private token sk-do-not-expose", file=sys.stderr)
    raise SystemExit(1)
if mode == "rate":
    print("429 quota exhausted", file=sys.stderr)
    raise SystemExit(1)
if mode == "flood":
    sys.stderr.write("private-log" * 100_000)
    sys.stderr.flush()
    time.sleep(5)
if mode == "hang":
    time.sleep(30)
if mode == "child":
    p = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"])
    Path(os.environ["FAKE_CHILD_PID"]).write_text(str(p.pid))
    time.sleep(30)
if mode == "malformed":
    print("not JSON")
    raise SystemExit(0)
if args and args[0] == "exec":
    if mode == "event_error":
        print(json.dumps({"type": "turn.failed", "error": {"message": "authentication failed secret"}}))
        raise SystemExit(0)
    if mode != "missing":
        target = Path(args[args.index("--output-last-message") + 1])
        if mode == "large_file":
            target.write_text("x" * 100_000)
        elif mode == "array":
            target.write_text("[]")
        else:
            target.write_text(json.dumps({"company": "Example machine shop"}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 34}}))
else:
    envelope = {"type": "result", "subtype": "success", "is_error": False,
                "structured_output": {"company": "Example machine shop"},
                "usage": {"input_tokens": 120, "output_tokens": 34}, "total_cost_usd": 0.004}
    if mode == "missing":
        del envelope["structured_output"]
    if mode == "array":
        envelope["structured_output"] = []
    if mode == "event_error":
        envelope["is_error"] = True
        envelope["subtype"] = "error_max_turns"
    print(json.dumps(envelope))
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    executable = tmp_path / "fake-agent"
    executable.write_text(f"#!{sys.executable}\n" + FAKE_CLI, encoding="utf-8")
    executable.chmod(0o700)
    log = tmp_path / "calls.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("JOB_FINDER_CODEX_BIN", str(executable))
    monkeypatch.setenv("JOB_FINDER_CLAUDE_BIN", str(executable))
    if os.name == "nt":
        resolve = providers._resolve_executable

        def resolve_fixture(provider, override=None):
            selected = override or os.getenv(f"JOB_FINDER_{provider.upper()}_BIN")
            if selected == str(executable):
                return [sys.executable, str(executable)]
            return resolve(provider, override)

        monkeypatch.setattr(providers, "_resolve_executable", resolve_fixture)
    return executable, log


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_success_transport_isolated_prompt_and_usage(provider, fake_cli, tmp_path):
    executable, log = fake_cli
    sentinel = tmp_path / "must-not-exist"
    prompt = f'Research Δ café: $(touch "{sentinel}") `whoami`; & < >'
    result = AgentRunner(
        provider, executable=str(executable), model="test-model", allow_tools=True
    ).run(prompt, SCHEMA)
    assert result.data == {"company": "Example machine shop"}
    assert result.input_tokens == 120
    assert result.output_tokens == 34
    assert result.cost_usd == (0.004 if provider == "claude" else None)
    call = json.loads(log.read_text())
    assert call["prompt"] == prompt
    assert (
        prompt not in call["argv"]
    )  # stdin does not expose the prompt through process argv.
    assert not sentinel.exists()
    assert Path(call["cwd"]) != Path.cwd()
    assert not Path(call["cwd"]).exists()  # no lingering prompt/results workspace.
    assert call["argv"][call["argv"].index("--model") + 1] == "test-model"


def test_codex_approved_access_inherits_config_and_tools(fake_cli):
    executable, log = fake_cli
    AgentRunner("codex", executable=str(executable), allow_tools=True).run(
        "Draft only", SCHEMA, web=False
    )
    args = json.loads(log.read_text())["argv"]
    assert args[0] == "exec" and args[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--ephemeral" in args
    assert "--ignore-user-config" not in args
    assert "--strict-config" not in args
    assert "--sandbox" not in args and "-c" not in args
    assert not any("features." in arg or "web_search=" in arg for arg in args)


def test_codex_research_requests_live_web_without_tool_restrictions(fake_cli):
    executable, log = fake_cli
    AgentRunner("codex", executable=str(executable), allow_tools=True).run(
        "Find jobs", SCHEMA, web=True
    )
    args = json.loads(log.read_text())["argv"]
    assert args[args.index("-c") + 1] == 'web_search="live"'
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert not any("features." in arg or '="disabled"' in arg for arg in args)


@pytest.mark.parametrize("web", [True, False])
def test_claude_approved_access_inherits_config_and_tools(fake_cli, web):
    executable, log = fake_cli
    AgentRunner(
        "claude",
        executable=str(executable),
        max_turns=6,
        max_cost_usd=0.5,
        allow_tools=True,
    ).run("Research", SCHEMA, web=web)
    args = json.loads(log.read_text())["argv"]
    assert "--dangerously-skip-permissions" in args
    assert not set(args) & {
        "--safe-mode",
        "--bare",
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--permission-mode",
        "--strict-mcp-config",
        "--mcp-config",
        "--bypass-hook-trust",
    }
    assert args[args.index("--max-turns") + 1] == "6"
    assert args[args.index("--max-budget-usd") + 1] == "0.5"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA


def test_claude_bypass_also_disables_the_optional_shell_sandbox(fake_cli):
    executable, log = fake_cli
    AgentRunner("claude", executable=str(executable), allow_tools=True).run("Research", SCHEMA)
    args = json.loads(log.read_text())['argv']
    assert json.loads(args[args.index('--settings') + 1]) == {"sandbox": {"enabled": False}}


def test_windows_children_receive_recovered_tool_path_without_changing_parent(tmp_path, monkeypatch):
    tools = tmp_path / "Newly installed tools Δ"
    tools.mkdir()
    helper = tools / "job-finder-network.exe"
    helper.touch()
    helper.chmod(0o700)
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers, "_install_directories", lambda: [tools])
    monkeypatch.setenv("PATH", str(tmp_path / "Old Explorer PATH"))
    monkeypatch.setenv("HTTPS_PROXY", "http://test-proxy.invalid:8080")
    before = dict(os.environ)
    code, out, err = providers._execute(
        [sys.executable, "-c", "import json,os,shutil; print(json.dumps([shutil.which('job-finder-network.exe'),os.getenv('HTTPS_PROXY')]))"],
        cwd=str(tmp_path), timeout=10, max_bytes=4096,
    )
    assert code == 0, err
    assert json.loads(out) == [str(helper), "http://test-proxy.invalid:8080"]
    assert dict(os.environ) == before


def test_windows_git_bash_discovery_uses_git_install_instead_of_other_bash(tmp_path, monkeypatch):
    system = tmp_path / "System32"
    git = tmp_path / "Custom Git"
    for directory in (system, git / "bin", git / "cmd"):
        directory.mkdir(parents=True)
    (system / "bash.exe").touch()  # A WSL/other bash launcher is not Git Bash.
    (git / "bin/bash.exe").touch()
    (git / "bin/git.exe").touch()
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers, "_install_directories", lambda: [system, git / "cmd"])
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    environment = providers._agent_environment([sys.executable])
    assert environment["CLAUDE_CODE_GIT_BASH_PATH"] == str(git / "bin/bash.exe")


@pytest.mark.parametrize("binary_folder", ["codex", "bin"])
@pytest.mark.parametrize("layout", ["hoisted", "nested"])
def test_windows_codex_native_package_prefers_the_os_architecture(binary_folder, layout, tmp_path, monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers, "_install_directories", lambda: [tmp_path])
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    monkeypatch.delenv("JOB_FINDER_CODEX_BIN", raising=False)
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", "ARM64")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    binaries = []
    for arch, triple in (("x64", "x86_64-pc-windows-msvc"), ("arm64", "aarch64-pc-windows-msvc")):
        modules = tmp_path / "node_modules"
        if layout == "nested":
            modules = modules / "@openai/codex/node_modules"
        binary = modules / f"@openai/codex-win32-{arch}" / "vendor" / triple / binary_folder / "codex.exe"
        binary.parent.mkdir(parents=True)
        binary.touch()
        binaries.append(binary)
    assert providers._resolve_executable("codex") == [str(binaries[1])]


@pytest.mark.parametrize("layout", ["npm-bin", "npm-platform", "npm-platform-nested"])
def test_windows_claude_current_native_npm_install_is_discovered(layout, tmp_path, monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers, "_install_directories", lambda: [tmp_path])
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    monkeypatch.delenv("JOB_FINDER_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)
    suffix = {
        "npm-bin": "@anthropic-ai/claude-code/bin/claude.exe",
        "npm-platform": "@anthropic-ai/claude-code-win32-x64/claude.exe",
        "npm-platform-nested": "@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-win32-x64/claude.exe",
    }[layout]
    binary = tmp_path / "node_modules" / suffix
    binary.parent.mkdir(parents=True)
    binary.touch()
    for ext in ("", ".cmd", ".ps1"):
        (tmp_path / ("claude" + ext)).write_text("Do not execute a shell shim")
    assert providers._resolve_executable("claude") == [str(binary)]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_hidden_help_flags_do_not_prevent_real_attempt(provider, fake_cli, monkeypatch):
    executable, log = fake_cli
    monkeypatch.setenv("FAKE_OLD", "1")
    result = AgentRunner(provider, executable=str(executable), allow_tools=True).run(
        "Research", SCHEMA
    )
    assert result.data["company"] == "Example machine shop" and log.exists()


def test_inspect_never_runs_a_model(fake_cli):
    _, log = fake_cli
    providers = inspect_providers()
    assert [item["id"] for item in providers] == ["codex", "claude"]
    assert all(item["available"] and item["version"] == "0.163.0" for item in providers)
    assert not log.exists()


def test_missing_executable_is_actionable(monkeypatch):
    monkeypatch.setenv("JOB_FINDER_CODEX_BIN", "/definitely/not/installed")
    status = inspect_providers()[0]
    assert not status["available"]
    assert status["code"] == "not_installed"
    assert status["recovery"]["url"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize(
    "mode,code", [("auth", "auth_required"), ("rate", "rate_limited")]
)
def test_errors_are_classified_without_private_logs(
    provider, mode, code, fake_cli, monkeypatch
):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(ProviderError) as error:
        AgentRunner(provider, executable=str(executable), allow_tools=True).run(
            "Research", SCHEMA
        )
    assert error.value.code == code
    assert "sk-do-not-expose" not in str(error.value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize(
    "mode,code",
    [("malformed", "invalid_response"), ("missing", "missing_result"), ("array", None)],
)
def test_bad_results_rejected(provider, mode, code, fake_cli, monkeypatch):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(ProviderError) as error:
        AgentRunner(provider, executable=str(executable), allow_tools=True).run(
            "Research", SCHEMA
        )
    assert error.value.code == (
        code or ("invalid_response" if provider == "codex" else "missing_result")
    )


@pytest.mark.parametrize(
    "provider,code", [("codex", "auth_required"), ("claude", "agent_budget")]
)
def test_zero_exit_error_envelope_rejected(provider, code, fake_cli, monkeypatch):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", "event_error")
    with pytest.raises(ProviderError) as error:
        AgentRunner(provider, executable=str(executable), allow_tools=True).run(
            "Research", SCHEMA
        )
    assert error.value.code == code


def test_no_deadlock_when_stderr_exceeds_cap(fake_cli, monkeypatch):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", "flood")
    start = time.monotonic()
    with pytest.raises(ProviderError) as error:
        AgentRunner(
            "codex", executable=str(executable), max_output_bytes=1024, allow_tools=True
        ).run("Research", SCHEMA)
    assert error.value.code == "output_limit"
    assert time.monotonic() - start < 4


def test_result_file_also_has_a_size_cap(fake_cli, monkeypatch):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", "large_file")
    with pytest.raises(ProviderError) as error:
        AgentRunner(
            "codex", executable=str(executable), max_output_bytes=1024, allow_tools=True
        ).run("Research", SCHEMA)
    assert error.value.code == "output_limit"


def test_timeout_does_not_hang(fake_cli, monkeypatch):
    executable, _ = fake_cli
    monkeypatch.setenv("FAKE_MODE", "hang")
    start = time.monotonic()
    with pytest.raises(ProviderError) as error:
        AgentRunner(
            "codex", executable=str(executable), timeout=0.15, allow_tools=True
        ).run("Research", SCHEMA)
    assert error.value.code == "timeout"
    assert time.monotonic() - start < 4


def test_cancellation_before_launch_does_no_work(fake_cli):
    executable, log = fake_cli
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProviderError) as error:
        AgentRunner("codex", executable=str(executable), allow_tools=True).run(
            "Research", SCHEMA, cancel=cancel
        )
    assert error.value.code == "cancelled" and not log.exists()


def test_cancellation_terminates_child_processes(fake_cli, tmp_path, monkeypatch):
    executable, _ = fake_cli
    pid_path = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_MODE", "child")
    monkeypatch.setenv("FAKE_CHILD_PID", str(pid_path))
    cancel = threading.Event()

    def cancel_after_start():
        for _ in range(100):
            if pid_path.exists():
                time.sleep(0.1)
                cancel.set()
                return
            time.sleep(0.03)
        cancel.set()

    timer = threading.Thread(target=cancel_after_start, daemon=True)
    timer.start()
    try:
        with pytest.raises(ProviderError) as error:
            AgentRunner("codex", executable=str(executable), allow_tools=True).run(
                "Research", SCHEMA, cancel=cancel
            )
        assert error.value.code == "cancelled"
        assert pid_path.exists()
        child_pid = int(pid_path.read_text())
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x100000, False, child_pid)  # SYNCHRONIZE
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 2000) == 0, "Agent child survived cancellation"
                finally:
                    kernel.CloseHandle(handle)
            else:
                assert ctypes.get_last_error() == 87  # process no longer exists
            return
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        # A killed orphan can briefly remain a zombie until PID 1 reaps it.
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        ).stdout.strip()
        # ps works on both macOS and Linux. Empty means it was reaped between
        # kill(pid, 0) and this status check; Z means killed and awaiting reap.
        assert not status or status.startswith("Z"), "Agent child survived cancellation"
    finally:
        timer.join(timeout=4)
        if pid_path.exists():
            if os.name == "nt":
                subprocess.run(
                    [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", pid_path.read_text(), "/T", "/F"],
                    capture_output=True, timeout=5, check=False,
                )
            else:
                try:
                    os.kill(int(pid_path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_nonfinite_or_boolean_usage_is_not_reported():
    response = {
        "structured_output": {"company": "Fixture"},
        "usage": {"input_tokens": True, "output_tokens": -1},
        "total_cost_usd": float("inf"),
    }
    result = AgentRunner("claude", allow_tools=True)._parse_claude(
        json.dumps(response).encode()
    )
    assert (
        result.input_tokens is None
        and result.output_tokens is None
        and result.cost_usd is None
    )


def test_unknown_provider_rejected():
    with pytest.raises(ProviderError) as error:
        AgentRunner("invented", allow_tools=True)
    assert error.value.code == "unsupported_provider"


def test_no_command_wrapper_fallback(tmp_path, monkeypatch):
    wrapper = tmp_path / "claude.cmd"
    wrapper.write_text("echo unsafe")
    wrapper.chmod(0o700)
    monkeypatch.setenv("JOB_FINDER_CLAUDE_BIN", str(wrapper))
    status = inspect_providers()[1]
    assert status["code"] == "not_installed"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_consent_required_before_any_cli_process(provider, fake_cli):
    executable, log = fake_cli
    with pytest.raises(ProviderError) as error:
        AgentRunner(provider, executable=str(executable)).run("Private input", SCHEMA)
    assert error.value.code == "consent_required" and not log.exists()


def test_version_failure_is_advisory_and_never_a_model_probe(fake_cli, monkeypatch):
    _, log = fake_cli
    monkeypatch.setenv("FAKE_VERSION_FAIL", "1")
    statuses = inspect_providers()
    assert all(
        row["installed"] and row["available"] and row["path"] for row in statuses
    )
    assert all(row["version"] is None and row["advisory"] for row in statuses)
    assert not log.exists()


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("layout", ["native", "npm", "registry-native", "registry-npm"])
def test_stale_windows_path_discovers_and_uses_native_or_npm(
    provider, layout, tmp_path, monkeypatch
):
    home, appdata, programs = (
        tmp_path / name for name in ("Home Space", "Roaming Space", "Program Files")
    )
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("ProgramFiles", str(programs))
    monkeypatch.delenv(f"JOB_FINDER_{provider.upper()}_BIN", raising=False)
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    registry_root, registry_node = tmp_path / "Custom CLI Location", tmp_path / "Custom Node Location"
    queries = []
    if layout.startswith("registry-"):
        monkeypatch.setenv("JOB_FINDER_TEST_REGISTRY_DIR", str(registry_root))
        def query(key, name):
            queries.append((key, name))
            return ('"";"%JOB_FINDER_TEST_REGISTRY_DIR%";' if key[0] == "user" else str(registry_node)), 2
        monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
            HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="system",
            OpenKey=lambda hive, key: nullcontext((hive, key)), QueryValueEx=query,
        ))
    if layout.endswith("native"):
        executable = (registry_root if layout.startswith("registry-") else home / ".local/bin") / f"{provider}.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        expected = [str(executable)]
    else:
        prefix = registry_root if layout.startswith("registry-") else appdata / "npm"
        entry = prefix / "node_modules" / providers._NPM_ENTRIES[provider]
        entry.parent.mkdir(parents=True)
        entry.touch()
        for suffix in ("", ".cmd", ".ps1"):
            (prefix / f"{provider}{suffix}").write_text("THIS SHELL WRAPPER MUST NOT RUN")
        node = (registry_node if layout.startswith("registry-") else programs / "nodejs") / "node.exe"
        node.parent.mkdir(parents=True)
        node.touch()
        expected = [str(node), str(entry)]
    assert providers._resolve_executable(provider) == expected
    if layout.startswith("registry-"):
        assert (("user", "Environment"), "Path") in queries
        assert (("system", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"), "Path") in queries
        assert Path(".") not in providers._install_directories()
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if provider == "codex":
            kwargs["result_path"].write_text('{"company":"Fixture"}')
            return 0, b'{"type":"turn.completed"}', b""
        return 0, b'{"structured_output":{"company":"Fixture"}}', b""

    monkeypatch.setattr(providers, "_execute", execute)
    result = AgentRunner(provider, allow_tools=True).run(
        'Literal $(query) & "input"', SCHEMA
    )
    assert result.data == {"company": "Fixture"}
    assert calls[0][0][: len(expected)] == expected
    assert calls[0][1]["stdin"] == 'Literal $(query) & "input"'
    assert not any(arg.endswith((".cmd", ".bat")) for arg in calls[0][0])
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("selection", ["override", "path-native", "path-npm"])
def test_registry_fallback_never_replaces_override_or_current_path(provider, selection, tmp_path, monkeypatch):
    registry = tmp_path / "Registry Fallback"
    registry.mkdir()
    (registry / f"{provider}.exe").touch()
    preferred = tmp_path / "Preferred CLI"
    preferred.mkdir()
    executable = preferred / (provider + (".cmd" if selection == "path-npm" else ".exe"))
    executable.touch()
    expected = [str(executable)]
    if selection == "path-npm":
        entry = preferred / "node_modules" / providers._NPM_ENTRIES[provider]
        entry.parent.mkdir(parents=True)
        entry.touch()
        node = preferred / "node.exe"
        node.touch()
        expected = [str(node), str(entry)]
    monkeypatch.delenv(f"JOB_FINDER_{provider.upper()}_BIN", raising=False)
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers, "_install_directories", lambda: [registry])
    monkeypatch.setattr(providers.shutil, "which", lambda value: str(executable) if value in {provider, str(executable)} else None)
    assert providers._resolve_executable(provider, str(executable) if selection == "override" else None) == expected


def test_unavailable_windows_registry_does_not_block_discovery(monkeypatch):
    def denied(*args):
        raise PermissionError("unavailable registry")
    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="system", OpenKey=denied,
    ))
    assert providers._install_directories()


def test_npm_entry_without_node_has_precise_recovery(tmp_path, monkeypatch):
    entry = tmp_path / "node_modules/@anthropic-ai/claude-code/cli.js"
    entry.parent.mkdir(parents=True)
    entry.touch()
    monkeypatch.delenv("JOB_FINDER_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(providers, "_install_directories", lambda: [tmp_path])
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    status = inspect_providers()[1]
    assert (
        status["installed"]
        and not status["available"]
        and status["code"] == "node_missing"
    )
    assert status["recovery"]["url"] == "https://nodejs.org/en/download"


def test_wrapper_discovery_prefers_adjacent_native_executable(tmp_path, monkeypatch):
    wrapper, native = tmp_path / "claude.cmd", tmp_path / "claude.exe"
    wrapper.touch()
    native.touch()
    monkeypatch.setattr(
        providers.shutil,
        "which",
        lambda value: str(wrapper) if value == "claude" else None,
    )
    assert providers._resolve_executable("claude") == [str(native)]


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize(
    "mode,code",
    [("unsupported", "unsupported_cli"), ("permission", "permission_denied")],
)
def test_real_attempt_reports_actual_option_or_permission_failure(
    provider, mode, code, fake_cli, monkeypatch
):
    executable, log = fake_cli
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(ProviderError) as error:
        AgentRunner(provider, executable=str(executable), allow_tools=True).run(
            "Research", SCHEMA
        )
    assert log.exists() and error.value.code == code
    assert (
        "1401234" not in error.value.message
        and "authentication engineer" not in error.value.message
    )


@pytest.mark.parametrize(
    "output,code",
    [
        (
            {
                "is_error": True,
                "errors": ["Permission denied"],
                "tool_input": {"query": "authentication 401 quota"},
            },
            "permission_denied",
        ),
        ({"error": {"message": "Failed request id 1401234"}}, "provider_failed"),
        ({"error": {"status_code": 401}}, "auth_required"),
        ({"is_error": True, "result": "Invalid API key sk-private"}, "auth_required"),
        ({"errors": ["HTTP 403 Forbidden"]}, "permission_denied"),
        ({"errors": ["HTTP 429 rate limit"]}, "rate_limited"),
        ({"error": {"message": "Connection timed out"}}, "network_error"),
    ],
)
def test_diagnostics_ignore_tool_inputs_and_bound_http_codes(output, code):
    error = providers._provider_failure("claude", json.dumps(output).encode(), 1)
    assert error.code == code
    assert "sk-private" not in error.message


def test_os_permission_failure_is_distinct_and_sanitized(monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        raise PermissionError("private path or credentials")

    monkeypatch.setattr(providers.subprocess, "Popen", blocked)
    with pytest.raises(ProviderError) as error:
        providers._execute(["agent"], cwd=str(tmp_path), timeout=1, max_bytes=1024)
    assert error.value.code == "launch_permission_denied"
    assert "private" not in error.value.message


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_login_opens_only_fixed_resolved_command_in_windows_console(
    provider, monkeypatch
):
    command = ["C:/Program Files/node.exe", f"C:/User Space/{provider}/cli.js"]
    monkeypatch.setattr(providers, "_resolve_executable", lambda name: command.copy())
    monkeypatch.setattr(providers.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        providers.subprocess,
        "Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    assert providers.open_provider_login(provider) == {
        "opened": True,
        "provider": provider,
    }
    assert calls[0][0] == command + (
        ["login"] if provider == "codex" else ["auth", "login"]
    )
    assert calls[0][1]["shell"] is False and calls[0][1]["creationflags"]


def test_mac_login_quotes_paths_before_terminal_dispatch(monkeypatch):
    command = ['/tmp/Space "quote" $(touch sentinel)/claude']
    monkeypatch.setattr(
        providers, "_resolve_executable", lambda provider: command.copy()
    )
    monkeypatch.setattr(providers.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(
        providers.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    providers.open_provider_login("claude")
    escaped = (
        providers.shlex.join(command + ["auth", "login"])
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )
    assert calls[0][0] == [
        "/usr/bin/osascript",
        "-e",
        f'tell application "Terminal" to do script "{escaped}"',
    ]


def test_login_without_terminal_is_actionable_and_provider_input_is_validated(
    monkeypatch,
):
    monkeypatch.setattr(
        providers, "_resolve_executable", lambda provider: ["/usr/bin/claude"]
    )
    monkeypatch.setattr(providers.sys, "platform", "linux")
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    with pytest.raises(ProviderError, match="claude auth login") as error:
        providers.open_provider_login("claude")
    assert error.value.code == "login_unavailable"
    with pytest.raises(ProviderError):
        providers.recovery_for("claude; echo unsafe", "auth_required")


@pytest.mark.parametrize(
    "code",
    [
        "not_installed",
        "auth_required",
        "permission_denied",
        "launch_permission_denied",
        "unsupported_cli",
        "model_unavailable",
        "network_error",
        "rate_limited",
        "agent_budget",
        "provider_failed",
    ],
)
def test_recovery_has_concrete_steps_without_private_output(code):
    recovery = providers.recovery_for("claude", code)
    assert (
        recovery["title"]
        and recovery["steps"]
        and all(isinstance(step, str) for step in recovery["steps"])
    )


def test_codex_nested_model_error_has_targeted_private_recovery(tmp_path):
    detail = {
        "error": {
            "code": "model_not_found",
            "message": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
        }
    }
    event = {"type": "error", "message": json.dumps(detail)}
    with pytest.raises(ProviderError) as error:
        AgentRunner("codex")._parse_codex(json.dumps(event).encode(), tmp_path / "unused.json")
    assert error.value.code == "model_unavailable"
    assert "gpt-6.1-sol" not in error.value.message and "Inputs → Agent settings" in error.value.message
    recovery = providers.recovery_for("codex", error.value.code)
    assert recovery["title"] == "Choose a supported model" and "retry" in recovery["steps"][-1]


@pytest.mark.parametrize("result", [
    'API Error: 404 {"type":"error","error":{"type":"not_found_error","message":"model: private-model-name"}}',
    "Your account does not have access to the requested model private-model-name.",
])
def test_claude_unsupported_or_inaccessible_model_is_not_a_login_error(result):
    envelope = {"type": "result", "is_error": True, "result": result}
    with pytest.raises(ProviderError) as error:
        AgentRunner("claude")._parse_claude(json.dumps(envelope).encode())
    assert error.value.code == "model_unavailable"
    assert "private-model-name" not in error.value.message
