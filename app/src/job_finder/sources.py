"""Bounded imports from public ATS feeds and conservative listing inspection.

Feed fields are evidence, not an assessment of the candidate. In particular,
remote work, publication dates, salary, and talent pools retain their source
meaning. No authenticated ATS API or application submission is used here.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as daytime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from .models import OpportunitySeed, now, public_url
from .research import (
    SourceUnavailable,
    TextParser,
    fetch_public_document,
    fetch_public_json,
)

MAX_RESULTS = 1000
MAX_LEVER_PAGES = 5
BOARD_TIMEOUT_SECONDS = 60.0
POOL_TITLE = re.compile(
    r"\b(?:general applications?|open applications?|talent (?:community|pool|network)|"
    r"future (?:opportunities|openings)|spontaneous applications?|unsolicited applications?|"
    r"expressions? of interest|register your interest|candidate pool)\b|"
    r"don['’]?t see (?:a |the |your )?(?:right |matching |perfect )?(?:role|job)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Board:
    provider: str
    slug: str
    careers_url: str
    endpoint: str


def parse_board(url: str) -> Board:
    """Accept a hosted board URL, never an arbitrary user-supplied API endpoint."""
    try:
        normalized = public_url(url)
        parts = urlsplit(normalized)
        if parts.port not in {None, 443 if parts.scheme == "https" else 80}:
            raise ValueError("Unsupported board port")
        host = parts.hostname or ""
        path = parts.path.strip("/").split("/")
        slug = path[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", slug):
            raise ValueError("Missing or invalid board name")
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            return Board(
                "greenhouse", slug, f"https://{host}/{slug}",
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            )
        if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
            api_host = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
            return Board("lever", slug, f"https://{host}/{slug}", f"https://{api_host}/v0/postings/{slug}")
        if host == "jobs.ashbyhq.com":
            return Board(
                "ashby", slug, f"https://{host}/{slug}",
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
            )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Use a public Greenhouse, Lever, or Ashby board URL.") from exc
    raise ValueError("Use a public Greenhouse, Lever, or Ashby board URL.")


def _string(value, limit: int = 1000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _plain(value, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    # Greenhouse can return escaped HTML inside JSON. Decode before removing
    # markup so escaped script/style content is not mistaken for visible text.
    parser = TextParser()
    parser.feed(unescape(unescape(value)))
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return text if len(text) <= limit else text[:limit - 28] + " [Truncated; see source URL.]"


def _url(value) -> str:
    if not isinstance(value, str) or not re.match(r"^https?://", value, re.IGNORECASE):
        return ""
    try:
        url = public_url(value)
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port not in {None, 80 if parts.scheme == "http" else 443}:
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid")):
                return ""
        return url
    except (ValueError, UnicodeError):
        return ""


def _identifier(value) -> str:
    return str(value)[:200] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _names(values, limit: int = 1000) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(dict.fromkeys(_string(v.get("name")) for v in values if isinstance(v, dict) and _string(v.get("name"))))[:limit]


def _date(value) -> str:
    """Preserve a source date only if it is parseable; never invent a timezone."""
    value = _string(value, 100)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return ""


def _expired(value: str) -> bool:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            date = datetime.combine(date.date(), daytime.max)
        return date.replace(tzinfo=date.tzinfo or UTC) < datetime.now(UTC)
    except ValueError:
        return False


def _amount(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _salary(low, high, currency, period, source_url: str) -> dict:
    low, high = _amount(low), _amount(high)
    if low is not None and high is not None and low > high:
        low, high = None, None
    currency = _string(currency, 10)
    if not re.fullmatch(r"[A-Za-z]{3}", currency):
        currency = ""
    period = re.sub(r"[\s_-]+", "", _string(period).casefold())
    interval = {
        "year": "year", "yearly": "year", "annual": "year", "annually": "year", "1year": "year",
        "month": "month", "monthly": "month", "1month": "month",
        "hour": "hour", "hourly": "hour", "1hour": "hour",
    }.get(period, "unknown")
    return {
        "salary_min": low, "salary_max": high, "salary_currency": currency.upper(),
        "salary_period": interval,
        "salary_source": source_url if low is not None or high is not None else "",
    }


def _base(board: Board, item: dict, source: str, listing: str, source_id: str, title: str) -> dict:
    if not source_id or not listing or not title:
        raise ValueError("Posting is missing its identity, title, or public URL")
    pool = bool(POOL_TITLE.search(title))
    return {
        "kind": "company_lead" if pool else "open_role",
        "company": _string(item.get("company_name") or item.get("companyName"), 250) or board.slug,
        "title": title[:300], "listing_url": listing,
        "apply_url": "" if pool else listing, "careers_url": board.careers_url,
        "source_provider": board.provider,
        "source_board": f"eu/{board.slug}" if board.provider == "lever" and "api.eu.lever.co/" in board.endpoint else board.slug,
        "source_id": source_id, "listing_source": "ats",
        "listing_status": "unverified" if pool else "open", "last_checked_at": now(),
        "source_urls": list(dict.fromkeys([source, listing])),
    }


def _greenhouse(board: Board, item: dict, source: str) -> dict:
    listing = _url(item.get("absolute_url"))
    result = _base(board, item, source, listing, _identifier(item.get("id")), _string(item.get("title"), 300))
    location = item.get("location")
    result.update({
        "requisition_id": _identifier(item.get("requisition_id")),
        "location": _string(location.get("name")) if isinstance(location, dict) else "",
        "department": _names(item.get("departments"), 200),
        "description": _plain(item.get("content")),
        "source_updated_at": _date(item.get("updated_at")),
        "posted_at": _date(item.get("first_published")),
        "valid_through": _date(item.get("application_deadline")),
        "date_basis": "Greenhouse first_published" if _date(item.get("first_published")) else "Greenhouse updated_at is modification time; original posting date unavailable",
    })
    # The documented null internal_job_id identifies a prospect post. Missing
    # internal_job_id is not the same evidence as an explicitly null value.
    if "internal_job_id" in item and item["internal_job_id"] is None:
        result.update(kind="company_lead", listing_status="unverified", apply_url="")
    if result["valid_through"] and _expired(result["valid_through"]):
        result["listing_status"] = "closed"
    return result


def _lever(board: Board, item: dict, source: str) -> dict:
    listing = _url(item.get("hostedUrl"))
    result = _base(board, item, source, listing, _identifier(item.get("id")), _string(item.get("text"), 300))
    categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
    all_locations = categories.get("allLocations")
    location = "; ".join(v for v in all_locations if isinstance(v, str))[:1000] if isinstance(all_locations, list) else _string(categories.get("location"))
    sections = [_plain(item.get("descriptionPlain") or item.get("description")), _plain(item.get("openingPlain") or item.get("opening"))]
    lists = item.get("lists")
    if isinstance(lists, list):
        sections.extend(f"{_plain(v.get('text'), 300)}: {_plain(v.get('content'))}" for v in lists[:30] if isinstance(v, dict))
    sections.extend([_plain(item.get("additionalPlain") or item.get("additional")), _plain(item.get("salaryDescriptionPlain"))])
    result.update({
        "apply_url": (_url(item.get("applyUrl")) or listing) if result["kind"] == "open_role" else "",
        "location": location,
        "work_mode": {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite"}.get(_string(item.get("workplaceType")), "unknown"),
        "employment_type": _string(categories.get("commitment"), 200),
        "department": _string(categories.get("department") or categories.get("team"), 200),
        "description": _plain(" ".join(sections)),
        "date_basis": "Lever public feed does not establish an original posting date",
    })
    salary = item.get("salaryRange")
    if isinstance(salary, dict):
        result.update(_salary(salary.get("min"), salary.get("max"), salary.get("currency"), salary.get("interval"), source))
    return result


def _ashby(board: Board, item: dict, source: str) -> dict | None:
    # Unlisted records are not meant to be discoverable on the public board.
    if item.get("isListed") is False:
        return None
    listing = _url(item.get("jobUrl"))
    source_id = _identifier(item.get("id")) or (urlsplit(listing).path.rstrip("/").split("/")[-1] if listing else "")
    result = _base(board, item, source, listing, source_id, _string(item.get("title"), 300))
    locations = [_string(item.get("location"))]
    if isinstance(item.get("secondaryLocations"), list):
        locations.extend(_string(v.get("location")) for v in item["secondaryLocations"] if isinstance(v, dict))
    compensation = item.get("compensation") if isinstance(item.get("compensation"), dict) else {}
    result.update({
        "apply_url": (_url(item.get("applyUrl")) or listing) if result["kind"] == "open_role" else "",
        "location": "; ".join(dict.fromkeys(v for v in locations if v))[:1000],
        "work_mode": {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}.get(_string(item.get("workplaceType")), "remote" if item.get("isRemote") is True else "unknown"),
        "employment_type": _string(item.get("employmentType"), 200),
        "department": _string(item.get("department") or item.get("team"), 200),
        "description": _plain(" ".join([
            _plain(item.get("descriptionPlain") or item.get("descriptionHtml")),
            _string(compensation.get("scrapeableCompensationSalarySummary"), 4000),
        ])),
        "source_updated_at": _date(item.get("publishedAt")),
        "date_basis": "Ashby publishedAt is last publication, not an original posting date",
    })
    components = compensation.get("summaryComponents")
    # Do not combine geographic pay tiers or confuse equity/bonuses with salary.
    if isinstance(components, list):
        salaries = [v for v in components if isinstance(v, dict) and v.get("compensationType") == "Salary"]
        if len(salaries) == 1:
            salary = salaries[0]
            result.update(_salary(salary.get("minValue"), salary.get("maxValue"), salary.get("currencyCode"), salary.get("interval"), source))
    return result


def fetch_board(url: str, limit: int = 100, cancel: threading.Event | None = None) -> dict:
    """Import at most `limit` records; return partial work with explicit warnings.

    `complete` means the returned board was exhausted without lost/invalid data,
    not that a company has no other jobs. `requests` counts feed fetch operations;
    each uses the shared transport's four-attempt redirect bound.
    """
    result = {"opportunities": [], "warnings": [], "requests": 0, "complete": False}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        result["warnings"].append(f"Board limit must be between 1 and {MAX_RESULTS}.")
        return result
    try:
        board = parse_board(url)
    except ValueError as exc:
        result["warnings"].append(str(exc))
        return result
    deadline = time.monotonic() + BOARD_TIMEOUT_SECONDS
    allowed_hosts = frozenset({urlsplit(board.endpoint).hostname})
    seen, skipped, offset = set(), 0, 0
    normalizer = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}[board.provider]
    for _ in range(MAX_LEVER_PAGES if board.provider == "lever" else 1):
        if cancel and cancel.is_set():
            result["warnings"].append("Board import cancelled; completed records were retained.")
            break
        if time.monotonic() >= deadline:
            result["warnings"].append("Board time limit reached; completed records were retained.")
            break
        count = min(100, limit - len(result["opportunities"]))
        endpoint = board.endpoint + "?" + urlencode({"mode": "json", "skip": offset, "limit": count}) if board.provider == "lever" else board.endpoint
        result["requests"] += 1
        try:
            payload = fetch_public_json(endpoint, cancel=cancel, allowed_hosts=allowed_hosts, deadline=deadline)
            records = payload if board.provider == "lever" else payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(records, list):
                raise SourceUnavailable("Board returned an unexpected jobs structure")
        except SourceUnavailable as exc:
            result["warnings"].append(f"{board.provider} board could not be fully read: {exc}")
            break
        new_ids = 0
        exhausted = True
        for item in records:
            if cancel and cancel.is_set():
                exhausted = False
                result["warnings"].append("Board import cancelled; completed records were retained.")
                break
            if time.monotonic() >= deadline:
                exhausted = False
                result["warnings"].append("Board time limit reached; completed records were retained.")
                break
            if len(result["opportunities"]) >= limit:
                exhausted = False
                break
            try:
                if not isinstance(item, dict):
                    raise ValueError("Not a posting object")
                normalized = normalizer(board, item, endpoint)
                if normalized is None:
                    continue
                seed = OpportunitySeed.model_validate(normalized).model_dump()
                identity = seed["source_id"]
                if identity in seen:
                    continue
                seen.add(identity)
                new_ids += 1
                result["opportunities"].append(seed)
            except (ValueError, TypeError, ValidationError):
                skipped += 1
        if not exhausted:
            result["warnings"].append("Board result limit or cancellation left records unimported.")
            break
        if board.provider != "lever":
            total = payload.get("meta", {}).get("total") if isinstance(payload.get("meta"), dict) else None
            if isinstance(total, int) and total > len(records):
                result["warnings"].append("Board response reports more records than it returned.")
            else:
                result["complete"] = skipped == 0
            break
        if len(records) < count:
            result["complete"] = skipped == 0
            break
        if len(result["opportunities"]) >= limit:
            result["warnings"].append("Board result limit reached; additional postings may exist.")
            break
        if not new_ids and records:
            result["warnings"].append("Board pagination made no progress; stopped to avoid repeated requests.")
            break
        offset += len(records)
    else:
        result["warnings"].append("Board request limit reached; additional postings may exist.")
    if skipped:
        result["warnings"].append(f"Skipped {skipped} malformed posting(s); check the public board for full coverage.")
    return result


class ListingParser(HTMLParser):
    """Keep visible text and bounded JSON-LD separately; never execute page code."""

    def __init__(self):
        super().__init__()
        self.title_parts = []
        self.scripts = []
        self.current = None
        self.current_size = 0
        self.in_title = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"noscript", "svg", "style"}:
            self.hidden += 1
        if tag == "title" and not self.hidden:
            self.in_title = True
        if tag == "script" and not self.hidden and len(self.scripts) < 20:
            if (dict(attrs).get("type") or "").casefold().split(";", 1)[0].strip() == "application/ld+json":
                self.current, self.current_size = [], 0

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.current is not None:
            if self.current_size <= 100_000:
                self.scripts.append("".join(self.current))
            self.current = None
        if tag in {"noscript", "svg", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data[:1000])
        if self.current is not None:
            self.current_size += len(data)
            if self.current_size <= 100_000:
                self.current.append(data)


def _job_nodes(value, depth: int = 0):
    if depth > 10:
        return
    if isinstance(value, list):
        for child in value[:200]:
            yield from _job_nodes(child, depth + 1)
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            yield value
        # JSON-LD commonly nests graph nodes, not arbitrary script key values.
        if "@graph" in value:
            yield from _job_nodes(value["@graph"], depth + 1)
        if "mainEntity" in value:
            yield from _job_nodes(value["mainEntity"], depth + 1)


def _schema_metadata(item: dict, source: str) -> dict:
    organization = item.get("hiringOrganization")
    organization = organization if isinstance(organization, dict) else {}
    identifier = item.get("identifier")
    identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
    locations = item.get("jobLocation")
    locations = locations if isinstance(locations, list) else [locations]
    addresses = []
    for location in locations[:20]:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, str):
            addresses.append(address)
        elif isinstance(address, dict):
            country = address.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            addresses.append(", ".join(_string(v) for v in [address.get("addressLocality"), address.get("addressRegion"), country] if _string(v)))
    eligibility = item.get("applicantLocationRequirements")
    eligibility = eligibility if isinstance(eligibility, list) else [eligibility]
    eligibility = "; ".join(_string(v.get("name")) if isinstance(v, dict) else _string(v) for v in eligibility)
    employment = item.get("employmentType")
    metadata = {
        "title": _string(item.get("title"), 300), "description": _plain(item.get("description")),
        "company": _string(organization.get("name"), 250),
        "company_url": _url(organization.get("sameAs") or organization.get("url")),
        "listing_url": _url(item.get("url")) or source,
        "listing_url_explicit": bool(_url(item.get("url"))),
        "requisition_id": _identifier(identifier), "posted_at": _date(item.get("datePosted")),
        "valid_through": _date(item.get("validThrough")),
        "date_basis": "JobPosting datePosted (source-provided original posting date)",
        "location": "; ".join(addresses)[:1000],
        "remote_eligibility": eligibility[:1500],
        "work_mode": "remote" if item.get("jobLocationType") == "TELECOMMUTE" else "unknown",
        "employment_type": "; ".join(v for v in employment if isinstance(v, str))[:200] if isinstance(employment, list) else _string(employment, 200),
    }
    salary = item.get("baseSalary")
    if isinstance(salary, dict):
        value = salary.get("value")
        if isinstance(value, dict):
            single = value.get("value")
            metadata.update(_salary(value.get("minValue", single), value.get("maxValue", single), salary.get("currency"), value.get("unitText"), source))
    return metadata


def inspect_listing(url: str, cancel: threading.Event | None = None) -> dict:
    """Inspect source metadata; a reachable page alone never proves an opening."""
    result = {
        "url": url, "title": "", "text": "", "jobpostings": [],
        "listing_status": "unverified", "status_basis": "", "checked_at": now(),
        "requests": 0, "warnings": [],
    }
    try:
        document = fetch_public_document(url, cancel=cancel)
    except SourceUnavailable as exc:
        result["requests"] = 1 if exc.status_code else 0
        result["warnings"].append(str(exc))
        if exc.status_code in {404, 410}:
            result.update(listing_status="closed", status_basis=f"Listing URL returned HTTP {exc.status_code}; no reason for removal is inferred")
        return result
    result.update(url=document.url, requests=document.requests)
    text_parser = TextParser()
    text_parser.feed(document.text)
    result["text"] = re.sub(r"\s+", " ", " ".join(text_parser.parts)).strip()[:30000] if "html" in document.content_type else document.text[:30000]
    parser = ListingParser()
    if "html" in document.content_type:
        parser.feed(document.text)
    result["title"] = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:500]
    for script in parser.scripts:
        try:
            parsed = json.loads(script)
        except (ValueError, RecursionError):
            result["warnings"].append("Ignored invalid JobPosting JSON-LD.")
            continue
        for posting in _job_nodes(parsed):
            if len(result["jobpostings"]) >= 20:
                break
            result["jobpostings"].append(_schema_metadata(posting, document.url))
    postings = result["jobpostings"]
    if postings and all(p["valid_through"] and _expired(p["valid_through"]) for p in postings):
        result.update(listing_status="closed", status_basis="All extracted JobPosting records have expired validThrough dates")
    else:
        result["status_basis"] = "Page is reachable; review current role-specific evidence before confirming it is open"
    return result
