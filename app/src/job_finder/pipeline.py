"""Durable bounded job-search stages shared by the GUI and CLI.

Each agent task has its own provider/model route. Call and elapsed-time caps apply
to every provider; a separate conservative allowance applies only to Claude calls.
Completed work is saved before the next cancellation checkpoint.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from . import demo, prompts, sources
from .config import AppError
from .models import (
    DiscoveryResult,
    Opportunity,
    OpportunitySeed,
    ProfileBrief,
    ResearchResult,
    RunRequest,
    SequenceResult,
    canonical_url,
    domain_of,
    now,
    route_for,
    strict_schema,
)
from .providers import AgentResult, AgentRunner, ProviderError, inspect_providers
from .quality import sequence_issues
from .ranking import assess, candidate_claims_supported, deadline_passed
from .research import (
    SourceUnavailable,
    email_dns_status,
    email_syntax,
    fetch_public_text,
    normalized,
)
from .service import Service, fingerprint, ident

STAGES = ["profile", "discover", "research", "verify", "draft"]
TASKS = ["profile", "discovery", "research", "outreach"]
UNCONTACTED = {"new", "shortlisted"}


def parsed_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        value = value.strip().replace("Z", "+00:00")
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def fresh_check(value: str, hours: int = 24) -> bool:
    checked = parsed_time(value)
    return bool(
        checked
        and datetime.now(UTC) - timedelta(hours=hours)
        <= checked
        <= datetime.now(UTC) + timedelta(minutes=5)
    )


def listing_expired(opportunity: dict) -> bool:
    return opportunity.get("listing_status") == "closed" or deadline_passed(
        opportunity.get("valid_through", "")
    )


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#]+", value.casefold()) if len(t) > 2}


def matching_posting(opportunity: dict, postings: list[dict]) -> dict | None:
    target_urls = {
        canonical_url(opportunity[field])
        for field in ("listing_url", "apply_url")
        if opportunity[field]
    }
    matches = []
    for posting in postings:
        old_req, new_req = opportunity["requisition_id"], posting.get("requisition_id", "")
        if old_req and new_req and old_req.casefold() != new_req.casefold():
            continue
        url_match = bool(
            posting.get("listing_url_explicit")
            and posting.get("listing_url")
            and canonical_url(posting["listing_url"]) in target_urls
        )
        title_match = bool(
            opportunity["title"]
            and normalized(opportunity["title"]) == normalized(posting.get("title", ""))
        )
        employer_match = bool(
            normalized(opportunity["company"]) == normalized(posting.get("company", ""))
            or (
                domain_of(opportunity["company_url"])
                and domain_of(opportunity["company_url"])
                == domain_of(posting.get("company_url", ""))
            )
        )
        requisition_match = bool(old_req and new_req and old_req.casefold() == new_req.casefold())
        if url_match or (title_match and (employer_match or requisition_match)):
            if not any(fingerprint(existing) == fingerprint(posting) for existing in matches):
                matches.append(posting)
    # Conflicting matching records require human review, not an arbitrary choice.
    return matches[0] if len(matches) == 1 else None


class Stopped(Exception):
    pass


class BudgetBusy(Exception):
    """Capacity is held by work in progress, not yet settled."""


class Context:
    def __init__(self, service: Service, job_id: str):
        self.service, self.store, self.job_id = service, service.store, job_id
        job = self.store.get("runs", job_id)
        self.owner_token = job.get("worker_token")
        self.search = self.store.get("searches", job["search_id"])
        self.documents = self.store.documents(self.search["id"], include_text=True)
        self.document_fingerprint = [(d["sha256"], d["name"]) for d in self.documents]
        self.candidate_text = service.candidate_text(self.search["id"])
        self.prompt_search = self.search | {"candidate_evidence": self.candidate_text}
        self.settings = self.search["settings"]
        if self.search.get("profile_fingerprint") != self.profile_fingerprint():
            self.prompt_search.pop("profile", None)
        self.stop, self.finished = threading.Event(), threading.Event()
        self.started = time.monotonic()
        self.previous_elapsed = job.get("elapsed_seconds", 0)
        self.end_status, self.end_error, self.failures = "completed", "", 0
        self.lock = threading.Lock()
        self.budget_condition = threading.Condition()
        snapshot = fingerprint(
            {
                "objective": self.search["objective"],
                "candidate": self.search["candidate"],
                "preferences": self.search["preferences"],
                "documents": self.document_fingerprint,
                "routes": {task: route_for(self.settings, task) for task in TASKS},
                "research_pages": self.settings["research_pages"],
            }
        )
        if job.get("input_fingerprint") and job["input_fingerprint"] != snapshot:
            self.store.event(
                job_id,
                "Inputs changed. Relevant saved work will be refreshed; matching results remain cached.",
            )
        self.mutate(
            lambda j: j.update(
                input_fingerprint=snapshot,
                run_error_count=0,
                cost_limit_usd=self.settings["max_cost_usd"]
                if any(route_for(self.settings, task)["provider"] == "claude" for task in TASKS)
                else None,
            )
        )

    def mutate(self, update):
        def guarded(job):
            if self.owner_token and job.get("worker_token") != self.owner_token:
                raise Stopped()
            update(job)

        return self.store.mutate_job(self.job_id, guarded)

    def halt(self, status: str, error: str = ""):
        with self.lock:
            if not self.stop.is_set():
                self.end_status, self.end_error = status, error
                self.stop.set()
        with self.budget_condition:
            self.budget_condition.notify_all()

    def check(self):
        if (
            self.previous_elapsed + time.monotonic() - self.started
            >= self.settings["max_minutes"] * 60
        ):
            self.halt("budget_exhausted", "Time limit reached. Increase the limit and resume.")
        if self.stop.is_set():
            raise Stopped()

    def monitor(self):
        last_heartbeat = self.started
        while not self.finished.wait(0.15):
            job = self.store.get("runs", self.job_id)
            if self.owner_token and job.get("worker_token") != self.owner_token:
                self.halt("cancelled", "Worker ownership changed.")
                return
            action = job.get("requested_action")
            if action in {"pause", "cancel"}:
                self.halt("paused" if action == "pause" else "cancelled")
            if (
                self.previous_elapsed + time.monotonic() - self.started
                >= self.settings["max_minutes"] * 60
            ):
                self.halt("budget_exhausted", "Time limit reached. Increase the limit and resume.")
            if time.monotonic() - last_heartbeat >= 1:
                elapsed = round(self.previous_elapsed + time.monotonic() - self.started, 2)
                try:
                    self.mutate(
                        lambda j, elapsed=elapsed: j.update(
                            elapsed_seconds=elapsed, heartbeat_at=now()
                        )
                    )
                except Stopped:
                    return
                last_heartbeat = time.monotonic()

    def stage(self, stage: str, total: int):
        self.check()
        self.mutate(
            lambda j: j.update(
                current_stage=stage,
                completed=0,
                total=total,
                current_item="",
                stage_index=STAGES.index(stage) + 1 if j["stage"] in {"pipeline", "report"} else 1,
            )
        )
        self.store.event(
            self.job_id,
            {
                "profile": "Reading your goals and experience",
                "discover": "Finding roles and hiring signals",
                "research": "Researching each opportunity",
                "verify": "Checking listings, sources, and email domains",
                "draft": "Writing short outreach sequences",
            }[stage],
        )

    def progress(self, item: str = "", increment: int = 0):
        self.mutate(
            lambda j: j.update(
                completed=min(j["total"], j["completed"] + increment), current_item=item
            )
        )

    def record_failure(self):
        with self.lock:
            self.failures += 1
        self.mutate(
            lambda j: j.update(
                error_count=j.get("error_count", 0) + 1,
                run_error_count=j.get("run_error_count", 0) + 1,
            )
        )

    def profile_fingerprint(self) -> str:
        return fingerprint(
            [
                "candidate-profile-v1",
                self.search["objective"],
                self.search["candidate"],
                self.search["preferences"],
                self.document_fingerprint,
                route_for(self.settings, "profile"),
            ]
        )

    def research_fingerprint(self, opportunity: dict) -> str:
        return fingerprint(
            [
                "job-research-v1",
                self.search["objective"],
                self.search["candidate"],
                self.search["preferences"],
                self.document_fingerprint,
                {
                    k: opportunity.get(k)
                    for k in (
                        "kind",
                        "company",
                        "company_url",
                        "title",
                        "listing_url",
                        "apply_url",
                        "requisition_id",
                    )
                },
                route_for(self.settings, "research"),
                self.settings["research_pages"],
            ]
        )

    def reserve(self, provider: str) -> float:
        reservation = {"cost": 0.0}

        def update(job):
            active = job.get("active_calls", 0)
            if job["agent_calls"] >= self.settings["max_agent_calls"]:
                if active:
                    raise BudgetBusy()
                raise AppError("BUDGET-CALLS", "Agent-call limit reached. Increase it and resume.")
            if provider == "claude":
                left = (
                    self.settings["max_cost_usd"]
                    - job.get("claude_cost_usd", 0)
                    - job.get("cost_unreported", 0)
                    - job.get("reserved_cost", 0)
                )
                if left < 0.01:
                    if active:
                        raise BudgetBusy()
                    raise AppError(
                        "BUDGET-COST",
                        "Claude allowance is exhausted, including calls without reported cost. Review usage and increase the limit to resume.",
                    )
                reservation["cost"] = min(left, 2.0)
                job["reserved_cost"] = job.get("reserved_cost", 0) + reservation["cost"]
                job["active_claude_calls"] = job.get("active_claude_calls", 0) + 1
            job["active_calls"] = active + 1
            job["agent_calls"] += 1

        while True:
            self.check()
            with self.budget_condition:
                try:
                    self.mutate(update)
                    return reservation["cost"]
                except BudgetBusy:
                    self.budget_condition.wait(timeout=0.1)
                except AppError as exc:
                    self.halt("budget_exhausted", exc.message)
                    raise Stopped() from exc

    def settle(
        self,
        provider: str,
        task: str,
        model_name: str,
        reservation: float,
        result: AgentResult | None,
        uncertain: bool,
    ):
        def update(job):
            job["active_calls"] = max(0, job.get("active_calls", 0) - 1)
            if provider == "claude":
                job["active_claude_calls"] = max(0, job.get("active_claude_calls", 0) - 1)
                job["reserved_cost"] = max(0, job.get("reserved_cost", 0) - reservation)
            usage = {
                "task": task,
                "provider": provider,
                "model": model_name,
                "at": now(),
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
            }
            if result is not None:
                for field in ("input_tokens", "output_tokens", "cost_usd"):
                    value = getattr(result, field)
                    usage[field] = value
                    if value is not None:
                        job[field] = (job.get(field) or 0) + value
                if provider == "claude" and result.cost_usd is not None:
                    job["claude_cost_usd"] = job.get("claude_cost_usd", 0) + result.cost_usd
            if uncertain and (result is None or result.cost_usd is None):
                job["unpriced_calls"] = job.get("unpriced_calls", 0) + 1
                if provider == "claude":
                    job["cost_unreported"] = job.get("cost_unreported", 0) + reservation
                    job["cost_unreported_calls"] = job.get("cost_unreported_calls", 0) + 1
            job.setdefault("usage", []).append(usage)

        with self.budget_condition:
            self.mutate(update)
            self.budget_condition.notify_all()

    def call(
        self,
        prompt: str,
        model,
        *,
        task: str = "research",
        web: bool = True,
        demo_data: dict | None = None,
    ) -> dict:
        self.check()
        route = route_for(self.settings, task)
        provider, model_name = route["provider"], route["model"]
        for attempt in range(2):
            self.check()
            reservation = self.reserve(provider)
            result, uncertain = None, provider != "demo"
            try:
                if provider == "demo":
                    if self.stop.wait(0.12):
                        raise Stopped()
                    result = AgentResult(demo_data or {})
                else:
                    runner = AgentRunner(
                        provider,
                        model=model_name,
                        timeout=min(
                            self.settings["agent_timeout_seconds"],
                            max(
                                1,
                                self.settings["max_minutes"] * 60
                                - self.previous_elapsed
                                - (time.monotonic() - self.started),
                            ),
                        ),
                        max_turns=self.settings["max_turns"],
                        max_cost_usd=reservation or 1.0,
                    )
                    result = runner.run(prompt, strict_schema(model), web=web, cancel=self.stop)
                # Save a finished result even if Pause arrived immediately afterward.
                return model.model_validate(result.data).model_dump()
            except ValidationError as exc:
                if attempt == 0:
                    self.store.event(
                        self.job_id,
                        "Agent result did not match the data contract. Retrying once.",
                        "warning",
                    )
                else:
                    raise AppError(
                        "AGENT-SCHEMA", "Agent returned invalid structured data after one retry."
                    ) from exc
            except ProviderError as exc:
                if exc.code in {
                    "not_installed",
                    "unsupported_cli",
                    "native_executable_required",
                    "launch_failed",
                    "unsupported_provider",
                    "invalid_limits",
                    "input_limit",
                }:
                    uncertain = False
                if self.stop.is_set() or exc.code == "cancelled":
                    if not self.stop.is_set():
                        self.halt("cancelled")
                    raise Stopped() from exc
                if exc.code in {
                    "auth_required",
                    "not_installed",
                    "unsupported_cli",
                    "native_executable_required",
                    "rate_limited",
                    "launch_failed",
                    "unsupported_provider",
                    "invalid_limits",
                    "input_limit",
                }:
                    self.halt("failed", exc.message)
                    raise Stopped() from exc
                if attempt == 0 and exc.code in {
                    "network_error",
                    "invalid_response",
                    "missing_result",
                }:
                    self.store.event(
                        self.job_id,
                        "Agent request failed. Retrying once within the call limit.",
                        "warning",
                    )
                else:
                    raise AppError(
                        "AGENT-" + exc.code.upper().replace("_", "-"), exc.message
                    ) from exc
            finally:
                self.settle(provider, task, model_name, reservation, result, uncertain)
        raise AppError("AGENT-OUTPUT", "No usable agent result.")


class Jobs:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store
        self.threads: dict[str, threading.Thread] = {}
        self.contexts: dict[str, Context] = {}
        self.guard = threading.Lock()

    def recover_interrupted(self):
        for job in self.store.list("runs"):
            if job["status"] not in {"running", "queued"}:
                continue
            pid = job.get("worker_pid")
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except PermissionError:
                    alive = True
                except (ProcessLookupError, OSError):
                    pass
            if not alive:
                recovered = []

                def recover(current, pid=pid, job=job, recovered=recovered):
                    if (
                        current["status"] not in {"running", "queued"}
                        or current.get("worker_pid") != pid
                        or current.get("worker_token") != job.get("worker_token")
                    ):
                        return
                    # A dead worker cannot report what its last request cost.
                    # Preserve its allowance rather than spending it twice.
                    current["cost_unreported"] = current.get("cost_unreported", 0) + current.get(
                        "reserved_cost", 0
                    )
                    current["cost_unreported_calls"] = current.get(
                        "cost_unreported_calls", 0
                    ) + current.get("active_claude_calls", 0)
                    current.update(
                        status="paused",
                        requested_action="",
                        reserved_cost=0,
                        active_calls=0,
                        active_claude_calls=0,
                        worker_pid=None,
                        worker_token=None,
                        current_item="Interrupted. Resume to continue.",
                        error="Previous worker stopped; completed work is saved. Unreported calls retain their cost allowance.",
                    )
                    recovered.append(True)

                self.store.mutate_job(job["id"], recover)
                if recovered:
                    self.store.event(
                        job["id"],
                        "Interrupted worker recovered. Resume continues from saved results; unreported cost remains reserved in the used allowance.",
                        "warning",
                    )

    def preflight(self, search: dict, stage: str):
        tasks = {
            "report": ["profile", "discovery", "research"],
            "pipeline": ["profile", "discovery", "research", "outreach"],
            "profile": ["profile"],
            "discover": ["discovery"],
            "research": ["research"],
            "draft": ["outreach"],
            "verify": [],
        }[stage]
        required = {route_for(search["settings"], task)["provider"] for task in tasks} - {"demo"}
        if not required:
            return
        providers = {p["id"]: p for p in inspect_providers()}
        for name in sorted(required):
            provider = providers.get(name)
            if not provider or not provider.get("available"):
                raise AppError(
                    "PROVIDER-SETUP",
                    (provider or {}).get("message") or f"Set up {name} before this stage.",
                )

    def create(self, search_id: str, stage: str = "pipeline", *, background: bool = True) -> dict:
        RunRequest(stage=stage)
        search = self.store.get("searches", search_id)
        self.service.assert_idle(search_id)
        if stage in {"report", "pipeline", "profile", "discover", "research"} and not (
            search["objective"]
            or search["preferences"]["roles"]
            or search["candidate"]["resume_text"]
            or search["candidate"]["skills"]
            or self.store.documents(search_id)
        ):
            raise AppError(
                "SEARCH-EMPTY", "Write an objective, add target roles, or upload a résumé."
            )
        self.preflight(search, stage)
        claude = any(route_for(search["settings"], task)["provider"] == "claude" for task in TASKS)
        job = {
            "id": ident(),
            "search_id": search_id,
            "stage": stage,
            "status": "queued",
            "current_stage": "",
            "current_item": "Waiting",
            "completed": 0,
            "total": 0,
            "stage_index": 0,
            "stage_count": len(STAGES) if stage == "pipeline" else len(STAGES) - 1 if stage == "report" else 1,
            "agent_calls": 0,
            "max_agent_calls": search["settings"]["max_agent_calls"],
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "claude_cost_usd": 0.0,
            "cost_unreported": 0.0,
            "cost_unreported_calls": 0,
            "unpriced_calls": 0,
            "usage": [],
            "cost_limit_usd": search["settings"]["max_cost_usd"] if claude else None,
            "reserved_cost": 0,
            "active_calls": 0,
            "active_claude_calls": 0,
            "error": "",
            "error_count": 0,
            "run_error_count": 0,
            "elapsed_seconds": 0,
            "requested_action": "",
            "worker_pid": os.getpid(),
            "worker_token": None,
            "created_at": now(),
            "updated_at": now(),
        }
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM runs WHERE status IN ('running','queued')"
                    ).fetchone()[0]
                    >= 2
                ):
                    raise AppError(
                        "JOB-LIMIT", "Two searches are already active. Pause one first.", 409
                    )
                self.store.save_in(db, "runs", job)
        except sqlite3.IntegrityError as exc:
            raise AppError("JOB-ACTIVE", "This search already has an active run.", 409) from exc
        if background:
            self.launch(job["id"])
        else:
            self.run(job["id"])
        return self.store.get("runs", job["id"])

    def launch(self, job_id: str):
        with self.guard:
            previous = self.threads.get(job_id)
            if (
                previous
                and previous.is_alive()
                and self.store.get("runs", job_id)["status"] != "queued"
            ):
                raise AppError("JOB-ACTIVE", "This job is already running.", 409)

            def start_after_cleanup():
                # Status becomes resumable just before the old thread exits.
                # A rapid Resume must not orphan the newly queued row or let
                # old cleanup remove the replacement worker's context.
                if previous and previous.is_alive():
                    previous.join()
                self.run(job_id)

            thread = threading.Thread(
                target=start_after_cleanup, daemon=True, name="job-finder-" + job_id
            )
            self.threads[job_id] = thread
            thread.start()

    def control(self, job_id: str, action: str, *, background: bool = True) -> dict:
        job = self.store.get("runs", job_id)
        if action == "resume":
            if job["status"] not in {"paused", "failed", "budget_exhausted"}:
                raise AppError(
                    "JOB-STATE", "Only paused, failed, or budget-limited jobs can resume.", 409
                )
            self.service.assert_idle(job["search_id"])
            search = self.store.get("searches", job["search_id"])
            self.preflight(search, job["stage"])

            def resume(current):
                # Recheck inside the write transaction. Two CLI processes may
                # both have observed the same paused row before either writes.
                if current["status"] not in {"paused", "failed", "budget_exhausted"}:
                    raise AppError("JOB-STATE", "This job has already resumed.", 409)
                if sum(j["status"] in {"running", "queued"} for j in self.store.list("runs")) >= 2:
                    raise AppError(
                        "JOB-LIMIT", "Two searches are already active. Pause one first.", 409
                    )
                current.update(
                    status="queued",
                    requested_action="",
                    error="",
                    max_agent_calls=search["settings"]["max_agent_calls"],
                    worker_pid=os.getpid(),
                    worker_token=None,
                )

            try:
                self.store.mutate_job(job_id, resume)
            except sqlite3.IntegrityError as exc:
                raise AppError("JOB-ACTIVE", "Another job is active for this search.", 409) from exc
            if background:
                self.launch(job_id)
            else:
                self.run(job_id)
        elif action in {"pause", "cancel"}:
            if job["status"] in {"running", "queued"}:

                def request_action(current):
                    if current["status"] in {"running", "queued"}:
                        current.update(
                            requested_action=action,
                            current_item="Pausing…" if action == "pause" else "Stopping…",
                        )

                self.store.mutate_job(job_id, request_action)
                context = self.contexts.get(job_id)
                if context:
                    context.halt("paused" if action == "pause" else "cancelled")
            elif action == "cancel" and job["status"] in {"paused", "failed", "budget_exhausted"}:

                def cancel_saved(current):
                    if current["status"] in {"paused", "failed", "budget_exhausted"}:
                        current.update(status="cancelled", requested_action="")
                    elif current["status"] in {"running", "queued"}:
                        current.update(requested_action="cancel", current_item="Stopping…")

                self.store.mutate_job(job_id, cancel_saved)
            else:
                raise AppError("JOB-STATE", "This job is not running.", 409)
        else:
            raise AppError("JOB-ACTION", "Choose pause, resume, or cancel.")
        return self.store.get("runs", job_id)

    def shutdown(self):
        for job_id, context in list(self.contexts.items()):
            self.store.mutate_job(job_id, lambda j: j.update(requested_action="pause"))
            context.halt("paused")
        for thread in list(self.threads.values()):
            thread.join(timeout=12)

    def run(self, job_id: str):
        # Claim under a transaction so a repeated CLI/UI resume cannot run twice.
        def claim(job):
            if job["status"] != "queued":
                raise AppError("JOB-ACTIVE", "This job is already claimed.", 409)
            job.update(
                status="running", worker_pid=os.getpid(), worker_token=ident(), heartbeat_at=now()
            )

        try:
            claimed = self.store.mutate_job(job_id, claim)
        except AppError as exc:
            if exc.code == "JOB-ACTIVE":
                return  # Another launcher already claimed the same row.
            raise
        try:
            context = Context(self.service, job_id)
        except Exception:

            def fail_initialization(current):
                if current.get("worker_token") == claimed.get("worker_token"):
                    current.update(
                        status="failed",
                        worker_pid=None,
                        worker_token=None,
                        error="Could not load job inputs. Check the saved search and resume.",
                        current_item="Needs attention",
                    )

            self.store.mutate_job(job_id, fail_initialization)
            self.store.event(
                job_id,
                "JOB-INPUTS: Could not load saved inputs. No agent request was started.",
                "error",
            )
            return
        self.contexts[job_id] = context
        monitor = threading.Thread(target=context.monitor, daemon=True)
        monitor.start()
        self.store.event(job_id, "Job started. Results are saved after each opportunity.")
        job = self.store.get("runs", job_id)
        try:
            if job.get("requested_action") in {"pause", "cancel"}:
                context.halt("paused" if job["requested_action"] == "pause" else "cancelled")
            stages = STAGES if job["stage"] == "pipeline" else STAGES[:-1] if job["stage"] == "report" else [job["stage"]]
            for stage in stages:
                context.check()
                if stage == "profile":
                    self.profile(context)
                elif stage == "discover":
                    self.discover(context)
                else:
                    self.opportunities(context, stage)
            context.check()
            if context.failures:
                context.halt(
                    "failed",
                    f"{context.failures} opportunity steps need attention. Resume retries incomplete work and keeps saved results.",
                )
        except Stopped:
            pass
        except AppError as exc:
            context.halt("failed", exc.message)
            self.store.event(job_id, exc.code + ": " + exc.message, "error")
        except Exception:
            context.halt(
                "failed",
                "The job stopped unexpectedly. Saved results are intact; retry this stage.",
            )
            self.store.event(
                job_id, "JOB-ERROR: Unexpected failure. Saved results are intact.", "error"
            )
        except BaseException:
            context.halt("paused", "Worker interrupted. Completed results are saved.")
            raise
        finally:
            context.finished.set()
            monitor.join(timeout=1)
            elapsed = round(context.previous_elapsed + time.monotonic() - context.started, 2)

            def finish(current):
                if current.get("worker_token") != context.owner_token:
                    return
                current["cost_unreported"] = current.get("cost_unreported", 0) + current.get(
                    "reserved_cost", 0
                )
                current["cost_unreported_calls"] = current.get(
                    "cost_unreported_calls", 0
                ) + current.get("active_claude_calls", 0)
                current.update(
                    status=context.end_status,
                    error=context.end_error,
                    elapsed_seconds=elapsed,
                    requested_action="",
                    reserved_cost=0,
                    active_calls=0,
                    active_claude_calls=0,
                    worker_pid=None,
                    worker_token=None,
                    current_item={
                        "completed": "Complete",
                        "paused": "Paused",
                        "cancelled": "Cancelled",
                        "budget_exhausted": "Limit reached",
                        "failed": "Needs attention",
                    }[context.end_status],
                )

            self.store.mutate_job(job_id, finish)
            self.store.event(
                job_id,
                context.end_error
                or (
                    "Completed with saved results."
                    if context.end_status == "completed"
                    else context.end_status.capitalize()
                ),
            )
            self.contexts.pop(job_id, None)

    def profile(self, ctx: Context):
        ctx.stage("profile", 1)
        key = ctx.profile_fingerprint()
        profile = ctx.search.get("profile")
        if not profile or ctx.search.get("profile_fingerprint") != key:
            ctx.progress("Reading résumé and preferences")
            profile = ctx.call(
                prompts.profile(ctx.search, ctx.documents),
                ProfileBrief,
                task="profile",
                web=False,
                demo_data=demo.profile_result(ctx.search) if ctx.search["demo"] else None,
            )
            latest = self.store.get("searches", ctx.search["id"])
            latest.update(profile=profile, profile_fingerprint=key, updated_at=now())
            self.store.save("searches", latest)
            ctx.search.update(profile=profile, profile_fingerprint=key)
        ctx.prompt_search.update(profile=profile, profile_fingerprint=key)
        ctx.progress(increment=1)

    def _retain(
        self, ctx: Context, rows: list[dict], remaining: int, *, authoritative: bool = False
    ) -> int:
        added = 0
        for row in rows:
            if added >= remaining:
                break
            row = dict(row)
            if not authoritative:
                row.update(
                    source_provider="web",
                    source_board="",
                    source_id="",
                    last_checked_at="",
                    listing_status="unverified",
                )
                if row.get("listing_source") == "ats":
                    row["listing_source"] = "unknown"
            if (
                row.get("kind") == "company_lead"
                and not ctx.search["preferences"]["include_company_leads"]
            ):
                continue
            if not row.get("source_urls"):
                continue
            if not row.get("email_source"):
                row["email"] = ""
            if not row.get("contact_source"):
                row["contact_name"] = row["contact_role"] = ""
            _, created = self.service.upsert_seed(
                ctx.search["id"], OpportunitySeed.model_validate(row)
            )
            added += int(created)
        return added

    def discover(self, ctx: Context):
        plan = prompts.queries(ctx.prompt_search)
        boards = [] if ctx.search["demo"] else ctx.settings["ats_boards"]
        ctx.stage("discover", len(boards) + len(plan))
        profile = ctx.search.get("profile") or {}
        wanted = _tokens(
            " ".join(
                ctx.search["preferences"]["roles"]
                + ctx.search["candidate"]["skills"]
                + profile.get("suggested_roles", [])
            )
        )
        for board in boards:
            ctx.check()
            remaining = ctx.settings["target_results"] - len(
                self.store.list("opportunities", ctx.search["id"])
            )
            if remaining <= 0:
                break
            key = fingerprint(
                [
                    "ats-v1",
                    board,
                    ctx.search["objective"],
                    ctx.search["preferences"],
                    ctx.document_fingerprint,
                ]
            )
            if self.store.done(ctx.job_id, "discover", key):
                ctx.progress(increment=1)
                continue
            ctx.progress("Reading employer job board")
            try:
                result = sources.fetch_board(board, limit=1000, cancel=ctx.stop)
                rows = (
                    result.opportunities
                    if hasattr(result, "opportunities")
                    else result["opportunities"]
                )
                rows = [row.model_dump() if hasattr(row, "model_dump") else row for row in rows]
                scored = [
                    (
                        len(
                            wanted
                            & _tokens(
                                row.get("title", "") + " " + " ".join(row.get("requirements", []))
                            )
                        ),
                        row,
                    )
                    for row in rows
                ]
                scored.sort(key=lambda pair: -pair[0])
                rows = [row for overlap, row in scored if not wanted or overlap > 0]
                omitted = len(scored) - len(rows)
                if omitted:
                    self.store.event(
                        ctx.job_id,
                        f"Skipped {omitted} board openings without target-role or skill overlap.",
                    )
                added = self._retain(ctx, rows, remaining, authoritative=True)
                warnings = (
                    result.warnings if hasattr(result, "warnings") else result.get("warnings", [])
                )
                complete = (
                    result.complete if hasattr(result, "complete") else result.get("complete", True)
                )
                if complete:
                    self.store.checkpoint(ctx.job_id, "discover", key)
                self.store.event(ctx.job_id, f"Added {added} opportunities from an employer board.")
                for warning in warnings:
                    self.store.event(ctx.job_id, str(warning), "warning")
            except SourceUnavailable as exc:
                if ctx.stop.is_set():
                    raise Stopped() from exc
                self.store.event(
                    ctx.job_id,
                    "An employer board could not be read. Web discovery will continue.",
                    "warning",
                )
            ctx.progress(increment=1)
        empty_batches = 0
        for batch_index, query in enumerate(plan, 1):
            ctx.check()
            existing = self.store.list("opportunities", ctx.search["id"])
            remaining = ctx.settings["target_results"] - len(existing)
            if remaining <= 0:
                self.store.event(ctx.job_id, "Discovery target reached.")
                break
            query_key = fingerprint(
                [
                    ctx.search["objective"],
                    ctx.search["preferences"],
                    ctx.document_fingerprint,
                    route_for(ctx.settings, "discovery"),
                    query,
                ]
            )
            if self.store.done(ctx.job_id, "discover", query_key):
                ctx.progress(increment=1)
                continue
            ctx.progress("Discovery batch " + str(batch_index))
            known = [
                item.get("listing_url") or item.get("company_url") or item["identity"]
                for item in existing
            ]
            result = ctx.call(
                prompts.discovery(ctx.prompt_search, query, known, min(15, remaining)),
                DiscoveryResult,
                task="discovery",
                demo_data=demo.discovery_result(min(15, remaining), known)
                if ctx.search["demo"]
                else None,
            )
            added = self._retain(
                ctx, result["opportunities"], remaining, authoritative=ctx.search["demo"]
            )
            self.store.checkpoint(ctx.job_id, "discover", query_key)
            self.store.event(
                ctx.job_id, f"Added {added} opportunities. " + result["coverage_notes"][:650]
            )
            ctx.progress(increment=1)
            empty_batches = empty_batches + 1 if added == 0 else 0
            if empty_batches >= 2:
                self.store.event(
                    ctx.job_id,
                    "Two batches found no new opportunities. Discovery stopped to preserve budget.",
                    "warning",
                )
                break
        count = len(self.store.list("opportunities", ctx.search["id"]))
        if count < ctx.settings["target_results"]:
            self.store.event(
                ctx.job_id,
                f"Found {count} of {ctx.settings['target_results']} requested opportunities. Try a wider search or more discovery queries.",
                "warning",
            )

    def opportunities(self, ctx: Context, stage: str):
        rows = [
            row
            for row in self.store.list("opportunities", ctx.search["id"])
            if row["status"] not in {"rejected", "suppressed"}
            and not self.service.is_suppressed(row)
        ]
        if stage == "draft":
            if any(
                row["application_status"] in UNCONTACTED
                and (
                    (
                        row["research_status"] == "complete"
                        and row["research_fingerprint"] != ctx.research_fingerprint(row)
                    )
                    or (
                        row["research_status"] == "pending"
                        and (row["research_summary"] or row["evidence"])
                    )
                )
                for row in rows
            ):
                raise AppError(
                    "RESEARCH-STALE",
                    "Research inputs changed. Run Research again before Draft, or run the full pipeline.",
                )
            # Recompute deterministic scores so changed weights/elapsed time cannot
            # turn an old score into an unnoticed drafting decision.
            self.service.refresh_scores(ctx.search["id"])
            rows = [self.store.get("opportunities", row["id"]) for row in rows]
            rows = [
                row
                for row in rows
                if row["research_status"] == "complete"
                and row["fit_score"] >= ctx.settings["min_fit"]
                and row["eligibility"] != "blocked"
                and row["application_status"] in UNCONTACTED
                and not self.service.is_email_suppressed(row)
                and not listing_expired(row)
                and (ctx.search["demo"] or fresh_check(row["last_checked_at"]))
            ]
        ctx.stage(stage, len(rows))

        def process(row):
            label = row["company"] + (" · " + row["title"] if row["title"] else "")
            try:
                ctx.check()
                ctx.progress(label)
                getattr(self, stage)(ctx, row)
                ctx.check()
                ctx.progress(label, increment=1)
            except Stopped:
                return
            except (AppError, ValidationError) as exc:
                if stage == "research":
                    latest = self.store.get("opportunities", row["id"])
                    latest.update(research_status="error", updated_at=now())
                    self.store.save("opportunities", latest)
                message = (
                    exc.message
                    if isinstance(exc, AppError)
                    else "Agent result did not match the expected record."
                )
                self.store.event(ctx.job_id, label + ": " + message, "error")
                ctx.record_failure()
                ctx.progress(label, increment=1)

        with ThreadPoolExecutor(max_workers=ctx.settings["concurrency"]) as pool:
            futures = [pool.submit(process, row) for row in rows]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                ctx.halt(
                    "failed", "An opportunity step stopped unexpectedly. Completed work is saved."
                )
                for future in futures:
                    future.cancel()
                raise
        ctx.check()

    def research(self, ctx: Context, opportunity: dict):
        key = ctx.research_fingerprint(opportunity)
        if (
            opportunity["research_status"] == "complete"
            and opportunity["research_fingerprint"] == key
            and fresh_check(opportunity["last_checked_at"])
        ):
            return
        direct_ats = opportunity["source_provider"] in {"greenhouse", "lever", "ashby"}
        direct_fresh = (
            direct_ats
            and opportunity["listing_status"] == "open"
            and fresh_check(opportunity["last_checked_at"])
        )
        result = ctx.call(
            prompts.research(ctx.prompt_search, opportunity),
            ResearchResult,
            task="research",
            demo_data=demo.research_result(opportunity) if ctx.search["demo"] else None,
        )
        # Do not discard a known identity field merely because a response omitted it.
        for field in ("company_url", "listing_url", "apply_url", "title", "requisition_id"):
            if not result[field]:
                result[field] = opportunity[field]
        # Source-adapter credentials are application provenance, never model claims.
        for field in ("source_provider", "source_board", "source_id"):
            result[field] = opportunity[field]
        if direct_ats:
            result["listing_source"] = "ats"
            for field in (
                "posted_at",
                "source_updated_at",
                "date_basis",
                "valid_through",
                "salary_min",
                "salary_max",
                "salary_currency",
                "salary_period",
                "salary_source",
            ):
                if result[field] in ("", None, "unknown"):
                    result[field] = opportunity[field]
            if direct_fresh and result["listing_status"] != "closed":
                result["listing_status"] = "open"
        elif result["listing_source"] == "ats":
            result["listing_source"] = "unknown"
        if result["kind"] != opportunity["kind"]:
            raise AppError(
                "RESEARCH-IDENTITY",
                "Keep company leads and specific openings separate. Add a newly found vacancy as its own row.",
            )
        old_domain, new_domain = (
            domain_of(opportunity["company_url"]),
            domain_of(result["company_url"]),
        )
        if old_domain and new_domain and old_domain != new_domain:
            raise AppError(
                "RESEARCH-IDENTITY",
                "Research resolved to a different company domain. Review this opportunity.",
            )
        adapter_placeholder = (
            direct_ats
            and not old_domain
            and opportunity["source_board"]
            and opportunity["source_id"]
            and normalized(opportunity["company"])
            == normalized(opportunity["source_board"].split("/")[-1])
            and opportunity["listing_url"]
            and canonical_url(opportunity["listing_url"]) == canonical_url(result["listing_url"])
        )
        if normalized(opportunity["company"]) != normalized(result["company"]) and not (
            (old_domain and old_domain == new_domain) or adapter_placeholder
        ):
            raise AppError(
                "RESEARCH-IDENTITY",
                "Research resolved to a different company. Review this opportunity.",
            )
        if opportunity["kind"] == "open_role":
            before, after = opportunity["requisition_id"], result["requisition_id"]
            if before and after and before.casefold() != after.casefold():
                raise AppError(
                    "RESEARCH-IDENTITY",
                    "Research changed the requisition. Keep separate openings as separate rows.",
                )
            old_title, new_title = _tokens(opportunity["title"]), _tokens(result["title"])
            changed_url = (
                opportunity["listing_url"]
                and result["listing_url"]
                and canonical_url(opportunity["listing_url"])
                != canonical_url(result["listing_url"])
            )
            if result["kind"] != "open_role" or (
                changed_url and old_title and len(old_title & new_title) / len(old_title) < 0.6
            ):
                raise AppError(
                    "RESEARCH-IDENTITY",
                    "Research resolved to a different role. Review its original listing.",
                )
        urls = {e["url"] for e in result["evidence"]}
        if not result["email_source"]:
            result["email"] = ""
        if not result["contact_source"]:
            result["contact_name"] = result["contact_role"] = ""
        result["email"] = result["email"].casefold()
        if result["email"] and not email_syntax(result["email"]):
            result["email"] = result["email_source"] = ""
        # A source quote is necessary for a candidate claim; the model's own
        # summary is never accepted as evidence of experience.
        result["candidate_claims"] = candidate_claims_supported(result, ctx.candidate_text)
        previous_email = opportunity["email"]
        opportunity.update(result)
        opportunity["domain"] = domain_of(opportunity["company_url"])
        opportunity["source_urls"] = list(dict.fromkeys(result["source_urls"] + sorted(urls)))[:25]
        for evidence in opportunity["evidence"]:
            evidence["checked_at"] = now()
        opportunity.update(
            research_status="complete",
            research_fingerprint=ctx.research_fingerprint(opportunity),
            draft_fingerprint="",
            drafts=[],
            qa_issues=[],
            status="researched",
            updated_at=now(),
            last_checked_at=now(),
            source_check="matched" if direct_fresh else "unverified",
        )
        opportunity.update(assess(ctx.search, opportunity, ctx.candidate_text))
        if previous_email != opportunity["email"] or opportunity["email_status"] != "verified":
            opportunity.update(
                email_status="published"
                if opportunity["email"] and opportunity["email_source"]
                else "missing",
                verification_source="",
                verification_at="",
            )
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        self.store.save("opportunities", Opportunity.model_validate(opportunity).model_dump())
        self.store.event(
            ctx.job_id, f"Researched {opportunity['company']} · fit {opportunity['fit_score']}/100"
        )

    def verify(self, ctx: Context, opportunity: dict):
        key = fingerprint(
            [
                opportunity["email"],
                opportunity["email_source"],
                opportunity["research_fingerprint"],
                opportunity["evidence"],
                opportunity["listing_url"],
                now()[:10],
            ]
        )
        if self.store.done(ctx.job_id, "verify", opportunity["id"] + key):
            return
        if ctx.search["demo"]:
            opportunity.update(
                email_status="published" if opportunity["email"] else "missing",
                email_checked_at="",
                last_checked_at=now(),
            )
            opportunity.update(assess(ctx.search, opportunity, ctx.candidate_text))
            self.store.save("opportunities", opportunity)
            self.store.checkpoint(ctx.job_id, "verify", opportunity["id"] + key)
            return
        ctx.check()
        if self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        elif opportunity["email_status"] != "verified":
            opportunity["email_status"] = email_dns_status(opportunity["email"])
        opportunity["email_checked_at"] = now()
        source_urls = list(
            dict.fromkeys(
                [
                    opportunity["listing_url"],
                    opportunity["apply_url"],
                    opportunity["email_source"],
                    opportunity["company_url"],
                ]
                + [e["url"] for e in opportunity["evidence"]]
            )
        )
        checked, inspection = {}, None
        listing_url = opportunity["listing_url"] or opportunity["apply_url"]
        for url in [url for url in source_urls if url][: ctx.settings["research_pages"]]:
            ctx.check()
            if opportunity["kind"] == "open_role" and url == listing_url:
                inspection = sources.inspect_listing(url, cancel=ctx.stop)
                checked[url] = inspection.get("text") or None
            else:
                try:
                    checked[url] = fetch_public_text(url, cancel=ctx.stop)
                except SourceUnavailable:
                    checked[url] = None
        risks = [risk for risk in opportunity["risks"] if not risk.startswith("Source check:")]
        if inspection:
            posting = matching_posting(opportunity, inspection.get("jobpostings", []))
            if posting:
                for field in (
                    "posted_at",
                    "valid_through",
                    "date_basis",
                    "location",
                    "remote_eligibility",
                    "work_mode",
                    "employment_type",
                    "salary_min",
                    "salary_max",
                    "salary_currency",
                    "salary_period",
                    "salary_source",
                ):
                    if posting.get(field) not in ("", None, "unknown"):
                        opportunity[field] = posting[field]
            elif inspection.get("jobpostings"):
                risks.append(
                    "Source check: structured posting metadata did not identify one matching role; review it manually."
                )
            for warning in inspection.get("warnings", []):
                risks.append("Source check: " + str(warning)[:300])
        if opportunity["email"] and opportunity["email_source"]:
            text = checked.get(opportunity["email_source"])
            if text is None:
                risks.append(
                    "Source check: email page unavailable or outside the page limit; review the address manually."
                )
                if opportunity["email_status"] != "verified":
                    opportunity["email_status"] = "unknown"
            elif opportunity["email"].casefold() not in normalized(text):
                risks.append("Source check: exact email was not found on the cited page.")
                if opportunity["email_status"] != "verified":
                    opportunity["email_status"] = "unknown"
        authoritative_open = (
            opportunity["source_provider"] in {"greenhouse", "lever", "ashby"}
            and opportunity["listing_status"] == "open"
            and opportunity["source_check"] == "matched"
            and fresh_check(opportunity["last_checked_at"])
        )
        matches = sum(
            bool(
                checked.get(e["url"])
                and e["quote"]
                and normalized(e["quote"]) in normalized(checked[e["url"]])
            )
            for e in opportunity["evidence"]
        )
        opportunity["source_check"] = (
            "matched" if matches else "reachable" if any(checked.values()) else "unavailable"
        )
        if opportunity["evidence"]:
            risks.append(
                f"Source check: {matches}/{len(opportunity['evidence'])} evidence excerpts independently matched. Other excerpts need review."
            )
        if opportunity["kind"] == "open_role":
            listing_text = " ".join(
                checked.get(url) or ""
                for url in {opportunity["listing_url"], opportunity["apply_url"]}
            )
            text = normalized(listing_text)
            closed = any(
                phrase in text
                for phrase in (
                    "no longer accepting applications",
                    "position has been filled",
                    "job has expired",
                    "job is no longer available",
                    "this position is closed",
                    "this job is closed",
                )
            )
            title = normalized(opportunity["title"])
            application = any(
                phrase in text
                for phrase in (
                    "apply for this job",
                    "apply now",
                    "submit application",
                    "apply for this position",
                    "apply to job",
                    "apply here",
                )
            )
            removed = bool(
                inspection
                and inspection.get("listing_status") == "closed"
                and re.match(
                    r"Listing URL returned HTTP (404|410)\b", inspection.get("status_basis", "")
                )
            )
            if removed or closed or listing_expired(opportunity):
                opportunity.update(listing_status="closed", source_check="closed")
            elif title and title in text and application:
                opportunity.update(listing_status="open", source_check="matched")
            elif authoritative_open:
                opportunity.update(listing_status="open", source_check="matched")
            else:
                opportunity["listing_status"] = "unverified"
                risks.append(
                    "Source check: a reachable page alone does not confirm an active vacancy."
                )
        opportunity.update(risks=risks, updated_at=now(), last_checked_at=now())
        opportunity.update(assess(ctx.search, opportunity, ctx.candidate_text))
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        ctx.check()
        self.store.save("opportunities", opportunity)
        self.store.checkpoint(ctx.job_id, "verify", opportunity["id"] + key)

    def draft(self, ctx: Context, opportunity: dict):
        opportunity["candidate_claims"] = candidate_claims_supported(
            opportunity, ctx.candidate_text
        )
        dossier = {
            field: opportunity.get(field)
            for field in (
                "company",
                "title",
                "kind",
                "company_url",
                "listing_url",
                "contact_name",
                "contact_role",
                "description",
                "research_summary",
                "evidence",
                "candidate_claims",
                "match_components",
                "risks",
                "outreach_angle",
                "signals",
                "requirement_checks",
            )
        }
        key = fingerprint(
            [
                ctx.search["candidate"],
                ctx.search["objective"],
                opportunity["research_fingerprint"],
                dossier,
                ctx.document_fingerprint,
                route_for(ctx.settings, "outreach"),
            ]
        )
        if opportunity["draft_fingerprint"] == key and opportunity["drafts"]:
            return
        result = ctx.call(
            prompts.sequence(ctx.prompt_search, opportunity),
            SequenceResult,
            task="outreach",
            web=False,
            demo_data=demo.sequence_result(opportunity) if ctx.search["demo"] else None,
        )
        opportunity.update(
            drafts=sorted(result["drafts"], key=lambda draft: draft["step"]),
            draft_fingerprint=key,
            status="drafted",
            updated_at=now(),
        )
        opportunity["qa_issues"] = sequence_issues(opportunity)
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        self.store.save("opportunities", Opportunity.model_validate(opportunity).model_dump())
        self.store.event(
            ctx.job_id,
            f"Drafted {opportunity['company']} · {len(opportunity['qa_issues'])} copy checks",
        )
