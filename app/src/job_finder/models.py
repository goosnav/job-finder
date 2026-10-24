"""Typed job-search inputs, evidence, and provider-independent results."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def public_url(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        value = "https://" + value
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username is not None or p.password is not None or len(value) > 2048:
        raise ValueError("Use a public http or https URL without credentials.")
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, p.query, ""))


def domain_of(value: str) -> str:
    try:
        return (urlsplit(public_url(value)).hostname or "").lower().removeprefix("www.").encode("idna").decode()
    except (ValueError, UnicodeError):
        return ""


def canonical_url(value: str) -> str:
    if not value:
        return ""
    p = urlsplit(public_url(value))
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "gh_src", "lever-source", "fbclid", "gclid"}]
    return urlunsplit(("https", p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def valid_urls(values: list[str]) -> list[str]:
    return list(dict.fromkeys(public_url(x) for x in values if x))


class Candidate(Model):
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    location: str = Field(default="", max_length=300)
    resume_text: str = Field(default="", max_length=60000)
    skills: list[str] = Field(default_factory=list, max_length=80)
    years_experience: float | None = Field(default=None, ge=0, le=70)
    work_authorization: str = Field(default="", max_length=1000)
    needs_sponsorship: bool | None = None
    clearance: str = Field(default="", max_length=500)
    links: list[str] = Field(default_factory=list, max_length=8)
    _links = field_validator("links")(valid_urls)


class Preferences(Model):
    roles: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=20)
    industries: list[str] = Field(default_factory=list, max_length=20)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=list)
    strict_work_mode: bool = False
    salary_min: float | None = Field(default=None, ge=0, le=10000000)
    salary_currency: str = Field(default="USD", max_length=10)
    employment_types: list[str] = Field(default_factory=list, max_length=8)
    seniority: str = Field(default="", max_length=500)
    must_have: str = Field(default="", max_length=3000)
    exclude: str = Field(default="", max_length=3000)
    relocation: Literal["yes", "no", "maybe", "unknown"] = "unknown"
    include_company_leads: bool = True
    max_post_age_days: int = Field(default=45, ge=1, le=365)


class Route(Model):
    provider: Literal["inherit", "codex", "claude"] = "inherit"
    model: str = Field(default="", max_length=120)


class ModelRoutes(Model):
    profile: Route = Field(default_factory=Route)
    discovery: Route = Field(default_factory=Route)
    research: Route = Field(default_factory=Route)
    outreach: Route = Field(default_factory=Route)


class Weights(Model):
    role: int = Field(default=20, ge=0, le=100)
    skills: int = Field(default=25, ge=0, le=100)
    seniority: int = Field(default=10, ge=0, le=100)
    location: int = Field(default=15, ge=0, le=100)
    compensation: int = Field(default=10, ge=0, le=100)
    preferences: int = Field(default=15, ge=0, le=100)
    timing: int = Field(default=5, ge=0, le=100)


class Settings(Model):
    provider: Literal["codex", "claude", "demo"] = "codex"
    agent_tools_approved: bool = False
    model: str = Field(default="", max_length=120)
    routes: ModelRoutes = Field(default_factory=ModelRoutes)
    target_results: int = Field(default=25, ge=1, le=1000)
    min_fit: int = Field(default=55, ge=0, le=100)
    discovery_queries: int = Field(default=6, ge=1, le=40)
    max_agent_calls: int = Field(default=100, ge=1, le=5000)
    max_minutes: int = Field(default=90, ge=1, le=1440)
    concurrency: int = Field(default=2, ge=1, le=4)
    agent_timeout_seconds: int = Field(default=180, ge=15, le=900)
    max_turns: int = Field(default=8, ge=2, le=40)
    max_cost_usd: float = Field(default=10, ge=0.1, le=1000)
    research_pages: int = Field(default=4, ge=1, le=10)
    weights: Weights = Field(default_factory=Weights)
    ats_boards: list[str] = Field(default_factory=list, max_length=20)
    _boards = field_validator("ats_boards")(valid_urls)


class SearchInput(Model):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(default="", max_length=6000)
    candidate: Candidate = Field(default_factory=Candidate)
    preferences: Preferences = Field(default_factory=Preferences)
    settings: Settings = Field(default_factory=Settings)
    demo: bool = False


class Evidence(Model):
    url: str = Field(max_length=2048)
    title: str = Field(default="", max_length=300)
    quote: str = Field(default="", max_length=600)
    field: str = Field(default="job", max_length=80)
    checked_at: str = ""
    _url = field_validator("url")(public_url)


class HiringSignal(Model):
    kind: Literal["funding", "contract", "expansion", "product", "team_growth", "job_posting", "leadership", "network", "other"]
    summary: str = Field(max_length=800)
    url: str = Field(max_length=2048)
    published_at: str = Field(default="", max_length=100)
    event_at: str = Field(default="", max_length=100)
    _url = field_validator("url")(public_url)


class OpportunitySeed(Model):
    kind: Literal["open_role", "company_lead"] = "open_role"
    company: str = Field(min_length=1, max_length=250)
    company_url: str = Field(default="", max_length=2048)
    title: str = Field(default="", max_length=300)
    listing_url: str = Field(default="", max_length=2048)
    apply_url: str = Field(default="", max_length=2048)
    careers_url: str = Field(default="", max_length=2048)
    requisition_id: str = Field(default="", max_length=200)
    source_provider: str = Field(default="web", max_length=80)
    source_board: str = Field(default="", max_length=200)
    source_id: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=1000)
    remote_eligibility: str = Field(default="", max_length=1500)
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    employment_type: str = Field(default="", max_length=200)
    seniority: str = Field(default="", max_length=200)
    department: str = Field(default="", max_length=200)
    industry: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=8000)
    requirements: list[str] = Field(default_factory=list, max_length=30)
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str = Field(default="", max_length=10)
    salary_period: Literal["year", "month", "hour", "unknown"] = "unknown"
    salary_source: str = Field(default="", max_length=2048)
    sponsorship: Literal["yes", "no", "unknown"] = "unknown"
    authorization_requirement: str = Field(default="", max_length=1000)
    clearance_requirement: str = Field(default="", max_length=1000)
    posted_at: str = Field(default="", max_length=100)
    source_updated_at: str = Field(default="", max_length=100)
    date_basis: str = Field(default="", max_length=300)
    valid_through: str = Field(default="", max_length=100)
    listing_status: Literal["open", "closed", "unverified"] = "unverified"
    listing_source: Literal["primary", "ats", "aggregator", "news", "import", "unknown"] = "unknown"
    last_checked_at: str = Field(default="", max_length=100)
    contact_name: str = Field(default="", max_length=200)
    contact_role: str = Field(default="", max_length=200)
    contact_source: str = Field(default="", max_length=2048)
    email: str = Field(default="", max_length=254)
    email_source: str = Field(default="", max_length=2048)
    linkedin_url: str = Field(default="", max_length=2048)
    source_urls: list[str] = Field(default_factory=list, max_length=25)
    signals: list[HiringSignal] = Field(default_factory=list, max_length=8)
    _urls = field_validator("company_url", "listing_url", "apply_url", "careers_url", "salary_source", "contact_source", "email_source", "linkedin_url")(public_url)
    _sources = field_validator("source_urls")(valid_urls)


class MatchComponent(Model):
    criterion: Literal["role", "skills", "seniority", "location", "compensation", "preferences", "timing"]
    match: Literal["strong", "partial", "gap", "unknown"]
    reason: str = Field(max_length=1000)
    source_url: str = Field(default="", max_length=2048)
    candidate_quote: str = Field(default="", max_length=700)
    _url = field_validator("source_url")(public_url)


class RequirementCheck(Model):
    criterion: Literal["location", "work_mode", "salary", "authorization", "sponsorship", "clearance", "must_have", "experience"]
    status: Literal["met", "conflict", "unknown"]
    reason: str = Field(max_length=1000)
    source_url: str = Field(default="", max_length=2048)
    _url = field_validator("source_url")(public_url)


class CandidateClaim(Model):
    text: str = Field(max_length=500)
    resume_quote: str = Field(max_length=700)


class DiscoveryResult(Model):
    opportunities: list[OpportunitySeed] = Field(max_length=20)
    coverage_notes: str = Field(max_length=2000)


class ResearchResult(OpportunitySeed):
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    match_components: list[MatchComponent] = Field(max_length=7)
    requirement_checks: list[RequirementCheck] = Field(max_length=12)
    candidate_claims: list[CandidateClaim] = Field(max_length=12)
    skill_gaps: list[str] = Field(max_length=15)
    risks: list[str] = Field(max_length=15)
    interview_hooks: list[str] = Field(max_length=8)
    application_tips: list[str] = Field(max_length=8)
    research_summary: str = Field(max_length=4000)
    outreach_angle: str = Field(max_length=1500)


class Draft(Model):
    step: int = Field(ge=1, le=3)
    delay_days: int = Field(ge=0, le=60)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=3000)
    source_urls: list[str] = Field(default_factory=list, max_length=12)
    candidate_claims: list[str] = Field(default_factory=list, max_length=8)
    angle: str = Field(default="", max_length=500)
    _urls = field_validator("source_urls")(valid_urls)


class SequenceResult(Model):
    drafts: list[Draft] = Field(min_length=3, max_length=3)


class ProfileBrief(Model):
    summary: str = Field(min_length=1, max_length=6000)
    suggested_roles: list[str] = Field(max_length=15)
    search_terms: list[str] = Field(max_length=25)
    strengths: list[str] = Field(max_length=15)
    gaps: list[str] = Field(max_length=15)
    unknowns: list[str] = Field(max_length=15)


class Opportunity(ResearchResult):
    evidence: list[Evidence] = Field(default_factory=list)
    match_components: list[MatchComponent] = Field(default_factory=list)
    requirement_checks: list[RequirementCheck] = Field(default_factory=list)
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)
    skill_gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    interview_hooks: list[str] = Field(default_factory=list)
    application_tips: list[str] = Field(default_factory=list)
    research_summary: str = ""
    outreach_angle: str = ""
    id: str
    search_id: str
    identity: str
    domain: str = ""
    created_at: str
    updated_at: str
    fit_score: int = 0
    confidence: int = 0
    score_breakdown: list[dict] = Field(default_factory=list)
    eligibility: Literal["eligible", "review", "blocked"] = "review"
    blockers: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    next_action: Literal["apply", "contact", "research", "skip"] = "research"
    research_status: Literal["pending", "complete", "error"] = "pending"
    research_fingerprint: str = ""
    draft_fingerprint: str = ""
    source_check: Literal["unverified", "matched", "reachable", "unavailable", "closed"] = "unverified"
    email_status: Literal["missing", "published", "mx_found", "no_mx", "unknown", "invalid", "verified", "suppressed", "catch_all", "risky"] = "missing"
    email_checked_at: str = ""
    verification_source: str = ""
    verification_at: str = ""
    status: Literal["new", "researched", "drafted", "approved", "rejected", "suppressed"] = "new"
    drafts: list[Draft] = Field(default_factory=list)
    qa_issues: list[str] = Field(default_factory=list)
    review_notes: str = ""
    application_status: Literal["new", "shortlisted", "applied", "contacted", "replied", "interview", "offer", "rejected", "withdrawn", "not_interested", "bounced"] = "new"
    activity_at: str = ""
    next_follow_up: str = ""


class RunRequest(Model):
    stage: Literal["report", "pipeline", "profile", "discover", "research", "verify", "draft"] = "pipeline"


class SuppressionInput(Model):
    kind: Literal["email", "domain"]
    value: str = Field(min_length=1, max_length=254)
    reason: str = Field(default="Do not contact", max_length=500)


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    def walk(obj):
        if isinstance(obj, dict):
            obj.pop("default", None)
            if obj.get("type") == "object":
                obj["additionalProperties"] = False
                obj["required"] = list(obj.get("properties", {}))
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(schema)
    return schema


def route_for(settings: dict, task: str) -> dict[str, str]:
    if settings["provider"] == "demo":
        return {"provider": "demo", "model": ""}
    route = settings.get("routes", {}).get(task, {})
    provider = route.get("provider", "inherit")
    resolved = settings["provider"] if provider == "inherit" else provider
    model = route.get("model") or (settings.get("model", "") if resolved == settings["provider"] else "")
    return {"provider": resolved, "model": model}
