from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from job_finder import pipeline
from job_finder.config import AppError
from job_finder.models import Model, OpportunitySeed, ResearchResult, now
from job_finder.pipeline import Context, Jobs, Stopped
from job_finder.providers import AgentResult, ProviderError
from job_finder.service import Service


class Payload(Model):
    ok: bool


@pytest.fixture
def make_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "inspect_providers",
        lambda: [
            {"id": name, "available": True, "message": ""}
            for name in ("claude", "codex")
        ],
    )

    def inspect_fixture(url, cancel=None):
        try:
            return {
                "url": url,
                "text": pipeline.fetch_public_text(url, cancel=cancel),
                "jobpostings": [],
                "listing_status": "unverified",
                "status_basis": "Page is reachable",
                "warnings": [],
            }
        except pipeline.SourceUnavailable as exc:
            removed = exc.status_code in {404, 410}
            return {
                "url": url,
                "text": "",
                "jobpostings": [],
                "listing_status": "closed" if removed else "unverified",
                "status_basis": f"Listing URL returned HTTP {exc.status_code}; no reason for removal is inferred"
                if removed
                else "",
                "warnings": [],
            }

    monkeypatch.setattr(pipeline.sources, "inspect_listing", inspect_fixture)
    count = [0]

    def create(
        *,
        settings=None,
        opportunities=2,
        resume="I have five years of experience as a manufacturing engineer using CAD and process design.",
    ):
        count[0] += 1
        service = Service(tmp_path / str(count[0]))
        search = service.create_search(
            {
                "name": "Regression search",
                "objective": "Find manufacturing engineer jobs.",
                "candidate": {
                    "resume_text": resume,
                    "skills": ["CAD", "process design"],
                },
                "preferences": {"roles": ["Manufacturing Engineer"]},
                "settings": {
                    "provider": "claude",
                    "concurrency": 1,
                    "max_cost_usd": 20,
                    "min_fit": 0,
                }
                | (settings or {}),
            }
        )
        for index in range(opportunities):
            website = f"https://company{index}.example.com"
            service.upsert_seed(
                search["id"],
                OpportunitySeed(
                    company=f"Company {index}",
                    company_url=website,
                    title="Manufacturing Engineer",
                    listing_url=website + "/jobs/engineer",
                    apply_url=website + "/jobs/engineer",
                    email=f"team@company{index}.example.com",
                    email_source=website + "/contact",
                    source_urls=[website + "/jobs/engineer"],
                ),
            )
        return service, search

    return create


@pytest.fixture
def install_runner(monkeypatch):
    def install(callback):
        calls = []
        lock = threading.Lock()

        class Runner:
            def __init__(self, provider, **kwargs):
                self.provider, self.options = provider, kwargs

            def run(self, prompt, schema, *, web, cancel):
                with lock:
                    record = {
                        "index": len(calls),
                        "provider": self.provider,
                        "prompt": prompt,
                        "schema": schema["title"],
                        "web": web,
                        "options": self.options,
                    }
                    calls.append(record)
                return callback(record, cancel)

        monkeypatch.setattr(pipeline, "AgentRunner", Runner)
        return calls

    return install


def queued_context(service, search):
    manager = Jobs(service)
    manager.launch = lambda job_id: None
    job = manager.create(search["id"], "research")
    return manager, Context(service, job["id"])


def research_payload(opportunity):
    seed = {key: opportunity[key] for key in OpportunitySeed.model_fields}
    source = opportunity["listing_url"] or opportunity["company_url"]
    return ResearchResult(
        **(
            seed
            | {
                "listing_status": "open",
                "last_checked_at": now(),
                "evidence": [{"url": source, "quote": "Manufacturing Engineer"}],
                "match_components": [
                    {
                        "criterion": "role",
                        "match": "strong",
                        "reason": "Matching role",
                        "source_url": source,
                        "candidate_quote": "manufacturing engineer",
                    }
                ],
                "requirement_checks": [],
                "candidate_claims": [
                    {
                        "text": "I have manufacturing engineering experience.",
                        "resume_quote": "manufacturing engineer",
                    }
                ],
                "skill_gaps": [],
                "risks": [],
                "interview_hooks": ["Process design"],
                "application_tips": [],
                "research_summary": "Employer has a manufacturing role.",
                "outreach_angle": "Ask about process design needs.",
            }
        )
    ).model_dump()


def research_data(service, record):
    opportunity = next(
        row
        for row in service.store.list("opportunities")
        if row["company_url"] in record["prompt"]
    )
    return AgentResult(
        research_payload(opportunity), input_tokens=100, output_tokens=50, cost_usd=0.1
    )


