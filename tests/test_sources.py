from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from job_finder import sources
from job_finder.models import OpportunitySeed
from job_finder.research import PublicDocument, SourceUnavailable


def greenhouse(job_id=101, **overrides):
    return {
        "id": job_id, "internal_job_id": 999, "title": "Manufacturing Engineer",
        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{job_id}",
        "location": {"name": "Dayton, OH"}, "updated_at": "2026-09-01T10:00:00Z",
        "content": "&lt;p&gt;Build flight components.&lt;/p&gt;", **overrides,
    }


def lever(job_id=101, **overrides):
    return {
        "id": str(job_id), "text": "Manufacturing Engineer",
        "hostedUrl": f"https://jobs.lever.co/acme/{job_id}",
        "applyUrl": f"https://jobs.lever.co/acme/{job_id}/apply",
        "categories": {"location": "Dayton, OH", "commitment": "Full-time"},
        "descriptionPlain": "Build flight components.", **overrides,
    }


def ashby(job_id="abc-123", **overrides):
    return {
        "title": "Manufacturing Engineer", "jobUrl": f"https://jobs.ashbyhq.com/acme/{job_id}",
        "applyUrl": f"https://jobs.ashbyhq.com/acme/{job_id}/application",
        "location": "Dayton, OH", "isListed": True,
        "publishedAt": "2026-09-01T10:00:00Z", "descriptionPlain": "Build flight components.",
        **overrides,
    }


def feeds(monkeypatch, *responses):
    pending, calls = list(responses), []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if not pending:
            pytest.fail("Unexpected extra feed fetch")
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(sources, "fetch_public_json", fetch)
    return calls


@pytest.mark.parametrize(
    ("url", "provider", "endpoint"),
    [
        ("https://boards.greenhouse.io/acme", "greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"),
        ("https://jobs.lever.co/acme", "lever", "https://api.lever.co/v0/postings/acme"),
        ("https://jobs.eu.lever.co/acme", "lever", "https://api.eu.lever.co/v0/postings/acme"),
        ("https://jobs.ashbyhq.com/acme", "ashby", "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"),
    ],
)
def test_supported_board_urls_construct_documented_endpoints(url, provider, endpoint):
    board = sources.parse_board(url)
    assert board.provider == provider
    assert board.endpoint == endpoint


@pytest.mark.parametrize("url", [
    "https://example.com/jobs", "http://127.0.0.1/jobs", "https://jobs.lever.co.evil.com/acme",
    "https://user:secret@jobs.lever.co/acme", "https://jobs.lever.co:8000/acme",
    "https://jobs.ashbyhq.com/", "https://jobs.ashbyhq.com/%2fsecret",
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs", "javascript:alert(1)",
])
def test_arbitrary_hosts_and_path_injection_never_reach_network(monkeypatch, url):
    calls = feeds(monkeypatch)
    result = sources.fetch_board(url)
    assert result["warnings"]
    assert result["requests"] == 0
    assert result["complete"] is False
    assert calls == []


def test_greenhouse_identity_prospect_posts_and_honest_dates(monkeypatch):
    calls = feeds(monkeypatch, {"jobs": [greenhouse(), greenhouse(102, internal_job_id=None)]})
    result = sources.fetch_board("https://boards.greenhouse.io/acme")
    job, pool = result["opportunities"]
    assert result["complete"] is True
    assert job["source_id"] == "101"
    assert job["source_board"] == "acme"
    assert job["listing_status"] == "open"
    assert job["posted_at"] == ""
    assert job["source_updated_at"] == "2026-09-01T10:00:00Z"
    assert job["salary_min"] is None and job["salary_max"] is None
    assert job["company_url"] == ""
    assert job["description"] == "Build flight components."
    assert pool["kind"] == "company_lead"
    assert pool["listing_status"] == "unverified"
    assert pool["apply_url"] == ""
    assert calls[0][1]["allowed_hosts"] == frozenset({"boards-api.greenhouse.io"})
    assert all(OpportunitySeed.model_validate(v) for v in result["opportunities"])


