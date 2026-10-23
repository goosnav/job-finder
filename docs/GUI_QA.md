# GUI validation

The screenshots in [`screenshots/`](screenshots/) were captured from the running Python application using its actual API, SQLite workspace, and fictional demo pipeline. No API response mocks or static data substitutions were used for the screenshots.

## Environment

- Linux source application, launched through `job_finder.api.create_app` on loopback.
- Playwright with Chromium headless shell 134.0.6998.35.
- Desktop: 1440 × 1050 and 1440 × 1150.
- Mobile viewport: 390 × 844.
- Isolated temporary workspaces containing fictional résumés, companies, and contact information.

## Verified through the interface

| Workflow | Observed result |
| --- | --- |
| Create a search | Objective, name, and preferences persisted through the API. |
| Paste a résumé | Candidate text, name, and reply email saved and returned in the search. |
| Choose task models | An explicit Claude research route and free-text model persisted independently of the Codex default. |
| Enter multiple locations | Semicolon-separated places preserve commas within city/state names. `Los Angeles, CA; San Diego, CA` becomes two locations. |
| Run the full demo | Five fictional opportunities, including a distinct company lead, completed the five-stage pipeline. Three opportunities received three-email sequences. |
| Inspect fit | Ranking, seven component explanations, confidence, gaps, requirements, source references, and résumé quotes were visible in the review drawer. |
| Review outreach | All three messages opened, subject edits saved, approval persisted, and public email status stayed visible. |
| Track progress | Shortlist status, a follow-up date, and review notes persisted. |
| Filter opportunities | Company-lead filtering returned the one company lead; text search returned the matching employer. |
| Export research | A CSV downloaded through the browser with job-search and email-sequence columns. |
| Upload a document | A TXT résumé uploaded, appeared in the search, and could be removed through the interface. |
| Import opportunities | A CSV row created an opportunity with its original role and source links. |
| Import verification | A dated verifier result updated the matched email to `verified`, with the source retained. |
| Import application progress | Matching by listing URL updated the opportunity to `interview` and retained its follow-up date. |
| Mobile layout | Main page and drawer had no horizontal overflow at 390 pixels. |
| Mobile email editing | Draft textareas resized to show all content; measured client height equaled scroll height for all three messages. |
| Feedback inside dialogs | Save and approval notifications rendered inside the modal's top layer and could be dismissed. |

The successful browser checks recorded **zero uncaught JavaScript errors and zero failed HTTP responses**. `node --check app/static/app.js` passed after the final edits.

## Screenshots

- [`opportunities.png`](screenshots/opportunities.png): ranked opportunities with distinct role listings and company leads.
- [`opportunity-review.png`](screenshots/opportunity-review.png): fit components and supporting résumé evidence.
- [`outreach.png`](screenshots/outreach.png): three-message editor and approval controls.
- [`evidence.png`](screenshots/evidence.png): source dates, listing status, and research excerpts.
- [`search.png`](screenshots/search.png): objective and résumé setup.
- [`agent-routes.png`](screenshots/agent-routes.png): independent provider and model controls for each task.
- [`progress.png`](screenshots/progress.png): actual demo progress while the run was active.
- [`mobile.png`](screenshots/mobile.png): compact opportunity list at 390 pixels.
- [`mobile-outreach.png`](screenshots/mobile-outreach.png): mobile review drawer.

## Limits of this validation

This validates the source GUI and actual local API workflows. The fictional demo does not exercise live internet discovery or authenticated model calls. Provider selection was verified as persisted configuration; successful Codex or Claude sign-in is a separate runtime requirement. No email or application was sent. Headless viewport checks do not constitute native macOS, Windows, or Linux desktop acceptance; packaging validation is recorded separately.