def test_concurrent_requests_never_exceed_call_limit(make_service, install_runner):
    service, search = make_service(settings={"max_agent_calls": 3, "concurrency": 4})
    _, ctx = queued_context(service, search)

    def complete(record, cancel):
        time.sleep(0.03)
        assert not cancel.is_set()
        return AgentResult({"ok": True}, cost_usd=0.1)

    calls = install_runner(complete)

    def request(_):
        try:
            return ctx.call("bounded", Payload)
        except Stopped:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(20)))
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == job["agent_calls"] == 3
    assert sum(result is not None for result in results) == 3
    assert job["active_calls"] == job["reserved_cost"] == 0
    assert job["cost_usd"] == pytest.approx(0.3)
    assert ctx.end_status == "budget_exhausted"


def test_cost_reservation_waits_without_cancelling_inflight_work(
    make_service, install_runner
):
    service, search = make_service(settings={"max_cost_usd": 1})
    _, ctx = queued_context(service, search)
    started, release = threading.Event(), threading.Event()

    def complete(record, cancel):
        if record["index"] == 0:
            started.set()
            assert release.wait(2)
        assert not cancel.is_set()
        return AgentResult({"ok": True}, cost_usd=0.2)

    calls = install_runner(complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ctx.call, "first", Payload)
        assert started.wait(1)
        second = pool.submit(ctx.call, "second", Payload)
        time.sleep(0.12)
        assert len(calls) == 1
        assert not ctx.stop.is_set()
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2) == {"ok": True}
    assert [call["options"]["max_cost_usd"] for call in calls] == pytest.approx(
        [1, 0.8]
    )
    job = service.store.get("runs", ctx.job_id)
    assert job["cost_usd"] == pytest.approx(0.4)
    assert job["cost_unreported"] == job["reserved_cost"] == 0


def test_missing_cost_consumes_conservative_allowance(make_service, install_runner):
    service, search = make_service(settings={"max_cost_usd": 1})
    _, ctx = queued_context(service, search)
    calls = install_runner(lambda record, cancel: AgentResult({"ok": True}))
    assert ctx.call("first", Payload) == {"ok": True}
    with pytest.raises(Stopped):
        ctx.call("second", Payload)
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == 1
    assert job["cost_usd"] is None
    assert job["cost_unreported"] == 1
    assert job["cost_unreported_calls"] == 1
    assert job["active_calls"] == job["reserved_cost"] == 0


@pytest.mark.parametrize(
    "code", ["network_error", "invalid_response", "missing_result"]
)
def test_retryable_provider_codes_count_each_attempt_and_unknown_cost(
    make_service, install_runner, code
):
    service, search = make_service()
    _, ctx = queued_context(service, search)

    def complete(record, cancel):
        if record["index"] == 0:
            raise ProviderError(code, "Retry once.")
        return AgentResult({"ok": True}, cost_usd=0.2)

    calls = install_runner(complete)
    assert ctx.call("retry", Payload) == {"ok": True}
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == job["agent_calls"] == 2
    assert job["cost_usd"] == pytest.approx(0.2)
    assert job["cost_unreported"] == 2


@pytest.mark.parametrize(
    "code",
    [
        "auth_required",
        "not_installed",
        "unsupported_cli",
        "native_executable_required",
        "rate_limited",
        "launch_failed",
    ],
)
def test_fatal_provider_error_stops_search_before_other_opportunities(
    make_service, install_runner, code
):
    service, search = make_service(opportunities=8)

    def fail(record, cancel):
        raise ProviderError(code, "Provider needs setup.")

    calls = install_runner(fail)
    job = Jobs(service).create(search["id"], "research", background=False)
    assert job["status"] == "failed"
    assert "Provider needs setup" in job["error"]
    assert len(calls) == job["agent_calls"] == 1
    assert job["active_calls"] == job["reserved_cost"] == 0


def test_failed_company_marks_job_failed_and_resume_reuses_success(
    make_service, install_runner
):
    service, search = make_service()
    should_fail = [True]

    def complete(record, cancel):
        if "Company 1" in record["prompt"] and should_fail[0]:
            raise ProviderError("provider_failed", "Company request failed.")
        return research_data(service, record)

    calls = install_runner(complete)
    manager = Jobs(service)
    first = manager.create(search["id"], "research", background=False)
    assert first["status"] == "failed"
    assert first["run_error_count"] == 1
    assert first["stage_index"] == first["stage_count"] == 1
    assert len(calls) == 2
    should_fail[0] = False
    resumed = manager.control(first["id"], "resume", background=False)
    assert resumed["status"] == "completed"
    assert resumed["run_error_count"] == 0
    assert resumed["error_count"] == 1
    assert len(calls) == resumed["agent_calls"] == 3
    assert all(
        opportunity["research_status"] == "complete"
        for opportunity in service.store.list("opportunities")
    )


