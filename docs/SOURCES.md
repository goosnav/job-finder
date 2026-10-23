# Discovery sources and evidence rules

Job Finder combines direct public ATS imports with bounded Codex/Claude research.
An employer feed supplies postings; agent research decides whether they fit the
candidate and locates supporting public evidence. A successful fetch does not
promise complete internet coverage, current employment eligibility, or a reply.

## Public board imports

Add hosted board URLs to `settings.ats_boards` in a search configuration. Board
identifiers come from a real employer's careers link; the app does not guess an
API host from arbitrary input. The imports are read-only and need no ATS key.

| Input example | Public GET endpoint constructed | Important source semantics |
| --- | --- | --- |
| `https://boards.greenhouse.io/acme` or `https://job-boards.greenhouse.io/acme` | `https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true` | `id` identifies the public posting; `internal_job_id: null` means a prospect post. `updated_at` is not an original posting date. |
| `https://jobs.lever.co/acme` | `https://api.lever.co/v0/postings/acme?mode=json&skip=0&limit=100` | Published postings; `skip` and `limit` paginate. The public contract does not establish an original posting date. |
| `https://jobs.eu.lever.co/acme` | `https://api.eu.lever.co/v0/postings/acme?mode=json&skip=0&limit=100` | EU instance; identity uses `source_board: eu/acme` so it cannot collide with the global board. |
| `https://jobs.ashbyhq.com/acme` | `https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true` | Exclude `isListed: false`. `publishedAt` is the most recent publication, not the first publication. Salary, equity, and bonus are distinct components. |

