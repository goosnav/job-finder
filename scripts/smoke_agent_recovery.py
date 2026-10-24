#!/usr/bin/env python3
"""Check agent consent, recovery and Quit with local CLI fixtures; no live calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from smoke_gui import workspace

PROFILE = {
    "summary": "Fictional engineer suited to product design and test engineering.",
    "suggested_roles": ["Product Engineer", "Test Engineer"],
    "search_terms": ["product engineer Los Angeles"],
    "strengths": ["CAD and test fixture design"],
    "gaps": ["Salary evidence needs confirmation"],
    "unknowns": ["Preferred industry"],
}
PRIVATE_TOKEN = "sk-private-fixture-token-9370"
PRIVATE_QUERY = "authentication-engineer-private-query-9370"
RESUME = b"Fictional engineer: CAD, prototype design and test fixtures."
FAKE_CLI = r"""
import json
import sys
from pathlib import Path

root = Path(__file__).parent
provider = Path(__file__).name
args = sys.argv[1:]
if "--version" in args or "--help" in args:
    with (root / "probes.jsonl").open("a") as log:
        log.write(json.dumps({"provider": provider, "args": args}) + "\n")
    print("fixture-cli 1.2.3")
    raise SystemExit(0)

sys.stdin.read()
mode = (root / (provider + ".mode")).read_text().strip()
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"provider": provider, "mode": mode, "args": args}) + "\n")
if mode == "auth":
    print("Authentication required: sk-private-fixture-token-9370", file=sys.stderr)
    raise SystemExit(1)
if mode == "permission":
    print(json.dumps({"is_error": True, "errors": ["WebSearch permission denied"],
        "permission_denials": [{"tool_name": "WebSearch", "tool_input": {
            "query": "authentication-engineer-private-query-9370"}}]}))
    raise SystemExit(1)
if mode == "model":
    print(json.dumps({"error": {"type": "model_not_found", "message":
        "The model fixture-unavailable does not exist for this account."}}), file=sys.stderr)
    raise SystemExit(1)
profile = json.loads((root / "profile.json").read_text())
usage = {"input_tokens": 20, "output_tokens": 30}
if provider == "codex":
    Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(profile))
    print(json.dumps({"type": "turn.completed", "usage": usage}))
else:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
        "structured_output": profile, "usage": usage, "total_cost_usd": 0.001}))