def test_pause_after_provider_result_saves_it_without_paid_duplicate(
    make_service, install_runner
):
    service, search = make_service()
    manager = Jobs(service)

    def complete(record, cancel):
        result = research_data(service, record)
        if record["index"] == 0:
            next(iter(manager.contexts.values())).halt("paused")
        return result

    calls = install_runner(complete)
    paused = manager.create(search["id"], "research", background=False)
    assert paused["status"] == "paused"
    assert len(calls) == 1
    assert (
        sum(
            opportunity["research_status"] == "complete"
            for opportunity in service.store.list("opportunities")
        )
        == 1
    )
    finished = manager.control(paused["id"], "resume", background=False)
    assert finished["status"] == "completed"
    assert len(calls) == finished["agent_calls"] == 2


def test_cancel_interrupts_active_call_and_retains_unreported_cost(
    make_service, install_runner
):
    service, search = make_service(settings={"max_cost_usd": 1})
    started = threading.Event()

    def wait_for_cancel(record, cancel):
        started.set()
        assert cancel.wait(2)
        raise ProviderError("cancelled", "Stopped.")

    calls = install_runner(wait_for_cancel)
    manager = Jobs(service)
    job = manager.create(search["id"], "research")
    assert started.wait(1)
    manager.control(job["id"], "cancel")
    manager.threads[job["id"]].join(timeout=2)
    assert not manager.threads[job["id"]].is_alive()
    final = service.store.get("runs", job["id"])
    assert final["status"] == "cancelled"
    assert final["cost_unreported"] == 1
    assert final["cost_unreported_calls"] == len(calls) == 1
    assert final["reserved_cost"] == final["active_calls"] == 0
    with pytest.raises(AppError):
        manager.control(job["id"], "resume")


def test_two_managers_cannot_resume_same_job_twice(make_service, monkeypatch):
    service, search = make_service()
    _, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id, lambda job: job.update(status="paused", worker_pid=None)
    )
    managers = [Jobs(Service(service.store.root)), Jobs(Service(service.store.root))]
    barrier, launches = threading.Barrier(2), []
    for manager in managers:
        monkeypatch.setattr(
            manager.service, "assert_idle", lambda search_id: barrier.wait(timeout=2)
        )
        monkeypatch.setattr(manager, "launch", lambda job_id: launches.append(job_id))

    def resume(manager):
        try:
            return manager.control(ctx.job_id, "resume")["status"]
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(resume, managers))
    assert sorted(states) == ["JOB-STATE", "queued"]
    assert launches == [ctx.job_id]


def test_dead_worker_recovery_preserves_reserved_allowance(make_service, monkeypatch):
    service, search = make_service()
    manager, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id,
        lambda job: job.update(
            status="running",
            worker_pid=99999999,
            active_calls=1,
            active_claude_calls=1,
            reserved_cost=1.5,
            cost_unreported=0.2,
            cost_unreported_calls=1,
        ),
    )

    def dead(pid, signal):
        raise ProcessLookupError()

    monkeypatch.setattr(pipeline.os, "kill", dead)
    manager.recover_interrupted()
    job = service.store.get("runs", ctx.job_id)
    assert job["status"] == "paused"
    assert job["cost_unreported"] == pytest.approx(1.7)
    assert job["cost_unreported_calls"] == 2
    assert job["active_calls"] == job["reserved_cost"] == 0


def test_resume_during_previous_thread_cleanup_does_not_orphan_job(
    make_service, install_runner
):
    service, search = make_service(opportunities=1)
    _, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id, lambda job: job.update(status="paused", worker_pid=None)
    )
    release = threading.Event()
    previous = threading.Thread(target=lambda: release.wait(2), daemon=True)
    previous.start()
    manager = Jobs(service)
    manager.threads[ctx.job_id] = previous
    calls = install_runner(lambda record, cancel: research_data(service, record))
    queued = manager.control(ctx.job_id, "resume")
    assert queued["status"] == "queued"
    assert calls == []
    release.set()
    manager.threads[ctx.job_id].join(timeout=2)
    assert service.store.get("runs", ctx.job_id)["status"] == "completed"
    assert len(calls) == 1