@pytest.mark.parametrize("title", ["General Application", "Join our Talent Community", "Future Opportunities", "Expression of Interest"])
def test_talent_pools_are_company_leads_on_any_board(monkeypatch, title):
    feeds(monkeypatch, [lever(text=title)])
    job = sources.fetch_board("https://jobs.lever.co/acme")["opportunities"][0]
    assert job["kind"] == "company_lead"
    assert job["listing_status"] == "unverified"
    assert job["apply_url"] == ""


def test_actual_talent_acquisition_role_is_not_misclassified(monkeypatch):
    feeds(monkeypatch, [lever(text="Senior Talent Acquisition Manager")])
    job = sources.fetch_board("https://jobs.lever.co/acme")["opportunities"][0]
    assert job["kind"] == "open_role"


def test_expired_deadline_is_not_open_even_on_current_feed(monkeypatch):
    feeds(monkeypatch, {"jobs": [greenhouse(application_deadline="2001-01-01", first_published="2000-12-01")]})
    job = sources.fetch_board("https://boards.greenhouse.io/acme")["opportunities"][0]
    assert job["listing_status"] == "closed"
    assert job["posted_at"] == "2000-12-01"


def test_bad_record_preserves_good_records_without_claiming_complete_coverage(monkeypatch):
    feeds(monkeypatch, {"jobs": [greenhouse(), {"title": "No identity"}, greenhouse(103, absolute_url="http://127.0.0.1/role")]})
    result = sources.fetch_board("https://boards.greenhouse.io/acme")
    assert len(result["opportunities"]) == 1
    assert result["complete"] is False
    assert "Skipped 2" in " ".join(result["warnings"])


def test_board_limit_and_reported_incomplete_payload_are_explicit(monkeypatch):
    feeds(monkeypatch, {"jobs": [greenhouse(1), greenhouse(2)]}, {"jobs": [greenhouse(1)], "meta": {"total": 2}})
    assert not sources.fetch_board("https://boards.greenhouse.io/acme", limit=1)["complete"]
    result = sources.fetch_board("https://boards.greenhouse.io/acme")
    assert not result["complete"] and result["warnings"]


def test_lever_pagination_uses_skip_and_eu_identity(monkeypatch):
    calls = feeds(monkeypatch, [lever(i) for i in range(100)], [lever(100)])
    result = sources.fetch_board("https://jobs.eu.lever.co/acme", limit=200)
    assert len(result["opportunities"]) == 101
    assert result["complete"] is True
    assert result["requests"] == 2
    assert parse_qs(urlsplit(calls[0][0]).query) == {"mode": ["json"], "skip": ["0"], "limit": ["100"]}
    assert parse_qs(urlsplit(calls[1][0]).query)["skip"] == ["100"]
    assert calls[0][1]["allowed_hosts"] == frozenset({"api.eu.lever.co"})
    assert result["opportunities"][0]["source_board"] == "eu/acme"


def test_lever_partial_failure_preserves_first_page(monkeypatch):
    feeds(monkeypatch, [lever(i) for i in range(100)], SourceUnavailable("Source returned HTTP 429", 429))
    result = sources.fetch_board("https://jobs.lever.co/acme", limit=200)
    assert len(result["opportunities"]) == 100
    assert not result["complete"]
    assert "429" in " ".join(result["warnings"])


def test_lever_repeated_page_cannot_loop_or_duplicate_results(monkeypatch):
    page = [lever(i) for i in range(100)]
    calls = feeds(monkeypatch, page, page)
    result = sources.fetch_board("https://jobs.lever.co/acme", limit=300)
    assert len(calls) == 2
    assert len(result["opportunities"]) == 100
    assert not result["complete"]
    assert "no progress" in " ".join(result["warnings"])


def test_lever_page_budget_is_bounded(monkeypatch):
    monkeypatch.setattr(sources, "MAX_LEVER_PAGES", 2)
    calls = feeds(monkeypatch, [lever(i) for i in range(100)], [lever(i) for i in range(100, 200)])
    result = sources.fetch_board("https://jobs.lever.co/acme", limit=400)
    assert len(calls) == 2
    assert len(result["opportunities"]) == 200
    assert not result["complete"]
    assert "request limit" in " ".join(result["warnings"])


