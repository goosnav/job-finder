# GUI validation

The browser smoke check uses the running app, local API, and SQLite workspace. Its inputs contain fictional candidate and employer details.

## Run the check

```sh
uv run --project app python scripts/smoke_gui.py
```

The script starts a temporary loopback server and removes its test workspace afterward. The default check makes no model calls. Screenshots go to `dev/qa/`.

Use an existing isolated server with `--url http://127.0.0.1:PORT`. This mode writes test searches to that server's workspace.

Add `--run-agent` to make one real profile call with the synthetic résumé. This requires a signed-in provider and uses its account limits.

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

The script also fails on uncaught JavaScript errors and unexpected HTTP errors. Expected résumé validation failures are allowed.

## Verification status

The full production browser check passed, including the duplicate-submission regression. An independent repeat of the main workflow also passed.

A live browser plan check also passed with the synthetic résumé. A real Codex profile completed in one call. A separate one-job report completed in two calls and produced CSV and Excel exports. These checks verify the provider path; they do not measure recommendation quality across real résumés.

## Screenshots

- [`intake.png`](screenshots/intake.png): résumé, targets, location, salary, work arrangement, and provider.
- [`search-plan.png`](screenshots/search-plan.png): suggested roles and candidate assessment.
- [`opportunities.png`](screenshots/opportunities.png): jobs and direct listing actions.
- [`opportunity-review.png`](screenshots/opportunity-review.png): job review drawer.
- [`search.png`](screenshots/search.png): saved inputs and document controls.
- [`agent-routes.png`](screenshots/agent-routes.png): task provider and model controls.
- [`mobile.png`](screenshots/mobile.png): results at 390 pixels.

## Limits

The default browser check imports fictional jobs; it does not test live discovery or outreach quality. No messages or applications are sent. Headless browser checks do not establish native acceptance of macOS, Windows, or Linux launchers. M1a remains ACTIVE until the full native matrix passes.