def test_two_managers_cannot_claim_one_queued_job_twice(make_service, install_runner):
    service, search = make_service(opportunities=1)
    _, ctx = queued_context(service, search)
    managers = [Jobs(Service(service.store.root)), Jobs(Service(service.store.root))]
    calls = install_runner(lambda record, cancel: research_data(service, record))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda manager: manager.run(ctx.job_id), managers))
    assert len(calls) == 1
    assert service.store.get("runs", ctx.job_id)["status"] == "completed"


def test_input_loading_failure_does_not_leave_permanently_running_job(
    make_service, monkeypatch
):
    service, search = make_service()

    def fail_context(*args):
        raise ValueError("Private failing input details")

    monkeypatch.setattr(pipeline, "Context", fail_context)
    job = Jobs(service).create(search["id"], "research", background=False)
    assert job["status"] == "failed"
    assert job["agent_calls"] == 0
    assert job["worker_pid"] is None
    assert "Private failing input" not in job["error"]


def profile_payload():
    return {
        "summary": "Manufacturing engineer with CAD experience.",
        "suggested_roles": ["Manufacturing Engineer"],
        "search_terms": ["manufacturing engineering"],
        "strengths": ["CAD"],
        "gaps": [],
        "unknowns": [],
    }


def sequence_payload(opportunity):
    source = opportunity["listing_url"] or opportunity["company_url"]
    return {
        "drafts": [
            {
                "step": step,
                "delay_days": days,
                "subject": "Engineering role",
                "body": body,
                "source_urls": [source],
                "candidate_claims": [],
            }
            for step, days, body in [
                (
                    1,
                    0,
                    "I saw your manufacturing role. Does process design sit with this team?",
                ),
                (
                    2,
                    5,
                    "Your team works on production processes. Is CAD experience useful for this opening?",
                ),
                (
                    3,
                    12,
                    "I will close the loop here. Who handles questions about this role?",
                ),
            ]
        ]
    }


def test_profile_reads_document_end_once_and_is_cached(make_service, install_runner):
    service, search = make_service(opportunities=0)
    tail = "CONSTRAINT: I cannot relocate outside Colorado."
    service.upload_document(
        search["id"],
        "resume.txt",
        ("Manufacturing project details. " * 500 + tail).encode(),
    )

    def complete(record, cancel):
        assert tail in record["prompt"]
        assert record["schema"] == "ProfileBrief" and record["web"] is False
        return AgentResult(profile_payload(), cost_usd=0.1)

    calls = install_runner(complete)
    manager = Jobs(service)
    first = manager.create(search["id"], "profile", background=False)
    second = manager.create(search["id"], "profile", background=False)
    assert first["status"] == second["status"] == "completed"
    assert first["agent_calls"] == 1 and second["agent_calls"] == 0
    assert service.store.get("searches", search["id"])["profile_fingerprint"]
    service.update_search(
        search["id"],
        {
            "settings": {
                "routes": {"profile": {"provider": "codex", "model": "profile-model"}}
            }
        },
    )
    third = manager.create(search["id"], "profile", background=False)
    assert third["status"] == "completed" and third["agent_calls"] == 1
    assert (
        calls[-1]["provider"] == "codex"
        and calls[-1]["options"]["model"] == "profile-model"
    )


@pytest.mark.parametrize(
    "change",
    ["objective", "preferences", "documents", "resume", "research_route", "pages"],
)
def test_standalone_draft_blocks_stale_research_and_pipeline_refreshes(
    make_service, install_runner, monkeypatch, change
):
    service, search = make_service(opportunities=1, settings={"target_results": 1})
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(
        pipeline,
        "fetch_public_text",
        lambda *args, **kwargs: (
            "Manufacturing Engineer Apply now team@company0.example.com"
        ),
    )

    def complete(record, cancel):
        if record["schema"] == "ProfileBrief":
            return AgentResult(profile_payload(), cost_usd=0.1)
        if record["schema"] == "SequenceResult":
            return AgentResult(
                sequence_payload(service.store.list("opportunities")[0]), cost_usd=0.1
            )
        return research_data(service, record)

    calls = install_runner(complete)
    manager = Jobs(service)
    assert (
        manager.create(search["id"], "research", background=False)["status"]
        == "completed"
    )
    if change == "documents":
        service.upload_document(
            search["id"], "scope.txt", b"I prefer work with small manufacturing teams."
        )
    else:
        patches = {
            "objective": {"objective": "Find process engineering roles."},
            "preferences": {"preferences": {"locations": ["Colorado"]}},
            "resume": {
                "candidate": {
                    "resume_text": "I have five years of experience as a manufacturing engineer using CAD and process design. I also know machining."
                }
            },
            "research_route": {
                "settings": {
                    "routes": {
                        "research": {"provider": "codex", "model": "new-research-model"}
                    }
                }
            },
            "pages": {"settings": {"research_pages": 6}},
        }
        service.update_search(search["id"], patches[change])
    stale = manager.create(search["id"], "draft", background=False)
    assert stale["status"] == "failed" and stale["agent_calls"] == 0
    assert len(calls) == 1
    assert any(
        "RESEARCH-STALE" in event["message"]
        for event in service.store.events(stale["id"])
    )
    refreshed = manager.create(search["id"], "pipeline", background=False)
    assert refreshed["status"] == "completed"
    assert refreshed["agent_calls"] == 3
    assert [call["schema"] for call in calls].count("ResearchResult") == 2
    assert len(service.store.list("opportunities")[0]["drafts"]) == 3


