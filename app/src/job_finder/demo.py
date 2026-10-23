"""Clearly fictional offline fixtures, isolated from actionable exports."""
from datetime import UTC, datetime, timedelta

from .models import OpportunitySeed, now

RESUME = """Alex Example — fictional demonstration profile.
Operations engineer with four years of experience improving manufacturing workflows.
Built Python tools that organize work orders and flag missing production details.
Used SQL and spreadsheets to track delivery performance and investigate bottlenecks.
Worked with engineering, purchasing, and shop-floor teams to introduce process changes.
Based in Los Angeles. Open to local hybrid roles and remote roles available in California.
"""
COMPANIES = ["Northline Robotics", "Arcwell Systems", "Mesa Industrial", "Vector Flight", "Harbor Instruments"]
TITLES = ["Operations Engineer", "Business Systems Engineer", "Operations & Automation", "Senior Flight Software Engineer", "Manufacturing Coordinator"]


def candidate() -> dict:
    return {"name": "Alex Example", "email": "alex@example.invalid", "location": "Los Angeles, CA", "resume_text": RESUME, "skills": ["Python", "SQL", "Process improvement"], "years_experience": 4, "needs_sponsorship": None, "links": []}


def profile_result(search: dict) -> dict:
    return {"summary": "Fictional operations engineer with practical Python and SQL experience and four years improving manufacturing workflows.", "suggested_roles": ["Operations Engineer", "Business Systems Engineer", "Automation Engineer"], "search_terms": ["operations engineering Python manufacturing", "business systems process automation"], "strengths": ["Workflow automation", "Cross-functional implementation", "Production data analysis"], "gaps": ["No flight software or control-system experience supplied"], "unknowns": ["Work authorization", "Sponsorship needs", "Degree requirements"]}


def discovery_result(limit: int, existing: list) -> dict:
    opportunities = []
    known = str(existing)
    for index, company in enumerate(COMPANIES):
        url = "https://" + company.split()[0].lower() + ".example"
        kind = "company_lead" if index == 2 else "open_role"
        listing = "" if kind == "company_lead" else url + "/careers/" + str(101 + index)
        if listing and listing in known or not listing and url in known:
            continue
        opportunities.append({"kind": kind, "company": company + " · Demo", "company_url": url, "title": TITLES[index], "listing_url": listing, "apply_url": listing + "/apply" if listing else "", "careers_url": url + "/careers", "requisition_id": str(101 + index) if listing else "", "location": "Los Angeles, CA" if index != 1 else "Remote · California", "remote_eligibility": "US residents in California (fictional)" if index == 1 else "", "work_mode": "remote" if index == 1 else "hybrid", "employment_type": "Full-time", "industry": "Engineering & manufacturing", "seniority": "Senior" if index == 3 else "Mid-level", "description": "FICTIONAL DEMO. " + ["Build tools for production planning and operations.", "Connect engineering and operations data with Python and SQL.", "Recently announced a fictional Series B and a new production site. No vacancy is confirmed.", "Develop safety-critical embedded flight software in C++.", "Coordinate production scheduling and purchase orders."][index], "requirements": ["Python and SQL", "Manufacturing operations"] if index < 3 else ["Embedded C++ and flight controls"] if index == 3 else ["Production scheduling"], "salary_min": [120000, 110000, None, 150000, 70000][index], "salary_max": [155000, 145000, None, 190000, 90000][index], "salary_currency": "USD" if index != 2 else "", "salary_period": "year" if index != 2 else "unknown", "salary_source": listing, "posted_at": (datetime.now(UTC) - timedelta(days=3 + index * 2)).isoformat() if listing else "", "date_basis": "Fictional posting date" if listing else "No vacancy", "listing_status": "open" if listing else "unverified", "listing_source": "primary" if listing else "news", "contact_name": ["Jordan", "Casey", "Taylor", "Morgan", "Jamie"][index] + " Example", "contact_role": "Talent partner · fictional", "contact_source": url + "/team", "email": "careers@" + company.split()[0].lower() + ".example", "email_source": url + "/contact", "source_urls": [url + "/careers", url + "/contact", listing or url + "/news/series-b"], "signals": [{"kind": "funding", "summary": "Fictional Series B and factory expansion; hiring needs are unconfirmed.", "url": url + "/news/series-b", "published_at": (datetime.now(UTC) - timedelta(days=6)).isoformat(), "event_at": ""}] if index == 2 else []})
    return {"opportunities": opportunities[:limit], "coverage_notes": "Five fictional opportunities. No real companies, vacancies, contacts or web research."}