"""


def records(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def detail(page, url):
    search_id = page.locator("#active-search").input_value()
    response = page.request.get(f"{url}/api/searches/{search_id}")
    assert response.ok, response.text()
    return response.json()


def assert_private_absent(page, saved):
    text = page.locator("body").inner_text() + json.dumps(saved)
    assert PRIVATE_TOKEN not in text, (
        "Raw provider token reached the UI or saved result"
    )
    assert PRIVATE_QUERY not in text, (
        "Private tool query reached the UI or saved result"
    )


def screenshot(page, output, name):
    if output:
        page.screenshot(path=str(output / name), full_page=True)


def create_intake(page, provider):
    if not page.locator("#intake-resume").count():
        page.get_by_role("button", name="New search", exact=False).click()
    page.locator("#intake-resume").set_input_files(
        {"name": "fictional-resume.txt", "mimeType": "text/plain", "buffer": RESUME}
    )
    page.get_by_label("Target roles", exact=True).fill("Product Engineer")
    page.get_by_label("Description", exact=True).fill(
        "Fictional goals: hands-on design."
    )
    page.get_by_label("Locations", exact=True).fill("Los Angeles, CA")
    page.get_by_label("Minimum annual salary", exact=True).fill("120000")
    page.get_by_label("hybrid", exact=True).check()
    page.get_by_label("Agent", exact=True).select_option(provider)
    page.get_by_role("button", name="Build search plan", exact=True).click()
    expect(
        page.get_by_role("dialog", name="Allow agent tools", exact=True)
    ).to_be_visible()


def assert_inputs(saved, provider):
    search = saved["search"]
    assert search["preferences"]["locations"] == ["Los Angeles, CA"]
    assert search["preferences"]["salary_min"] == 120000
    assert search["preferences"]["work_modes"] == ["hybrid"]
    assert search["objective"] == "Fictional goals: hands-on design."
    assert search["settings"]["provider"] == provider
    assert len(saved["documents"]) == 1


def wait_failure(page, url, provider, code):
    region = page.locator("#provider-recovery")
    expect(region).to_be_visible(timeout=20000)
    expect(region).to_contain_text(
        {
            "auth_required": "Sign in to your agent",
            "permission_denied": "The agent or a tool denied access",
            "model_unavailable": "Choose a supported model",
        }[code],
        timeout=20000,
    )
    expect(region.locator("ol li").first).to_be_visible()
    saved = detail(page, url)
    run = saved["runs"][0]
    assert run["status"] == "failed", run
    assert run["error_provider"] == provider, run
    assert run["error_code"] == code, run
    assert run["recovery"]["steps"], run
    assert_inputs(saved, provider)
    assert_private_absent(page, saved)
    assert_private_absent(page, page.request.get(f"{url}/api/runs/{run['id']}").json())
    return region


def exercise_provider(page, url, fixture, provider, output, login_requests):
    calls = fixture / "calls.jsonl"
    mode = fixture / (provider + ".mode")
    mode.write_text("auth")
    before = len(records(calls))
    create_intake(page, provider)
    assert len(records(calls)) == before, "Agent ran before consent"
    if provider == "codex":
        page.get_by_role("dialog", name="Allow agent tools").get_by_role(
            "button", name="Cancel", exact=True
        ).click()
        saved = detail(page, url)
        assert not saved["search"]["settings"]["agent_tools_approved"]
        assert not saved["runs"] and len(records(calls)) == before
        assert_inputs(saved, provider)
        page.get_by_role("button", name="Build search plan", exact=True).click()
        expect(page.get_by_role("dialog", name="Allow agent tools")).to_be_visible()
    screenshot(page, output, provider + "-consent.png")
    page.get_by_role("button", name="Allow & run", exact=True).click()
    recovery = wait_failure(page, url, provider, "auth_required")
    assert detail(page, url)["search"]["settings"]["agent_tools_approved"]
    assert len(records(calls)) == before + 1, (
        "Actual fixture invocation was not isolated"
    )
    name = "Codex" if provider == "codex" else "Claude Code"
    recovery.get_by_role("button", name="Sign in to " + name, exact=True).click()
    expect(
        page.locator(".toast").filter(has_text="Fixture sign-in window")
    ).to_be_visible()
    assert login_requests[-1] == provider and len(records(calls)) == before + 1
    screenshot(page, output, provider + "-auth.png")

    # Refresh detection through the real endpoint, with the other executable absent.
    other = "claude" if provider == "codex" else "codex"
    executable = fixture / other
    parked = fixture / (other + ".parked")
    executable.rename(parked)
    try:
        page.get_by_role("button", name="Setup & help", exact=True).click()
        help_dialog = page.locator("#small-dialog")
        card = help_dialog.locator(".provider-card").filter(
            has=page.get_by_text(
                "Claude Code" if other == "claude" else "Codex", exact=True
            )
        )
        expect(card.locator(".badge")).to_have_text("Not found")
    finally:
        parked.rename(executable)
    probes = len(records(fixture / "probes.jsonl"))
    help_dialog.get_by_role("button", name="Check again", exact=True).click()
    expect(card.locator(".badge")).to_have_text("Found")
    assert len(records(fixture / "probes.jsonl")) > probes
    help_dialog.get_by_role("button", name="Got it", exact=True).click()

    mode.write_text("permission")
    page.locator("#provider-recovery").get_by_role("button", name="Retry task").click()
    recovery = wait_failure(page, url, provider, "permission_denied")
    assert not recovery.get_by_role("button", name="Sign in", exact=False).count()
    expect(recovery).to_contain_text("permission", ignore_case=True)
    page.reload()
    recovery = wait_failure(page, url, provider, "permission_denied")
    page.set_viewport_size({"width": 320, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, output, provider + "-permission-320.png")

    if provider == "codex":
        mode.write_text("model")
        recovery.get_by_role("button", name="Retry task", exact=True).click()
        recovery = wait_failure(page, url, provider, "model_unavailable")
        assert not recovery.get_by_role("button", name="Sign in", exact=False).count()
        choose = recovery.get_by_role("button", name="Choose model", exact=True)
        choose.focus()
        page.keyboard.press("Enter")
        expect(page.locator(".agent-settings")).to_have_attribute("open", "")
        model = page.get_by_label("Default model", exact=True)
        expect(model).to_be_visible()
        expect(model).to_be_focused()
        screenshot(page, output, "model-settings-320.png")
        model.fill("fixture-supported-model")

    mode.write_text("success")
    recovery.get_by_role("button", name="Retry task", exact=True).click()
    expect(page.locator(".search-plan")).to_be_visible(timeout=20000)
    expect(page.locator(".search-plan")).to_contain_text(PROFILE["summary"])
    expect(page.locator("#provider-recovery")).to_be_hidden()
    assert not page.get_by_role("dialog", name="Allow agent tools").is_visible()
    saved = detail(page, url)
    assert_inputs(saved, provider)
    assert saved["search"]["settings"]["agent_tools_approved"]
    assert saved["runs"][0]["status"] == "completed"
    assert saved["search"]["profile"] == PROFILE
    assert_private_absent(page, saved)
    assert len(records(calls)) == before + (4 if provider == "codex" else 3)
    if provider == "codex":
        args = records(calls)[-1]["args"]
        assert args[args.index("--model") + 1] == "fixture-supported-model"
        assert saved["search"]["settings"]["model"] == "fixture-supported-model"
    screenshot(page, output, provider + "-recovered-plan-320.png")

    # Revoking a saved approval must require the modal again, including cached work.
    before_revoke = len(records(calls))
    runs_before_revoke = len(saved["runs"])
    page.get_by_role("button", name="Inputs", exact=True).click()
    page.locator(".agent-settings > summary").click()
    assert not page.locator('input[name="settings.agent_tools_approved"]').count()
    page.get_by_role("button", name="Revoke access", exact=True).click()
    expect(
        page.get_by_text("Tool access is approved when you start a task.")
    ).to_have_count(1)
    assert not detail(page, url)["search"]["settings"]["agent_tools_approved"]
    page.get_by_role("button", name="Build search plan", exact=True).click()
    consent = page.get_by_role("dialog", name="Allow agent tools", exact=True)
    expect(consent).to_be_visible()
    assert len(records(calls)) == before_revoke
    consent.get_by_role("button", name="Cancel", exact=True).click()
    page.reload()
    expect(page.locator("#active-search")).to_have_value(saved["search"]["id"])
    saved = detail(page, url)
    assert not saved["search"]["settings"]["agent_tools_approved"]
    assert len(saved["runs"]) == runs_before_revoke
    assert len(records(calls)) == before_revoke
    assert_inputs(saved, provider)
    page.set_viewport_size({"width": 1440, "height": 1000})


def run(output):
    with tempfile.TemporaryDirectory(
        prefix="job-finder-recovery-fixtures-"
    ) as directory:
        fixture = Path(directory)
        (fixture / "profile.json").write_text(json.dumps(PROFILE))
        for provider in ("codex", "claude"):
            executable = fixture / provider
            executable.write_text(f"#!{sys.executable}\n" + FAKE_CLI)
            executable.chmod(0o700)
            (fixture / (provider + ".mode")).write_text("auth")
        environment = {
            "JOB_FINDER_CODEX_BIN": str(fixture / "codex"),
            "JOB_FINDER_CLAUDE_BIN": str(fixture / "claude"),
        }
        with (
            patch.dict(os.environ, environment),
            workspace(None) as url,
            sync_playwright() as p,
        ):
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors, login_requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "response",
                lambda response: (
                    errors.append(f"{response.status} {response.url}")
                    if response.status >= 400
                    else None
                ),
            )

            def fixture_login(route):
                assert route.request.method == "POST"
                login_requests.append(route.request.url.rsplit("/", 2)[1])
                route.fulfill(
                    json={"message": "Fixture sign-in window; no real login opened."}
                )

            page.route("**/api/providers/*/login", fixture_login)
            page.goto(url)
            expect(page.locator("#intake-resume")).to_be_visible()
            assert not records(fixture / "calls.jsonl"), (
                "Provider discovery called a model"
            )
            for provider in ("codex", "claude"):
                exercise_provider(page, url, fixture, provider, output, login_requests)

            # Switching agents must run the saved task through the other real adapter.
            (fixture / "codex.mode").write_text("auth")
            create_intake(page, "codex")
            page.get_by_role("button", name="Allow & run", exact=True).click()
            recovery = wait_failure(page, url, "codex", "auth_required")
            recovery.get_by_role("button", name="Use Claude Code", exact=True).click()
            expect(page.locator(".search-plan")).to_be_visible(timeout=20000)
            assert_inputs(detail(page, url), "claude")
            assert records(fixture / "calls.jsonl")[-1]["provider"] == "claude"

            # The power control must work by keyboard at 320px and preserve Cancel.
            page.get_by_role("button", name="New search", exact=False).click()
            page.set_viewport_size({"width": 320, "height": 800})
            page.get_by_label("Description", exact=True).fill(
                "Fictional unsaved quit test."
            )
            page.evaluate("scrollTo(0,0)")
            quit_button = page.locator(".topbar").get_by_role(
                "button", name="Quit Job Finder"
            )
            expect(quit_button).to_be_visible()
            box = quit_button.bounding_box()
            assert box and box["y"] < 100 and box["x"] + box["width"] <= 320
            warnings = []

            def cancel_quit(dialog):
                warnings.append(dialog.message)
                dialog.dismiss()

            page.once("dialog", cancel_quit)
            quit_button.focus()
            page.keyboard.press("Enter")
            assert warnings and "unsaved" in warnings[-1].lower(), warnings
            expect(page.get_by_label("Description", exact=True)).to_have_value(
                "Fictional unsaved quit test."
            )
            screenshot(page, output, "quit-320.png")
            page.once("dialog", lambda dialog: dialog.accept())
            quit_button.focus()
            page.keyboard.press("Enter")
            expect(
                page.get_by_role("heading", name="Job Finder is closed.")
            ).to_be_visible()
            for _ in range(30):
                try:
                    urllib.request.urlopen(url + "/health/ready", timeout=0.3).close()
                except (OSError, urllib.error.URLError):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Quit did not stop the actual local server")
            assert not errors, errors
            browser.close()
    print(
        "PASS: both real fixture adapters; consent Cancel/Allow/persistence; auth and permission "
        "recovery; model choice; revoke/cancel; no private output; Check again; intercepted sign-in; retry to Plan; "
        "agent switch; keyboard Quit at 320px; dirty warning and actual shutdown. No live calls."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional screenshot directory")
    args = parser.parse_args()
    if os.name == "nt":
        parser.error(
            "The CLI fixtures require POSIX executable scripts; use macOS or Linux."
        )
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    run(args.output)


if __name__ == "__main__":
    main()