def test_mixed_routes_separate_claude_allowance_from_codex_reported_cost(
    make_service, install_runner
):
    service, search = make_service(
        settings={
            "provider": "codex",
            "model": "default-model",
            "max_cost_usd": 1,
            "routes": {
                "profile": {"provider": "claude", "model": "small-profile"},
                "outreach": {"provider": "claude", "model": "writer"},
            },
        }
    )
    _, ctx = queued_context(service, search)
    calls = install_runner(
        lambda record, cancel: AgentResult(
            {"ok": True}, cost_usd=99 if record["provider"] == "codex" else 0.2
        )
    )
    ctx.call("research", Payload, task="research")
    ctx.call("profile", Payload, task="profile")
    ctx.call("outreach", Payload, task="outreach")
    saved = service.store.get("runs", ctx.job_id)
    assert [call["provider"] for call in calls] == ["codex", "claude", "claude"]
    assert [call["options"]["model"] for call in calls] == [
        "default-model",
        "small-profile",
        "writer",
    ]
    assert [call["options"]["max_cost_usd"] for call in calls[1:]] == pytest.approx(
        [1, 0.8]
    )
    assert saved["cost_usd"] == pytest.approx(99.4) and saved[
        "claude_cost_usd"
    ] == pytest.approx(0.4)
    assert [row["task"] for row in saved["usage"]] == [
        "research",
        "profile",
        "outreach",
    ]


def test_unpriced_codex_does_not_consume_claude_allowance(make_service, install_runner):
    service, search = make_service(
        settings={
            "provider": "codex",
            "max_cost_usd": 1,
            "routes": {"outreach": {"provider": "claude"}},
        }
    )
    _, ctx = queued_context(service, search)
    calls = install_runner(lambda record, cancel: AgentResult({"ok": True}))
    ctx.call("research", Payload)
    ctx.call("research again", Payload)
    ctx.call("outreach", Payload, task="outreach")
    saved = service.store.get("runs", ctx.job_id)
    assert len(calls) == 3 and saved["cost_unreported"] == 1
    assert saved["cost_unreported_calls"] == 1 and saved["unpriced_calls"] == 3


def test_preflight_only_requires_providers_for_selected_stages(
    make_service, monkeypatch, install_runner
):
    service, search = make_service(
        settings={"provider": "codex", "routes": {"outreach": {"provider": "claude"}}}
    )
    monkeypatch.setattr(
        pipeline,
        "inspect_providers",
        lambda: [
            {"id": "codex", "available": True},
            {"id": "claude", "available": False, "message": "Install Claude."},
        ],
    )
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    assert (
        manager.create(search["id"], "research", background=False)["status"]
        == "completed"
    )
    with pytest.raises(AppError, match="Install Claude"):
        manager.create(search["id"], "pipeline", background=False)