Official API references, checked during implementation:

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever public Postings API](https://github.com/lever/postings-api)
- [Ashby public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)

`sources.fetch_board(url, limit=100, cancel=None)` returns a dictionary:

```python
{
    "opportunities": [...],  # OpportunitySeed-compatible dictionaries
    "warnings": [...],       # Partial failure, invalid data, cancellation, limits
    "requests": 1,           # Feed fetch operations attempted
    "complete": True,        # Returned board exhausted without invalid/lost data
}
```

The configured limit is 1–1,000 results. Greenhouse and Ashby each use one feed
request. Lever uses at most five pages of at most 100 records. A board has a
60-second total budget; each transport has a 20-second deadline and at most four
HTTP attempts including redirects. Each response is limited to 8 MB. The
`requests` field counts feed fetch operations, not every redirect. Partial work
survives cancellation, an invalid record, or a later page failure. `complete:
false` and the accompanying warning are meaningful; an interrupted board must
not be used to conclude that a company has no other jobs.

Endpoint hosts are fixed by the selected ATS. Redirects must stay on the exact
allowed API host and use HTTPS. The shared transport rejects private, loopback,
link-local, mixed-public/private DNS, credential-bearing URLs, and nonstandard
ports, then pins the validated address while retaining TLS host verification.
It sends no cookies, credentials, or application data to the ATS. Rate-limit
responses end the bounded import with a warning instead of retrying indefinitely.

Every normalized row retains `source_provider`, `source_board`, `source_id`,
listing/application/careers URLs, source URLs, and the check timestamp. If the
feed omits the employer's display name, the observed board identifier is used
until research confirms the company name; no employer website is invented.
Feed content may be truncated to the model's 8,000-character description limit;
the original source remains linked.

## Opening status and hiring signals

Specific published roles from the direct feeds are current feed evidence of an
opening at the check time. A past explicit application deadline overrides that
status. Greenhouse prospect posts and titles such as “General Application” or
“Talent Community” become `company_lead` records with `listing_status:
unverified`. Their application link is not presented as a confirmed vacancy.
Title checks are conservative heuristics, so the individual role still needs
review before an application.

Agent discovery should search multiple useful lenses rather than repeat one
keyword query:

1. Exact target titles and close functional equivalents on employer/ATS pages.
2. Candidate skills, tools, industry knowledge, and adjacent role families.
3. Desired geography, commuting locations, remote eligibility, and work mode.
4. Employer and investor announcements of funding, contracts, expansion, products,
   or new teams, followed by a check of the employer's actual careers page.
5. Relevant startup directories, such as [YC's public job listings](https://www.ycombinator.com/jobs),
   and public industry associations or specialist boards, followed by primary
   source confirmation.
6. Smaller companies with a plausible need for the candidate's work, retained as
   explicitly speculative company leads when no matching vacancy is found.

Funding, a contract award, or a new office can justify investigating a company.
None proves an available role, available headcount, ability to sponsor, salary,
or a hiring manager's intent. Store the source URL, publication date, event date
when stated, and the limited conclusion supported by that evidence. A later news
republication must not make an old event appear new. Search failures and access
blocks remain unknown rather than becoming negative evidence.

Public contacts require the exact address and its public source. Do not construct
an email address from a person's name or company naming pattern. A company's
general contact is different from a verified hiring contact. DNS routing checks
do not establish mailbox existence, willingness to receive outreach, or delivery.

## Career-page inspection and freshness

`sources.inspect_listing(url, cancel=None)` performs a bounded safe read and
returns the final URL, title, visible text, selected JobPosting metadata, status
hints, check time, request count, and warnings. It parses bounded JSON-LD blocks
without running scripts. Metadata remains source-provided data requiring review.

Official structured-data references:

- [Schema.org JobPosting](https://schema.org/JobPosting)
- [Google JobPosting structured-data guidance](https://developers.google.com/search/docs/appearance/structured-data/job-posting)

The inspector preserves `datePosted`, `validThrough`, employer identity, explicit
location eligibility, employment type, and actual `baseSalary` where present.
It never substitutes `estimatedSalary` for employer-stated pay. Remote work does
not imply worldwide eligibility. `applicantLocationRequirements` is geographical
eligibility, not proof of work authorization or sponsorship.

HTTP 404/410 is evidence that the listing URL is no longer available; the app
does not infer why it disappeared. HTTP 403, 429, timeouts, and parse failures
leave status unverified. Expired `validThrough` metadata is a closed-status hint.
An HTTP 200 response or generic “we are hiring” page does not by itself confirm
a particular opening. A page containing several different JobPosting objects
must be matched to the intended requisition before applying its facts to a row.

Original posting date, source update/publication time, hiring-event date, and
last-checked time are separate fields. Unknown dates remain empty. In particular,
the importer does not label Greenhouse updates or Ashby republishing as a new
vacancy. Recheck the employer page shortly before applying; listings can close
after the most recent run.

## Ranking and outreach safeguards

- Deduplicate by ATS provider/board/posting ID or canonical requisition URL,
  not company domain. One employer can have many distinct openings.
- Keep role fit separate from evidence confidence and eligibility. A high fit
  score is a prioritization heuristic, not a probability of an interview or offer.
- Keep salary unknown when not published; retain currency and year/month/hour
  units. Do not annualize hourly pay using an assumed schedule or compare unlike
  currencies. Multiple Ashby salary summaries stay in source text instead of
  being merged into a misleading single range.
- Show explicit conflicts and unresolved questions about location, sponsorship,
  authorization, clearance, required experience, pay, and candidate must-haves.
  A missing requirement is not evidence that the candidate satisfies it.
- Base candidate claims on the résumé or explicit candidate input. Public company
  evidence can support personalization; it cannot create candidate experience.
- Draft short, role-specific outreach with a clear question and source-backed
  reason to contact the employer. Company leads need exploratory language rather
  than claims that an advertised role exists. Human review precedes sending.

## Verification limits

Live smoke attempted on 2026-10-23: one active public board per provider was
located through primary ATS listings, then checked through the actual adapter
with `limit=3`. Greenhouse, Lever, and Ashby each returned zero rows and the
warning `Source did not resolve to a public address` after approximately eight
seconds. This environment's public-DNS check prevented reading any live feed;
live vendor-payload compatibility remains unverified. No transport protections
were bypassed, no model/account calls were made, and no real job data was added
to the repository.

Fixture tests cover provider contracts, pagination and partial results, prospect
classification, salary/date semantics, identity, JSON-LD, and transport safety.
They are not a claim of exhaustive ATS coverage, permanent vendor compatibility,
or relevance quality across live job markets. Authenticated model research and
end-to-end ranking quality need real-world evaluation with the user's objectives.
