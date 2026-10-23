# Security and data boundaries

Job Finder is a local research and draft-preparation app. It stores candidate material and search activity on the user's computer, and sends selected task inputs to the chosen Codex or Claude Code provider when a live task runs. It does not send emails, submit applications, create job-board accounts, or run a hosted multi-user service.

## Candidate data and credentials

The SQLite workspace contains extracted résumé text, explicit candidate facts, preferences, source evidence, contacts, outreach drafts, and application tracking. Uploaded originals are read in memory for extraction; the app stores the extracted text and a content hash. Deleting a document removes its stored document text and invalidates dependent research/drafts. Previously generated evidence or user-created exports can still contain material the user supplied.

Native launches use `JobFinder` under the operating system's application-data location. This is separate from Auto Outbound and from the extracted program folder. The app attempts restrictive directory/database permissions where the platform supports them. It does not implement database encryption; operating-system account security, disk encryption, and backup handling remain relevant to private résumé and job-search data. Source/CLI users can choose a location with `JOB_FINDER_DATA` or the CLI data option.

Provider authentication stays with the installed CLI. Job Finder does not add a provider-token form or copy an authentication database. The provider process still runs as the current user and uses its normal authentication environment. Original candidate material is supplied to the offline profile task; bounded relevant excerpts and the resulting brief support later research. Public search queries are built from job criteria, with prompts explicitly excluding private identity and résumé details. That prompt instruction is not a data-loss-prevention guarantee for a third-party model.

## Local HTTP boundary

The server binds to loopback. The API checks the Host and Origin headers, rejects cross-site fetches, and requires a random in-memory session token for mutations. Responses disable caching and add content-type, framing, referrer, and content-security policies. Validation errors identify the field to fix without echoing raw résumé inputs. Unexpected errors return a stable generic message.

This boundary is intended to resist websites making requests to a local app. It is not authentication against another process running as the same operating-system user. Do not expose the server through a public reverse proxy as though it were a hosted service; a hosted edition would need a different authentication and authorization design.

## Agent execution

The adapters use explicit argument arrays with `shell=False` and an isolated temporary working directory. Résumé text, prompts, model names, and schema content never become shell commands. Windows batch wrappers are rejected in favor of native executables. Capability probes reject CLIs that lack the required isolation or structured-output options.

Codex runs with a read-only sandbox, ephemeral state, a strict configuration, and shell/apps/plugins/hooks/memory features disabled. Claude uses safe mode, no session persistence, an empty strict MCP configuration, and an explicit tool allowlist. Web research permits web tools; profile and drafting calls disable web access. Process input/output sizes, elapsed time, and cancellation are bounded. Raw provider errors are mapped to safe application error codes rather than returned wholesale to the browser.

These controls reduce the available actions; they do not prove that a model will interpret every adversarial webpage correctly. Retrieved pages, uploaded documents, and model responses are treated as untrusted input. Typed contracts, provenance checks, independent source checks, and human review provide additional layers. The app has no send/apply adapter for injected content to invoke.

## Public network access

The independent fetcher accepts public HTTP(S) URLs without embedded credentials. It rejects non-public resolved addresses, checks redirects again, and connects to a previously validated IP while preserving TLS hostname validation. Requests have byte, content-type, redirect, timeout, and cancellation limits. Direct ATS adapters additionally restrict requests and redirects to their documented API hosts.

Supported public ATS feeds do not require signing into a job board. Unsupported URLs and partial feed failures produce warnings and preserve any valid records already read. The system does not bypass authentication, paywalls, or access controls.

## Evidence and contact integrity

Several distinctions are deliberate:

- A company funding signal is not a confirmed vacancy. Company leads and specific openings retain different kinds and export fields.
- An HTTP 200 response is not proof that a role is open. Verification checks the intended role and only applies matching structured metadata. Missing access remains unknown; a known removed listing or expired deadline closes it.
- Model output cannot assign trusted ATS adapter provenance. Imported and web-discovered records start with unverified authority.
- A résumé quote must appear in original candidate material before it can support a candidate claim. Generated profile prose cannot create a credential.
- A DNS mail record does not verify a mailbox. An unreadable email-source page leaves the address unknown unless a valid independent verification record exists.

These checks are evidence filters, not guarantees of factual correctness, contact deliverability, or hiring suitability. The UI retains source URLs, dates, uncertainty, and blockers for review. Approval and export guards are separate from the fit score.

Address suppression blocks outreach to that address while preserving a valid website application. Domain exclusion blocks the employer's actions. Prior application/contact activity is retained so exports can identify duplicate outreach across searches. No message is sent by an approval action.

## Files and exports

Uploads have an 8 MB per-file limit. Candidate material is limited to four documents and 60,000 extracted/pasted characters in total. PDF extraction rejects encrypted documents and excessive page counts; scanned PDFs need OCR before import. Office archives have expansion checks, and spreadsheet import limits rows and columns. File names are reduced to a local name instead of becoming filesystem paths.

CSV/XLSX exports neutralize spreadsheet formula prefixes. Workbook evidence and search-context sheets preserve the reasons behind the ranked rows. Exported files contain private candidate and job-search information; copying them to another product is a separate user action outside this app's storage boundary.

## Budgets, cancellation, and recovery

Every live provider attempt counts toward the run's call allowance. Elapsed time, per-call timeouts, concurrency, and response size bound work. Claude's allowance is reserved transactionally across its calls, and a failed or interrupted call with unreported cost conservatively retains its reservation. Codex does not expose the same enforceable dollar control through this integration, so the app reports available usage without presenting a false total-cost cap.

Worker ownership tokens and SQLite transactions prevent duplicate claims. Pause, cancel, and Quit preserve completed work. A paused or interrupted run can resume; a cancelled run cannot silently restart. A recovered worker's unreported allowance remains visible.

## Native launcher boundary and verification status

The Go launcher uses an OS lock, an unguessable setup capability, same-origin retry checks, pinned tool checksums, an explicit release whitelist, and a fingerprinted external runtime. A failed dependency update preserves the previous ready runtime. A browser-open failure writes the exact local URL to `logs/OPEN_THIS_URL.txt`.

The current native images are unsigned. Cross-compilation and a Linux headless-browser smoke do not establish native desktop acceptance on macOS, Windows, or ARM Linux. Signing, notarization, the full clean-target matrix, and all native failure/recovery scenarios remain outstanding. See [Install and launch](INSTALL.md).

Report reproducible defects with the relevant version and error code. Remove résumé text, contact lists, tokens, and private provider output from material posted to a public issue.