def test_direct_ats_discovery_ranks_titles_before_retaining_target(
    make_service, monkeypatch
):
    service, search = make_service(
        opportunities=0,
        settings={"target_results": 1, "ats_boards": ["https://jobs.lever.co/example"]},
    )
    rows = [
        OpportunitySeed(
            company="Example",
            company_url="https://example.com",
            title=title,
            listing_url=f"https://jobs.lever.co/example/{index}",
            source_provider="lever",
            source_board="example",
            source_id=str(index),
            listing_source="ats",
            listing_status="open",
            last_checked_at=now(),
            source_urls=[f"https://jobs.lever.co/example/{index}"],
        ).model_dump()
        for index, title in enumerate(["Payroll Clerk", "Manufacturing Engineer"])
    ]
    monkeypatch.setattr(
        pipeline.sources,
        "fetch_board",
        lambda *args, **kwargs: {
            "opportunities": rows,
            "warnings": [],
            "complete": True,
            "requests": 1,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "AgentRunner",
        lambda *args, **kwargs: pytest.fail("Board met target without an agent"),
    )
    run = Jobs(service).create(search["id"], "discover", background=False)
    assert run["status"] == "completed" and run["agent_calls"] == 0
    assert service.store.list("opportunities")[0]["title"] == "Manufacturing Engineer"


def test_agent_discovery_cannot_claim_trusted_ats_provenance(
    make_service, install_runner
):
    service, search = make_service(opportunities=0, settings={"target_results": 1})
    row = OpportunitySeed(
        company="Example",
        company_url="https://example.com",
        title="Engineer",
        listing_url="https://example.com/jobs/1",
        source_provider="lever",
        source_board="fake",
        source_id="invented",
        listing_source="ats",
        listing_status="open",
        last_checked_at=now(),
        source_urls=["https://example.com/jobs/1"],
    ).model_dump()
    install_runner(
        lambda record, cancel: AgentResult(
            {"opportunities": [row], "coverage_notes": "Sourced result"}, cost_usd=0.1
        )
    )
    assert (
        Jobs(service).create(search["id"], "discover", background=False)["status"]
        == "completed"
    )
    saved = service.store.list("opportunities")[0]
    assert (
        saved["source_provider"] == "web"
        and saved["source_id"]
        == saved["source_board"]
        == saved["last_checked_at"]
        == ""
    )
    assert saved["listing_source"] != "ats" and saved["listing_status"] == "unverified"


@pytest.mark.parametrize(
    "text,status",
    [
        ("Welcome to our careers page", "unverified"),
        ("Manufacturing Engineer Apply now", "open"),
        ("Manufacturing Engineer This position is closed", "closed"),
    ],
)
def test_http_success_alone_never_proves_opening(
    make_service, install_runner, monkeypatch, text, status
):
    service, search = make_service(opportunities=1)
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    manager.create(search["id"], "research", background=False)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *args, **kwargs: text)
    assert (
        manager.create(search["id"], "verify", background=False)["status"]
        == "completed"
    )
    saved = service.store.list("opportunities")[0]
    assert saved["listing_status"] == status
    if status == "closed":
        assert saved["eligibility"] == "blocked"


def test_suppressed_email_does_not_remove_website_application(
    make_service, install_runner, monkeypatch
):
    service, search = make_service(opportunities=1)
    service.suppress(
        {"kind": "email", "value": "team@company0.example.com", "reason": "No contact"}
    )
    calls = install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    assert (
        manager.create(search["id"], "research", background=False)["status"]
        == "completed"
    )
    saved = service.store.list("opportunities")[0]
    assert len(calls) == 1 and saved["research_status"] == "complete"
    assert saved["status"] != "suppressed" and saved["email_status"] == "suppressed"


def test_research_rejects_different_requisition(make_service, install_runner):
    service, search = make_service(opportunities=1)
    row = service.store.list("opportunities")[0]
    row["requisition_id"] = "role-1"
    service.store.save("opportunities", row)

    def swapped(record, cancel):
        data = research_payload(row)
        data["requisition_id"] = "role-2"
        return AgentResult(data, cost_usd=0.1)

    install_runner(swapped)
    result = Jobs(service).create(search["id"], "research", background=False)
    assert result["status"] == "failed"
    assert service.store.list("opportunities")[0]["requisition_id"] == "role-1"


def test_distinct_roles_at_one_company_are_not_collapsed(make_service):
    service, search = make_service(opportunities=1)
    _, created = service.upsert_seed(
        search["id"],
        OpportunitySeed(
            company="Company 0",
            company_url="https://company0.example.com",
            title="Quality Engineer",
            listing_url="https://company0.example.com/jobs/quality",
            source_urls=["https://company0.example.com/jobs/quality"],
        ),
    )
    assert created and len(service.store.list("opportunities")) == 2


def test_demo_pipeline_stays_offline_with_five_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "AgentRunner",
        lambda *args, **kwargs: pytest.fail("No real agent for demo"),
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_public_text",
        lambda *args, **kwargs: pytest.fail("No web request for demo"),
    )
    monkeypatch.setattr(
        pipeline.sources,
        "fetch_board",
        lambda *args, **kwargs: pytest.fail("No ATS request for demo"),
    )
    service = Service(tmp_path)
    search = service.create_demo()
    run = Jobs(service).create(search["id"], background=False)
    assert run["status"] == "completed"
    assert run["stage_index"] == run["stage_count"] == 5
    assert run["cost_unreported_calls"] == 0
    assert all(row["provider"] == "demo" for row in run["usage"])
    assert service.store.get("searches", search["id"])["profile"]
    assert any(len(row["drafts"]) == 3 for row in service.store.list("opportunities"))


