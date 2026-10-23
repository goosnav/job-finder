# Local HTTP contract

The application serves a single local user on `127.0.0.1`. This is not a hosted or multi-user API. All work is stored in the same local workspace used by the CLI.

Fetch `GET /api/session` from the same origin. Keep its token in memory and send it as `X-Session-Token` on every mutation. Cross-site requests and unexpected Host headers are rejected. Never put the token in a URL. CORS is not enabled. `GET /api/openapi.json` supplies the versioned FastAPI schema.

| Method | Path | Operation |
|---|---|---|
| GET | `/health/ready` | Readiness, app identity and version |
| GET | `/api/session` | Session token, local paths, CLI capability status |
| GET/POST | `/api/searches` | List/create searches; GET returns `{searches}` |
| GET/PATCH | `/api/searches/{id}` | Search, ranked opportunities, runs, counts, résumé documents |
| POST | `/api/searches/{id}/documents` | Multipart `file`: extract résumé/supporting text |
| DELETE | `/api/searches/{id}/documents/{document_id}` | Remove document and invalidate dependent research/drafts |
| POST | `/api/searches/{id}/import` | Multipart CSV/XLSX opportunity import |
| POST | `/api/searches/{id}/verification/import` | Multipart `file` plus optional verifier `source` |
| POST | `/api/searches/{id}/outcomes/import` | Multipart application/contact tracking CSV |
| POST | `/api/searches/{id}/runs` | `{ "stage": "pipeline" }` or an individual stage; returns `{run}` |
| GET | `/api/runs/{id}` | `{run, events}`: durable progress and recent events |
| POST | `/api/runs/{id}/pause` | Stop active calls and save state; returns `{run}` |
| POST | `/api/runs/{id}/resume` | Continue with retained counters and valid caches |
| POST | `/api/runs/{id}/cancel` | Stop without scheduling more work |
| PATCH | `/api/opportunities/{id}` | Edit contacts/drafts, review, verification, or application tracking |
| GET | `/api/searches/{id}/export?format=csv&scope=all` | CSV/XLSX; scopes `all`, `apply`, `outreach`, `shortlist` |
| GET/POST | `/api/suppressions` | Global email/domain exclusions |
| POST | `/api/suppressions/import` | Multipart exclusion CSV |
| POST | `/api/demo` | Create and run a fictional offline search; returns `{search, run}` |
| POST | `/api/quit` | Graceful local server and agent shutdown |

## Search inputs and task routing

A search has `name`, `objective`, `candidate`, `preferences`, `settings`, and
`demo`. Candidate fields include supplied résumé text, demonstrated skills,
location, experience, authorization, sponsorship needs, clearance, and links.
Preferences include target roles/locations/industries, work modes, annual salary
minimum and currency, employment types, seniority, must-haves, exclusions,
relocation, company-lead inclusion, and maximum posting age. Unknown candidate
facts should remain empty/null rather than being inferred.

Settings include `provider`, `model`, `routes`, `target_results`, `min_fit`,
`discovery_queries`, `max_agent_calls`, `max_minutes`, `concurrency`,
`agent_timeout_seconds`, `max_turns`, `max_cost_usd`, `research_pages`, `weights`,
and `ats_boards`. The OpenAPI schema gives exact enums and bounds. The CLI also
prints the full configuration schema with `job-finder schema search --json`.

Each route in `routes.profile`, `routes.discovery`, `routes.research`, and
`routes.outreach` accepts a `provider` (`inherit`, `codex`, or `claude`) and a
`model` string. Empty model values inherit the global model/default. Model names
are passed to the installed provider CLI rather than frozen into an app dropdown.

```json
{
  "name": "Manufacturing engineering",
  "objective": "Find aerospace manufacturing and automation roles near Dayton.",
  "candidate": {"skills": ["Python", "CNC", "Inspection tooling"]},
  "preferences": {"roles": ["Manufacturing Engineer"], "locations": ["Dayton, OH"]},
  "settings": {
    "provider": "codex",
    "target_results": 25,
    "routes": {"outreach": {"provider": "claude", "model": ""}}
  }
}
```

PATCH merges nested settings/candidate/preference objects. Edits that could race
an active run return `409`; pause first. Updates to candidate evidence,
preferences, or research routes invalidate dependent work. Changing global
provider between demo and real mode requires a separate search.

## Results, review, and tracking

`GET /api/searches/{id}` returns `search`, `opportunities`, `runs`, `counts`,
`documents`, and `outcomes`. Opportunities are ranked and decorated with `rank`,
`ready_to_apply`, `ready_to_contact`, `apply_blockers`, `export_blockers`, and
`prior_activity`. Each row retains its `kind` (`open_role` or `company_lead`),
fit/coverage assessment, eligibility, source evidence, explicit unknowns,
application URL, contact provenance, and draft sequence. Funding/news signals
do not establish an open role. See [source semantics](SOURCES.md).

Opportunity PATCH accepts review `status`, `review_notes`, edited `drafts`,
public contact fields, verification fields, `application_status`, `activity_at`,
and `next_follow_up`. Application status is one of `new`, `shortlisted`,
`applied`, `contacted`, `replied`, `interview`, `offer`, `rejected`, `withdrawn`,
`not_interested`, or `bounced`. Timestamps use ISO dates/times. Approval is a
local review action; the app does not send mail or apply on the user's behalf.

Opportunity CSV imports require a company and a source appropriate to the row:
specific roles need a title and listing/application URL; company leads need a
company website. Imported claims remain unverified until researched/checked.
Verifier imports use `email`, `status`, `verification_source` (or form `source`),
and `verification_at`. Tracking imports prefer `opportunity_id`/`id`, then a
listing/application URL; a shared mailbox alone cannot identify an application
to a specific role. Duplicate jobs merge by role identity rather than company.

Exports retain the full job-specific research fields. `all` includes incomplete
research; `apply` includes rows passing application checks; `outreach` includes
reviewed rows passing contact checks and deduplicates mailboxes; `shortlist`
includes rows the user marked shortlisted. An empty filtered export returns an
actionable `EXPORT-EMPTY` error. Fictional demo rows never become ready for live
application/outreach exports.

## Runs, budgets, and errors

Stages are `pipeline`, `profile`, `discover`, `research`, `verify`, and `draft`.
Run states are `queued`, `running`, `paused`, `completed`, `failed`,
`budget_exhausted`, and `cancelled`. `completed`/`total` describe the **current
stage**, not a global ETA. `stage_index`/`stage_count` show pipeline position.
Completed research, events, usage, and checkpoints persist after interruption.

`usage` records each reported task/provider/model and optional token/cost values.
`cost_usd` is reported cost, `claude_cost_usd` tracks the Claude-specific part,
and `unpriced_calls` identifies calls without exact cost data. Missing usage is
not zero spend. Dollar limits cannot promise a precise combined stop when the
provider CLI does not expose pricing; call, time, turn, page, and concurrency
limits still bound work. The UI/CLI presents these limits separately.

Errors use `{"error":{"code":"STABLE-CODE","message":"Actionable message"}}`.
Errors omit submitted résumé text, raw provider stderr, tokens, and credentials.
Uploads are bounded at 8 MB; extracted résumé text and document counts also have
limits. No endpoint sends messages, submits applications, or exports stored
provider credentials.
