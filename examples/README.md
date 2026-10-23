# Import and configuration examples

All names, employers, contacts and URLs in these files are fictional.

- `search-settings.json` is a complete starting configuration. Add your actual résumé and candidate details; select installed CLI models in the routes. No agent runs when creating the search.
- `opportunities.csv` shows distinct role and company-lead rows. A role needs company, title and a posting or application URL. A company lead needs an official company website. Imported source/status claims are unverified until researched.
- `outcomes.csv` uses an opportunity ID from an export or the exact listing/application URL. `application_status` can be new, shortlisted, applied, contacted, replied, interview, offer, rejected, withdrawn, not_interested, or bounced. An ambiguous shared email cannot mark several jobs as applied.
- `verification.csv` requires the actual verifier and actual check date; replace the demonstration date. MX routing alone is not a verified mailbox. Invalid results exclude the email, not the application link.
- `suppressions.csv` excludes a mailbox or a whole employer. A skipped role does not create a global exclusion.

Each import reports accepted, duplicate/updated, and rejected/skipped counts. Inspect errors rather than assuming every row was accepted.