def test_report_uses_shared_stages_and_omits_outreach(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "AgentRunner", lambda *args, **kwargs: pytest.fail("No real agent for demo"))
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *args, **kwargs: pytest.fail("No web request for demo"))
    service = Service(tmp_path)
    search = service.create_demo()
    manager = Jobs(service)
    run = manager.create(search["id"], "report", background=False)
    assert run["status"] == "completed"
    assert run["stage_index"] == run["stage_count"] == 4
    assert run["current_stage"] == "verify"
    detail = service.search_detail(search["id"])
    assert detail["opportunities"]
    assert all(row["research_status"] == "complete" and not row["drafts"] for row in detail["opportunities"])
    assert service.export(search["id"], "csv").startswith(b"\xef\xbb\xbf")
    draft = manager.create(search["id"], "draft", background=False)
    assert draft["status"] == "completed"
    assert any(row["drafts"] for row in service.search_detail(search["id"])["opportunities"])


def test_report_preflight_does_not_require_outreach_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "inspect_providers", lambda: [{"id": "codex", "available": True}])
    service = Service(tmp_path)
    search = service.create_search({"name": "Report", "objective": "Engineering", "settings": {
        "provider": "codex", "routes": {"outreach": {"provider": "claude"}},
    }})
    manager = Jobs(service)
    manager.preflight(search, "report")
    with pytest.raises(pipeline.AppError, match="Set up claude"):
        manager.preflight(search, "pipeline")


def test_live_old_posting_and_today_deadline_are_not_expired():
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).date()
    opportunity = {
        "listing_status": "open",
        "posted_at": (today - timedelta(days=180)).isoformat(),
        "valid_through": today.isoformat(),
    }
    assert not pipeline.listing_expired(opportunity)
    opportunity["valid_through"] = (today - timedelta(days=1)).isoformat()
    assert pipeline.listing_expired(opportunity)


@pytest.mark.parametrize("verified,expected", [(False, "unknown"), (True, "verified")])
def test_unavailable_email_page_does_not_count_as_published_contact(
    make_service, install_runner, monkeypatch, verified, expected
):
    service, search = make_service(opportunities=1)
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    manager.create(search["id"], "research", background=False)
    if verified:
        row = service.store.list("opportunities")[0]
        row.update(
            email_status="verified",
            verification_source="Independent verifier",
            verification_at=now(),
        )
        service.store.save("opportunities", row)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")

    def public_page(url, **kwargs):
        if url.endswith("/contact"):
            raise pipeline.SourceUnavailable("Unavailable")
        return "Manufacturing Engineer Apply now"

    monkeypatch.setattr(pipeline, "fetch_public_text", public_page)
    assert (
        manager.create(search["id"], "verify", background=False)["status"]
        == "completed"
    )
    row = service.store.list("opportunities")[0]
    assert row["email_status"] == expected
    assert any("email page unavailable" in risk for risk in row["risks"])


def test_direct_ats_slug_can_resolve_company_and_retain_source_pay_dates(
    make_service, install_runner
):
    service, search = make_service(opportunities=0)
    source = "https://jobs.lever.co/acme-tools/role-1"
    row, _ = service.upsert_seed(
        search["id"],
        OpportunitySeed(
            company="acme-tools",
            title="Manufacturing Engineer",
            listing_url=source,
            apply_url=source,
            source_provider="lever",
            source_board="acme-tools",
            source_id="role-1",
            listing_source="ats",
            listing_status="open",
            last_checked_at=now(),
            salary_min=110000,
            salary_max=140000,
            salary_period="year",
            salary_currency="USD",
            salary_source=source,
            source_updated_at="2026-10-20",
            date_basis="Lever source update",
            source_urls=[source],
        ),
    )

    def complete(record, cancel):
        result = research_payload(row)
        result.update(
            company="Acme Tools Inc",
            company_url="https://acmetools.example.com",
            source_updated_at="",
            date_basis="",
            salary_min=None,
            salary_max=None,
            salary_period="unknown",
            salary_currency="",
            salary_source="",
        )
        return AgentResult(result, cost_usd=0.1)

    install_runner(complete)
    run = Jobs(service).create(search["id"], "research", background=False)
    assert run["status"] == "completed"
    saved = service.store.get("opportunities", row["id"])
    assert (
        saved["company"] == "Acme Tools Inc"
        and saved["domain"] == "acmetools.example.com"
    )
    assert saved["source_provider"] == "lever" and saved["source_id"] == "role-1"
    assert saved["salary_min"] == 110000 and saved["salary_max"] == 140000
    assert (
        saved["salary_period"] == "year" and saved["source_updated_at"] == "2026-10-20"
    )