def research_result(opportunity: dict) -> dict:
    base = {key: opportunity[key] for key in OpportunitySeed.model_fields}
    index = next((i for i, name in enumerate(COMPANIES) if name in opportunity["company"]), 0)
    url = opportunity["listing_url"] or opportunity["company_url"] + "/news/series-b"
    quote = "Built Python tools that organize work orders and flag missing production details."
    matches = [
        ["strong", "strong", "strong", "strong", "strong", "strong", "strong"],
        ["partial", "strong", "strong", "strong", "strong", "strong", "strong"],
        ["partial", "strong", "partial", "strong", "unknown", "strong", "strong"],
        ["gap", "gap", "gap", "strong", "strong", "partial", "strong"],
        ["partial", "partial", "strong", "strong", "gap", "partial", "strong"],
    ][index]
    reasons = ["Relevant work, supported by the fictional role description.", "Python and SQL work appears in the supplied demo résumé.", "Seniority compared with the four years explicitly supplied.", "Local hybrid or California-eligible remote work.", "Published fictional base range compared with the candidate minimum.", "Engineering operations match the stated objective.", "Recent fictional posting or expansion signal."]
    base.update(evidence=[{"url": url, "title": "Fictional role or company page", "quote": base["description"], "field": "job", "checked_at": now()}, {"url": base["email_source"], "title": "Fictional contact page", "quote": base["email"], "field": "email", "checked_at": now()}], match_components=[{"criterion": criterion, "match": match, "reason": reason, "source_url": url, "candidate_quote": quote} for criterion, match, reason in zip(("role", "skills", "seniority", "location", "compensation", "preferences", "timing"), matches, reasons, strict=True)], requirement_checks=[{"criterion": "location", "status": "met", "reason": "The fictional arrangement fits Los Angeles.", "source_url": url}], candidate_claims=[{"text": "I build Python tools for manufacturing workflows.", "resume_quote": quote}, {"text": "I use SQL to investigate production bottlenecks.", "resume_quote": "Used SQL and spreadsheets to track delivery performance and investigate bottlenecks."}], skill_gaps=["No embedded flight software experience supplied."] if index == 3 else [], risks=["FICTIONAL DEMO. Do not apply or contact.", "Confirm all requirements on a real job search."], interview_hooks=["Ask how operations data flows between engineering and production."], application_tips=["Highlight a relevant workflow project and the part you personally delivered."], research_summary="Fictional demonstration of source-backed matching. " + base["description"], outreach_angle="Ask whether practical operations automation experience fits the team's needs.")
    return base


def sequence_result(opportunity: dict) -> dict:
    greeting = opportunity["contact_name"].split()[0] if opportunity["contact_name"] else "there"
    url = opportunity["listing_url"] or opportunity["company_url"] + "/news/series-b"
    intro = f"Your {opportunity['title']} posting mentions production workflows." if opportunity["kind"] == "open_role" else "Your expansion announcement caught my attention. I do not know whether your team is hiring."
    return {"drafts": [
        {"step": 1, "delay_days": 0, "subject": "Operations engineering fit", "body": f"Hi {greeting},\n\n{intro} I build Python tools for manufacturing workflows.\n\nWould that experience fit any of your team's current needs?", "source_urls": [url], "candidate_claims": ["I build Python tools for manufacturing workflows."], "angle": "Connect a sourced role or signal to supported experience."},
        {"step": 2, "delay_days": 5, "subject": "Production data experience", "body": f"Hi {greeting},\n\nOne other relevant detail: I use SQL to investigate production bottlenecks. I enjoy connecting that analysis to a practical change for the team.\n\nWould a brief example of that work help you assess fit?", "source_urls": [url], "candidate_claims": ["I use SQL to investigate production bottlenecks."], "angle": "Add a different supported strength."},
        {"step": 3, "delay_days": 12, "subject": "Close the loop", "body": f"Hi {greeting},\n\nI'll leave this here. If the timing or fit is off, I understand.\n\nIs there a better person to ask about operations engineering needs? No reply is needed if there is no match.", "source_urls": [url], "candidate_claims": [], "angle": "Close politely without implying a vacancy or relationship."},
    ]}