def test_cancelled_and_zero_time_budget_do_no_network(monkeypatch):
    calls = feeds(monkeypatch)
    cancel = threading.Event()
    cancel.set()
    result = sources.fetch_board("https://jobs.lever.co/acme", cancel=cancel)
    assert result["requests"] == 0 and not result["complete"]
    monkeypatch.setattr(sources, "BOARD_TIMEOUT_SECONDS", 0)
    assert sources.fetch_board("https://jobs.lever.co/acme")["requests"] == 0
    assert calls == []


@pytest.mark.parametrize("limit", [0, 1001, True, -1, "100"])
def test_invalid_limits_do_no_network(monkeypatch, limit):
    calls = feeds(monkeypatch)
    assert sources.fetch_board("https://jobs.lever.co/acme", limit=limit)["warnings"]
    assert calls == []


def test_ashby_hides_unlisted_and_preserves_publication_semantics(monkeypatch):
    feeds(monkeypatch, {"apiVersion": "1", "jobs": [ashby(), ashby("unlisted", isListed=False)]})
    result = sources.fetch_board("https://jobs.ashbyhq.com/acme")
    assert result["complete"]
    assert len(result["opportunities"]) == 1
    job = result["opportunities"][0]
    assert job["source_id"] == "abc-123"
    assert job["posted_at"] == ""
    assert job["source_updated_at"] == "2026-09-01T10:00:00Z"
    assert "last publication" in job["date_basis"]


def test_ashby_salary_uses_salary_not_equity_or_bonus(monkeypatch):
    feeds(monkeypatch, {"jobs": [ashby(compensation={"summaryComponents": [
        {"compensationType": "EquityPercentage", "minValue": 1, "maxValue": 2},
        {"compensationType": "Salary", "minValue": 150000, "maxValue": 190000, "currencyCode": "USD", "interval": "1 YEAR"},
        {"compensationType": "Bonus", "minValue": 20000, "maxValue": 30000},
    ]})]})
    job = sources.fetch_board("https://jobs.ashbyhq.com/acme")["opportunities"][0]
    assert (job["salary_min"], job["salary_max"], job["salary_period"], job["salary_currency"]) == (150000, 190000, "year", "USD")
    assert "includeCompensation=true" in job["salary_source"]


def test_multiple_ashby_salary_summaries_are_not_merged_across_currencies(monkeypatch):
    feeds(monkeypatch, {"jobs": [ashby(compensation={"summaryComponents": [
        {"compensationType": "Salary", "minValue": 150000, "currencyCode": "USD", "interval": "1 YEAR"},
        {"compensationType": "Salary", "minValue": 100000, "currencyCode": "GBP", "interval": "1 YEAR"},
    ]})]})
    job = sources.fetch_board("https://jobs.ashbyhq.com/acme")["opportunities"][0]
    assert job["salary_min"] is None and job["salary_max"] is None


@pytest.mark.parametrize(("low", "high"), [(float("nan"), float("inf")), (True, -2), (200, 100), (10 ** 1000, 10 ** 1001)])
def test_invalid_salary_never_becomes_a_number(monkeypatch, low, high):
    feeds(monkeypatch, [lever(salaryRange={"min": low, "max": high, "currency": "USD", "interval": "year"})])
    job = sources.fetch_board("https://jobs.lever.co/acme")["opportunities"][0]
    assert job["salary_min"] is None and job["salary_max"] is None


def test_remote_does_not_invent_country_eligibility_or_sponsorship(monkeypatch):
    feeds(monkeypatch, [lever(workplaceType="remote", salaryRange={"min": 40, "max": 60, "currency": "USD", "interval": "hour"})])
    job = sources.fetch_board("https://jobs.lever.co/acme")["opportunities"][0]
    assert job["work_mode"] == "remote"
    assert job["remote_eligibility"] == ""
    assert job["sponsorship"] == "unknown"
    assert job["clearance_requirement"] == ""
    assert job["salary_period"] == "hour"


