# Security and data boundaries

Job Finder stores candidate material and search activity locally, and sends task inputs to the chosen Codex or Claude Code provider. The app has no Send or Apply workflow. Approved agent tools can change files and take external actions through the user's configured connections.

## Candidate data and credentials

The SQLite workspace contains extracted résumé text, explicit candidate facts, preferences, source evidence, contacts, outreach drafts, and application tracking. Uploaded originals are read in memory for extraction; the app stores the extracted text and a content hash. Deleting a document removes its stored document text and invalidates dependent research/drafts. Previously generated evidence or user-created exports can still contain material the user supplied.

Native launches use `JobFinder` under the operating system's application-data location. This is separate from Auto Outbound and from the extracted program folder. The app attempts restrictive directory/database permissions where the platform supports them. It does not implement database encryption; operating-system account security, disk encryption, and backup handling remain relevant to private résumé and job-search data. Source/CLI users can choose a location with `JOB_FINDER_DATA` or the CLI data option.

Provider authentication stays with the installed CLI. Job Finder does not add a provider-token form or copy an authentication database. The provider process runs as the current user and uses its normal authentication environment. Original candidate material is supplied to the profile task; relevant excerpts and the resulting brief support later research. Public search queries are built from job criteria, with prompts explicitly excluding private identity and résumé details. That prompt instruction is not a data-loss-prevention guarantee for a third-party model.

## Local HTTP boundary

The server binds to loopback. The API checks the Host and Origin headers, rejects cross-site fetches, and requires a random in-memory session token for mutations. Responses disable caching and add content-type, framing, referrer, and content-security policies. Validation errors identify the field to fix without echoing raw résumé inputs. Unexpected errors return a stable generic message.

This boundary is intended to resist websites making requests to a local app. It is not authentication against another process running as the same operating-system user. Do not expose the server through a public reverse proxy as though it were a hosted service; a hosted edition would need a different authentication and authorization design.

## Agent execution

`settings.agent_tools_approved` defaults to `false`. Before the first live task, the GUI requires **Allow & run** after it shows the access notice. Approval persists per search. CLI users grant it with `run --allow-agent-tools`; API clients must obtain equivalent consent before setting it to `true`. Revocation blocks future agent tasks.

Approved tasks inherit the user's CLI configuration, connected tools, and existing sign-in. Codex receives `--dangerously-bypass-approvals-and-sandbox`, with `web_search="live"` on research tasks. Claude receives `--dangerously-skip-permissions` and a session-only `sandbox.enabled=false` setting. Disabling Claude's optional shell sandbox also removes its per-domain sandbox network restrictions for this task. Other user settings remain loaded, and managed organization policy still applies. See the [Codex CLI reference](https://developers.openai.com/codex/cli/reference/) and [Claude sandbox documentation](https://code.claude.com/docs/en/sandboxing). Job Finder adds no tool allowlist, safe mode, or separate restriction on profile/draft tools. Separate CLI hook-trust decisions still apply.

The agent can use the internet, run commands, read or change files, and use connected tools under the user's account. Instructions in uploaded documents and webpages can influence those actions. Typed outputs, provenance checks, and human review help assess saved results; they do not confine an approved agent's actions.

The transport uses explicit argument arrays with `shell=False` and passes prompts through stdin. It starts in a temporary working directory. That directory is not a file-access boundary. Standard native and npm installations are detected automatically; npm transports invoke Node.js directly instead of a shell shim. Version probes are advisory. The actual task checks authentication, option support, and access.

On Windows, recovered installation and system tool directories are appended to the agent's child PATH. Proxy, certificate, authentication and provider environment settings are preserved. The parent process and system environment are unchanged. Current native Claude npm executables are launched directly; older JavaScript npm entry points still use Node.

Process input/output sizes, elapsed time, and cancellation are bounded. Provider failures produce stable error codes and recovery steps instead of raw stderr. The protected sign-in endpoint opens only the fixed `codex login` or `claude auth login` command in a native console.

## Public network access

The independent fetcher accepts public HTTP(S) URLs without embedded credentials. It rejects non-public resolved addresses, checks redirects again, and connects to a previously validated IP while preserving TLS hostname validation. Requests have byte, content-type, redirect, timeout, and cancellation limits. Direct ATS adapters additionally restrict requests and redirects to their documented API hosts.

Supported public ATS feeds do not require signing into a job board. Unsupported URLs and partial feed failures produce warnings and preserve valid records. These public-source adapters do not bypass authentication, paywalls, or access controls. Their network checks do not restrict approved agent tools.

## Evidence and contact integrity

Several distinctions are deliberate:

- A company funding signal is not a confirmed vacancy. Company leads and specific openings retain different kinds and export fields.
- An HTTP 200 response is not proof that a role is open. Verification checks the intended role and only applies matching structured metadata. Missing access remains unknown; a known removed listing or expired deadline closes it.
- Model output cannot assign trusted ATS adapter provenance. Imported and web-discovered records start with unverified authority.
- A résumé quote must appear in original candidate material before it can support a candidate claim. Generated profile prose cannot create a credential.
- A DNS mail record does not verify a mailbox. An unreadable email-source page leaves the address unknown unless a valid independent verification record exists.

These checks are evidence filters, not guarantees of factual correctness, contact deliverability, or hiring suitability. The UI retains source URLs, dates, uncertainty, and blockers for review. Approval and export guards are separate from the fit score.

Address suppression blocks outreach exports to that address while preserving a valid website application. Domain exclusion blocks the employer's action exports. Prior application/contact activity is retained so exports can identify duplicate outreach across searches. Outreach approval only changes the local review record; it does not start a send task.

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
