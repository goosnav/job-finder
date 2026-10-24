# GUI validation

The browser smoke check uses the running app, local API, and SQLite workspace. Its inputs contain fictional candidate and employer details.

## Run the check

```sh
uv run --project app python scripts/smoke_gui.py
uv run --project app python scripts/smoke_agent_recovery.py
```

The script starts a temporary loopback server and removes its test workspace afterward. The default check makes no model calls. Screenshots go to `dev/qa/`.

Use an existing isolated server with `--url http://127.0.0.1:PORT`. This mode writes test searches to that server's workspace.

Add `--run-agent` to make one real profile call with the synthetic résumé. This requires a signed-in provider and uses its account limits.

`smoke_agent_recovery.py` runs on macOS or Linux. It creates local executable CLI fixtures and exercises both provider adapters through the browser. Sign-in window requests are intercepted; no real login or model call occurs. Use `--output DIRECTORY` to retain screenshots.

## Current checks

| Workflow | Check |
| --- | --- |
| Production startup | A saved demo selection does not reopen; no sample button or demo search appears in the picker. |
| Résumé intake | A TXT résumé alone creates a search and retains location, salary, and work arrangement. |
| Goals-only intake | Roles and a description create a search without a résumé. |
| Invalid résumé | A malformed PDF shows an error, retains the inputs, and creates no partial search. |
| Repeated submission | A slow intake accepts one request and creates one search; both buttons disable until it finishes and recover after an invalid upload. |
| Supporting documents | A mixed upload shows the accepted document and the failed file; removal persists. |
| Search plan | With `--run-agent`, a real profile produces suggested roles and shows the saved constraints. |
| Job review | Imported jobs open in the drawer; notes persist. |
| Shortlist | Saved jobs appear in Shortlist; CSV contains only the saved job. |
| Job access | The job link opens its application URL in a new tab. The test intercepts its fictional destination. |
| Exports | CSV retains job links and notes; Excel contains Report, Opportunities, Sources, and Read me sheets. |
| Keyboard | A checkbox retains focus after selection; Tab continues through the interface. |
| Layout | Intake and results have no page overflow at 320, 390, 768, and desktop widths. |
| Reopen | A reload restores saved jobs. |
| Agent consent | Cancel starts no task; Allow & run persists approval for that search. |
| Agent recovery | Both provider adapters retain specific sign-in and permission failures without private output. |
| Retry | Check again refreshes detection; retry preserves inputs and reaches Plan; switching agents also reaches Plan. |
| Completion | A stale running poll followed by a completed search snapshot releases task controls and permits revoking access. |
| Quit | The header control works by keyboard at 320 pixels, warns about unsaved changes, and stops the actual server. |

The script also fails on uncaught JavaScript errors and unexpected HTTP errors. Expected résumé validation failures are allowed.

## Verification status

The production GUI check and both-provider consent/recovery check passed. These include duplicate-submission prevention, preserved inputs, retry, and actual shutdown. An independent repeat of the main workflow also passed.

The unit suite passed 273 tests with one existing warning. Ruff passed.

Claude Code 2.1.304 completed a real web task using existing sign-in and reported USD 0.0277. Codex 0.144.1 reached the provider but returned `model_unavailable` for both the inherited and explicitly tested models. The account's model access limits current live Codex use. The GUI offers **Choose model** or the other installed agent. No successful live Codex task is verified for this adapter revision.

The Windows CI suite checks direct PowerShell under RemoteSigned and the CMD opener. It also runs both npm transports through Node with shell shims present and developer tools absent from PATH. See the [current CI results](https://github.com/goosnav/job-finder/actions/workflows/test.yml). This automated check is not full native-image acceptance.

## Screenshots

- [`agent-consent.png`](screenshots/agent-consent.png): explicit tool-access approval.
- [`agent-recovery.png`](screenshots/agent-recovery.png): persistent sign-in recovery with direct actions.

- [`intake.png`](screenshots/intake.png): résumé, targets, location, salary, work arrangement, and provider.
- [`search-plan.png`](screenshots/search-plan.png): suggested roles and candidate assessment.
- [`opportunities.png`](screenshots/opportunities.png): jobs and direct listing actions.
- [`opportunity-review.png`](screenshots/opportunity-review.png): job review drawer.
- [`search.png`](screenshots/search.png): saved inputs and document controls.
- [`agent-routes.png`](screenshots/agent-routes.png): task provider and model controls.
- [`mobile.png`](screenshots/mobile.png): results at 390 pixels.

## Limits

The default browser check imports fictional jobs; it does not test live discovery or outreach quality. The recorded fixture tests send no messages or applications. Approved live agents can take external actions through configured tools. Headless browser checks do not establish native acceptance of macOS, Windows, or Linux launchers. M1a remains ACTIVE until the full native matrix passes.