def listing(monkeypatch, body):
    monkeypatch.setattr(sources, "fetch_public_document", lambda *a, **kw: PublicDocument("https://example.com/jobs/1", body, "text/html"))


def test_listing_metadata_preserves_actual_pay_eligibility_and_original_date(monkeypatch):
    posting = {
        "@type": "JobPosting", "title": "Engineer", "description": "<p>Build useful things.</p>",
        "hiringOrganization": {"name": "Acme", "sameAs": "https://example.com"},
        "datePosted": "2026-01-01", "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Canada"},
        "baseSalary": {"currency": "CAD", "value": {"minValue": 50, "maxValue": 70, "unitText": "HOUR"}},
    }
    listing(monkeypatch, f'<title>Acme careers</title><h1>Engineer</h1><script type="application/ld+json">{json.dumps({"@graph": [posting]})}</script><script>window.secret = "hidden@example.com"</script>')
    result = sources.inspect_listing("https://example.com/jobs/1")
    item = result["jobpostings"][0]
    assert result["title"] == "Acme careers"
    assert "hidden@example.com" not in result["text"]
    assert result["listing_status"] == "unverified"
    assert item["remote_eligibility"] == "Canada"
    assert item["salary_min"] == 50 and item["salary_period"] == "hour"
    assert item["posted_at"] == "2026-01-01"
    assert item["listing_url_explicit"] is False


def test_structured_listing_url_distinguishes_explicit_role_from_page_fallback(monkeypatch):
    listing(monkeypatch, '<script type="application/ld+json">[{"@type":"JobPosting","title":"Other role"},{"@type":"JobPosting","url":"https://example.com/jobs/1","title":"Exact role"}]</script>')
    items = sources.inspect_listing("https://example.com/jobs/1")["jobpostings"]
    assert items[0]["listing_url"] == items[1]["listing_url"]
    assert items[0]["listing_url_explicit"] is False
    assert items[1]["listing_url_explicit"] is True


def test_estimated_salary_is_not_employer_salary(monkeypatch):
    listing(monkeypatch, '<script type="application/ld+json">{"@type":"JobPosting","estimatedSalary":{"currency":"USD","value":{"minValue":90000,"unitText":"YEAR"}}}</script>')
    item = sources.inspect_listing("https://example.com/jobs/1")["jobpostings"][0]
    assert item.get("salary_min") is None


def test_expired_structured_job_is_closed_but_reachable_generic_page_is_unverified(monkeypatch):
    listing(monkeypatch, '<script type="application/ld+json">[{"@type":"JobPosting","validThrough":"2001-01-01"}]</script>')
    assert sources.inspect_listing("https://example.com/jobs/1")["listing_status"] == "closed"
    listing(monkeypatch, "<title>Careers</title><p>We are hiring!</p>")
    assert sources.inspect_listing("https://example.com/jobs/1")["listing_status"] == "unverified"


@pytest.mark.parametrize(("status", "expected"), [(404, "closed"), (410, "closed"), (403, "unverified"), (429, "unverified"), (500, "unverified")])
def test_unavailable_listing_distinguishes_removal_from_temporary_fetch_failure(monkeypatch, status, expected):
    def fail(*a, **kw):
        raise SourceUnavailable(f"Source returned HTTP {status}", status)
    monkeypatch.setattr(sources, "fetch_public_document", fail)
    result = sources.inspect_listing("https://example.com/jobs/1")
    assert result["listing_status"] == expected
    assert result["warnings"]


def test_malformed_and_hidden_json_ld_do_not_crash_or_manufacture_jobs(monkeypatch):
    listing(monkeypatch, '<script type="application/ld+json">{invalid}</script><noscript><script type="application/ld+json">{"@type":"JobPosting","title":"Hidden"}</script></noscript>')
    result = sources.inspect_listing("https://example.com/jobs/1")
    assert result["jobpostings"] == []
    assert result["warnings"]