def test_zero_overlap_board_jobs_leave_room_for_web_discovery(
    make_service, install_runner, monkeypatch
):
    service, search = make_service(
        opportunities=0,
        settings={"target_results": 1, "ats_boards": ["https://jobs.lever.co/example"]},
    )
    wrong = OpportunitySeed(
        company="Example",
        title="Payroll Clerk",
        listing_url="https://jobs.lever.co/example/payroll",
        source_provider="lever",
        source_board="example",
        source_id="payroll",
        source_urls=["https://jobs.lever.co/example/payroll"],
    ).model_dump()
    right = OpportunitySeed(
        company="Example",
        title="Manufacturing Engineer",
        company_url="https://example.com",
        listing_url="https://example.com/jobs/engineer",
        source_urls=["https://example.com/jobs/engineer"],
    ).model_dump()

    def board(url, *, limit, cancel):
        assert limit == 1000
        return {
            "opportunities": [wrong],
            "warnings": [],
            "complete": True,
            "requests": 1,
        }

    monkeypatch.setattr(pipeline.sources, "fetch_board", board)
    calls = install_runner(
        lambda record, cancel: AgentResult(
            {"opportunities": [right], "coverage_notes": "Relevant web result"},
            cost_usd=0.1,
        )
    )
    assert (
        Jobs(service).create(search["id"], "discover", background=False)["status"]
        == "completed"
    )
    assert (
        len(calls) == 1
        and service.store.list("opportunities")[0]["title"] == "Manufacturing Engineer"
    )


@pytest.mark.parametrize(
    "status,expected", [(404, "closed"), (410, "closed"), (403, "unverified")]
)
def test_listing_removed_is_closed_but_access_denied_is_unknown(
    make_service, install_runner, monkeypatch, status, expected
):
    service, search = make_service(opportunities=1)
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    manager.create(search["id"], "research", background=False)

    def unavailable(url, **kwargs):
        if "/jobs/" in url:
            raise pipeline.SourceUnavailable(
                "Source is unavailable", status_code=status
            )
        return "Company public contact"

    monkeypatch.setattr(pipeline, "fetch_public_text", unavailable)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    assert (
        manager.create(search["id"], "verify", background=False)["status"]
        == "completed"
    )
    assert service.store.list("opportunities")[0]["listing_status"] == expected


@pytest.mark.parametrize("matching,expected", [(True, "closed"), (False, "open")])
def test_only_matching_structured_posting_can_apply_expired_deadline(
    make_service, install_runner, monkeypatch, matching, expected
):
    service, search = make_service(opportunities=1)
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    manager.create(search["id"], "research", background=False)
    row = service.store.list("opportunities")[0]
    posting = {
        "listing_url": row["listing_url"],
        "listing_url_explicit": matching,
        "title": row["title"] if matching else "Unrelated Sales Role",
        "company": row["company"] if matching else "Another Employer",
        "valid_through": "2001-01-01",
        "date_basis": "JobPosting datePosted",
    }
    monkeypatch.setattr(
        pipeline.sources,
        "inspect_listing",
        lambda *args, **kwargs: {
            "text": "Manufacturing Engineer Apply now",
            "jobpostings": [posting],
            "listing_status": "closed",
            "status_basis": "All extracted JobPosting records have expired validThrough dates",
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        pipeline, "fetch_public_text", lambda *args, **kwargs: "Public contact"
    )
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    assert (
        manager.create(search["id"], "verify", background=False)["status"]
        == "completed"
    )
    saved = service.store.list("opportunities")[0]
    assert saved["listing_status"] == expected
    assert saved["valid_through"] == ("2001-01-01" if matching else "")


def test_listing_inspection_reuses_text_within_one_page_budget(
    make_service, install_runner, monkeypatch
):
    service, search = make_service(opportunities=1, settings={"research_pages": 1})
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    manager.create(search["id"], "research", background=False)
    calls = []

    def inspect(url, **kwargs):
        calls.append(url)
        return {
            "text": "Manufacturing Engineer Apply now",
            "jobpostings": [],
            "listing_status": "unverified",
            "warnings": [],
        }

    monkeypatch.setattr(pipeline.sources, "inspect_listing", inspect)
    monkeypatch.setattr(
        pipeline,
        "fetch_public_text",
        lambda *args, **kwargs: pytest.fail("No duplicate or additional page fetch"),
    )
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    assert (
        manager.create(search["id"], "verify", background=False)["status"]
        == "completed"
    )
    assert len(calls) == 1
    assert service.store.list("opportunities")[0]["email_status"] == "unknown"
