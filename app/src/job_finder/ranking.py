"""Explainable matching. Scores describe evidence alignment, never hiring odds."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .models import Weights, canonical_url, domain_of

FACTORS = ("role", "skills", "seniority", "location", "compensation", "preferences", "timing")


def normalized(text: str) -> str:
    return " ".join(str(text).casefold().split())


def date_age(value: str) -> float | None:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return (datetime.now(UTC) - date).total_seconds() / 86400
    except (ValueError, TypeError, AttributeError):
        return None


def candidate_claims_supported(opportunity: dict, candidate_text: str) -> list[dict]:
    text = normalized(candidate_text)
    return [claim for claim in opportunity.get("candidate_claims", []) if len(normalized(claim.get("resume_quote", ""))) >= 8 and normalized(claim["resume_quote"]) in text]


def assess(search: dict, opportunity: dict, candidate_text: str) -> dict:
    preferences, candidate = search["preferences"], search["candidate"]
    weights = Weights.model_validate(search["settings"].get("weights", {})).model_dump()
    if not any(weights.values()):
        weights = Weights().model_dump()
    if preferences["salary_min"] is None:
        weights["compensation"] = 0
    if not preferences["locations"] and not candidate["location"] and not preferences["work_modes"]:
        weights["location"] = 0
    # Funding/press cannot dominate qualification, even with custom weights.
    other = sum(v for k, v in weights.items() if k != "timing")
    timing = min(5.0, 100 * weights["timing"] / max(1, other + weights["timing"]))
    if other == 0:
        weights = Weights().model_dump()
        other = sum(v for k, v in weights.items() if k != "timing")
        timing = 5.0
    normalized_weights = {k: (timing if k == "timing" else (100 - timing) * v / other) for k, v in weights.items()}
    components = {c["criterion"]: c for c in opportunity.get("match_components", [])}
    urls = set(opportunity.get("source_urls", [])) | {e["url"] for e in opportunity.get("evidence", [])} | {s["url"] for s in opportunity.get("signals", [])}
    urls.discard("")
    source_text = normalized(candidate_text)
    desired_text = normalized(search["objective"] + " " + " ".join(preferences["roles"]))
    breakdown, known, score = [], 0.0, 0.0
    questions, blockers = [], []
    for criterion in FACTORS:
        item = components.get(criterion, {})
        match = item.get("match", "unknown")
        reason = item.get("reason", "Not assessed yet.")
        if match != "unknown" and (not item.get("source_url") or item.get("source_url") not in urls):
            match, reason = "unknown", "No supporting job source for this assessment."
        quote = normalized(item.get("candidate_quote", ""))
        candidate_support = len(quote) >= 8 and (quote in source_text or (criterion == "role" and quote in desired_text))
        if criterion in {"role", "skills", "seniority"} and match in {"strong", "partial"} and not candidate_support:
            match, reason = "unknown", "Candidate evidence needs confirmation in the supplied résumé or profile."
        weight = normalized_weights[criterion]
        points = weight * {"strong": 1.0, "partial": 0.5, "gap": 0.0, "unknown": 0.0}[match]
        score += points
        known += weight if match != "unknown" else 0
        breakdown.append({"criterion": criterion, "weight": round(weight, 1), "match": match, "points": round(points, 1), "reason": reason, "source_url": item.get("source_url", ""), "candidate_quote": item.get("candidate_quote", "")})
    if opportunity.get("listing_status") == "closed":
        blockers.append("Opening is closed.")
    if deadline_passed(opportunity.get("valid_through", "")):
        blockers.append("Published application deadline has passed.")
    needs_review = False
    if preferences["strict_work_mode"] and preferences["work_modes"]:
        if opportunity["work_mode"] == "unknown":
            questions.append("Confirm the required work arrangement.")
            needs_review = True
        elif opportunity["work_mode"] not in preferences["work_modes"]:
            blockers.append("Work arrangement conflicts with your required mode.")
    if candidate["needs_sponsorship"] is True:
        if opportunity["sponsorship"] == "no":
            blockers.append("The posting does not offer the sponsorship you require.")
        elif opportunity["sponsorship"] == "unknown":
            questions.append("Confirm sponsorship before investing in this application.")
            needs_review = True
    if preferences["salary_min"] is not None:
        comparable = opportunity["salary_period"] == "year" and opportunity["salary_currency"].upper() == preferences["salary_currency"].upper()
        upper = opportunity.get("salary_max")
        if comparable and upper is not None and upper < preferences["salary_min"] and opportunity.get("salary_source"):
            blockers.append("Published maximum base pay is below your minimum.")
        elif not comparable or upper is None or not opportunity.get("salary_source"):
            questions.append("Confirm annual base pay in your requested currency; no currency or hourly conversion is assumed.")
            needs_review = True
        elif opportunity.get("salary_min") is not None and opportunity["salary_min"] < preferences["salary_min"]:
            questions.append("The salary range overlaps your minimum; confirm the likely offer.")
    hard = {"location": bool(preferences["locations"] or preferences["relocation"] == "no"), "work_mode": preferences["strict_work_mode"], "authorization": bool(candidate["work_authorization"]), "sponsorship": candidate["needs_sponsorship"] is True, "clearance": bool(candidate["clearance"]), "must_have": bool(preferences["must_have"] or preferences["exclude"])}
    for check in opportunity.get("requirement_checks", []):
        if check["status"] == "conflict" and check.get("source_url") and check.get("source_url") in urls and hard.get(check["criterion"]):
            blockers.append(check["reason"])
        elif check["status"] == "unknown" or check["status"] == "conflict":
            questions.append(check["reason"])
            needs_review |= hard.get(check["criterion"], False)
    if opportunity["work_mode"] == "remote" and not opportunity["remote_eligibility"]:
        questions.append("Remote location, residency, and time-zone eligibility are not established.")
        needs_review = True
    if opportunity.get("clearance_requirement") and not candidate["clearance"]:
        questions.append("Confirm clearance eligibility; no clearance was supplied in your profile.")
        needs_review = True
    age = date_age(opportunity.get("posted_at", ""))
    if age is not None and age < -1:
        questions.append("The posting date is in the future; verify the source.")
    elif age is not None and age > preferences["max_post_age_days"]:
        questions.append("Older posting: confirm that the role is still active.")
        needs_review = True
    elif age is None:
        questions.append("Original posting date is unknown; update and republish dates are shown separately.")
    if opportunity["kind"] == "company_lead":
        questions.append("Company lead: no specific vacancy is established.")
        needs_review = True
    elif opportunity["listing_status"] != "open":
        questions.append("Confirm that the opening is still accepting applications.")
        needs_review = True
    if not source_text or len(source_text) < 50:
        questions.append("Add résumé evidence to assess demonstrated experience.")
        needs_review = True
    if opportunity.get("research_status") != "complete":
        questions.append("Research has not completed.")
        needs_review = True
    eligibility = "blocked" if blockers else "review" if needs_review else "eligible"
    action = "skip" if blockers else "contact" if opportunity["kind"] == "company_lead" else "apply" if opportunity["listing_status"] == "open" else "research"
    if int(round(score)) < search["settings"]["min_fit"] and action not in {"skip", "research"}:
        action = "research"
    return {"fit_score": round(score), "confidence": round(known), "score_breakdown": breakdown, "eligibility": eligibility, "blockers": list(dict.fromkeys(blockers)), "questions": list(dict.fromkeys(questions)), "next_action": action}


def rank_key(opportunity: dict) -> tuple:
    inactive = opportunity.get("status") in {"rejected", "suppressed"} or opportunity.get("eligibility") == "blocked"
    acted = opportunity.get("application_status", "new") not in {"new", "shortlisted"}
    return (inactive, acted, -opportunity.get("fit_score", 0), opportunity.get("kind") != "open_role", -opportunity.get("confidence", 0), opportunity.get("company", "").casefold(), opportunity.get("title", "").casefold())


def deadline_passed(value: str) -> bool:
    # A date-only deadline includes the whole stated day; no employer timezone is inferred.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        try:
            return datetime.fromisoformat(value).date() < datetime.now(UTC).date()
        except ValueError:
            return False
    age = date_age(value)
    return age is not None and age > 0


def identity_of(opportunity: dict) -> str:
    if opportunity["kind"] == "company_lead":
        domain = domain_of(opportunity.get("company_url", ""))
        if domain and domain not in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}:
            return "company:" + domain
        provider, board = opportunity.get("source_provider"), opportunity.get("source_board", "")
        if provider in {"greenhouse", "lever", "lever_eu", "ashby"} and board:
            return f"company:ats:{provider}:{board.casefold()}"
        raise ValueError("A company lead needs its official company website or a known ATS board.")
    for value in (opportunity.get("listing_url", ""), opportunity.get("apply_url", "")):
        if not value:
            continue
        p = urlsplit(value)
        host = (p.hostname or "").casefold()
        segments = p.path.strip("/").split("/")
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
            match = re.search(r"/(?:jobs/)?([0-9]+)(?:/apply)?/?$", p.path)
            if match:
                region = "eu" if ".eu." in host else "global"
                return f"ats:greenhouse:{region}:" + match[1]
        if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"} and len(segments) >= 2:
            provider = "lever:eu" if host == "jobs.eu.lever.co" else "lever:global" if host == "jobs.lever.co" else "ashby"
            return f"ats:{provider}:{segments[0]}:{segments[1]}"
        return "role:" + canonical_url(value)
    raise ValueError("An open role needs a posting or application URL.")
