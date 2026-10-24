"""Scriptable interface to the same services used by the browser."""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .config import AppError
from .files import MAX_FILE
from .models import OpportunitySeed, SearchInput
from .pipeline import Jobs
from .providers import inspect_providers
from .service import Service

app = typer.Typer(help="Find relevant jobs. Research fit. Review applications and outreach drafts.", no_args_is_help=True, pretty_exceptions_enable=False)
searches = typer.Typer(help="Create and configure searches.", no_args_is_help=True)
run_commands = typer.Typer(help="Inspect durable runs and their events.", no_args_is_help=True)
suppressions = typer.Typer(help="Global do-not-contact list.", no_args_is_help=True)
app.add_typer(searches, name="search")
app.add_typer(run_commands, name="runs")
app.add_typer(suppressions, name="suppress")
console = Console()
errors = Console(stderr=True)
Json = Annotated[bool, typer.Option("--json", help="Machine-readable JSON on stdout.")]


def guarded(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AppError, ValidationError, OSError, ValueError) as exc:
            code = exc.code if isinstance(exc, AppError) else "INPUT-INVALID"
            message = exc.message if isinstance(exc, AppError) else "Check the arguments, file contents, and path permissions."
            if kwargs.get("json_output"):
                typer.echo(json.dumps({"error": {"code": code, "message": message}}))
            else:
                errors.print(f"[red]{code}[/red] {message}")
            raise typer.Exit(2) from None
    return call


def output(value, json_output: bool):
    if json_output:
        typer.echo(json.dumps(value, ensure_ascii=False))
    else:
        console.print_json(data=value)


def read_file(path: Path) -> bytes:
    with path.open("rb") as file:
        raw = file.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise AppError("FILE-SIZE", "Files must be 8 MB or smaller.")
    return raw


def json_file(path: Path) -> dict:
    value = json.loads(read_file(path).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise AppError("INPUT-INVALID", "Use a JSON object containing search or opportunity fields.")
    return value


def route_overrides(values: dict) -> dict:
    routes = {}
    for task in ("profile", "discovery", "research", "outreach"):
        route = {key: values[f"{task}_{key}"] for key in ("provider", "model") if values.get(f"{task}_{key}") is not None}
        if route:
            routes[task] = route
    return routes


@app.callback()
def root(data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Use a separate local workspace.")] = None):
    if data_dir:
        os.environ["JOB_FINDER_DATA"] = str(data_dir.expanduser().resolve())


@app.command()
@guarded
def gui(port: Annotated[int, typer.Option(min=0, max=65535)] = 0, no_browser: bool = False):
    """Open the local browser app. Port 0 chooses a free port."""
    from .start import serve
    serve(port, open_browser=not no_browser)


@app.command()
@guarded
def doctor(json_output: Json = False):
    """Check CLI installations without invoking a model or reading credentials."""
    service = Service()
    output({"version": __version__, "data_directory": str(service.store.root), "providers": inspect_providers(), "note": "Availability is not an authentication test. Sign in to the chosen CLI first."}, json_output)


@app.command("schema")
@guarded
def schema(kind: Annotated[str, typer.Argument(help="search or opportunity")] = "search", json_output: Json = False):
    """Print the JSON configuration/import contract, including advanced fields."""
    models = {"search": SearchInput, "opportunity": OpportunitySeed}
    if kind not in models:
        raise AppError("SCHEMA-KIND", "Choose search or opportunity.")
    output(models[kind].model_json_schema(), json_output)


@searches.command("create")
@guarded
def create_search(
    config: Annotated[Path | None, typer.Option("--config", help="Complete search JSON; explicit flags override its fields.")] = None,
    name: str = "",
    objective: str = "",
    resume_file: Annotated[list[Path] | None, typer.Option("--resume", help="Repeat for supporting PDF, DOCX, TXT, or MD files.")] = None,
    candidate_name: str = "",
    email: str = "",
    candidate_location: str = "",
    skill: Annotated[list[str] | None, typer.Option(help="Repeat for each demonstrated skill.")] = None,
    work_authorization: str = "",
    needs_sponsorship: Annotated[bool | None, typer.Option("--needs-sponsorship/--no-sponsorship")] = None,
    clearance: str = "",
    role: Annotated[list[str] | None, typer.Option(help="Repeat for each target role.")] = None,
    location: Annotated[list[str] | None, typer.Option(help="Repeat for acceptable work locations.")] = None,
    work_mode: Annotated[list[str] | None, typer.Option(help="remote, hybrid, or onsite; repeat as needed.")] = None,
    strict_work_mode: Annotated[bool | None, typer.Option("--strict-work-mode/--flexible-work-mode")] = None,
    salary_min: float | None = None,
    salary_currency: str = "",
    must_have: str = "",
    exclude: str = "",
    company_leads: Annotated[bool | None, typer.Option("--company-leads/--open-roles-only")] = None,
    ats_board: Annotated[list[str] | None, typer.Option(help="Public Greenhouse, Lever, or Ashby board URL; repeat as needed.")] = None,
    provider: str | None = None,
    model: str | None = None,
    profile_provider: str | None = None,
    profile_model: str | None = None,
    discovery_provider: str | None = None,
    discovery_model: str | None = None,
    research_provider: str | None = None,
    research_model: str | None = None,
    outreach_provider: str | None = None,
    outreach_model: str | None = None,
    target: int | None = None,
    min_fit: int | None = None,
    max_calls: int | None = None,
    minutes: int | None = None,
    concurrency: int | None = None,
    max_cost: float | None = None,
    json_output: Json = False,
):
    """Create a bounded search without starting agents. JSON config exposes every field."""
    raw = json_file(config) if config else {}
    if name:
        raw["name"] = name
    raw.setdefault("name", "Job search")
    if objective:
        raw["objective"] = objective
    candidate, preferences, settings = raw.setdefault("candidate", {}), raw.setdefault("preferences", {}), raw.setdefault("settings", {})
    if not all(isinstance(v, dict) for v in (candidate, preferences, settings)):
        raise AppError("INPUT-INVALID", "Candidate, preferences, and settings must be JSON objects.")
    candidate.update({k: v for k, v in {"name": candidate_name, "email": email, "location": candidate_location, "work_authorization": work_authorization, "clearance": clearance}.items() if v})
    if skill is not None:
        candidate["skills"] = skill
    if needs_sponsorship is not None:
        candidate["needs_sponsorship"] = needs_sponsorship
    preferences.update({k: v for k, v in {"must_have": must_have, "exclude": exclude, "salary_currency": salary_currency}.items() if v})
    preferences.update({k: v for k, v in {"roles": role, "locations": location, "work_modes": work_mode, "strict_work_mode": strict_work_mode, "salary_min": salary_min, "include_company_leads": company_leads}.items() if v is not None})
    settings.update({k: v for k, v in {"provider": provider, "model": model, "target_results": target, "min_fit": min_fit, "max_agent_calls": max_calls, "max_minutes": minutes, "concurrency": concurrency, "max_cost_usd": max_cost, "ats_boards": ats_board}.items() if v is not None})
    overrides = route_overrides(locals())
    if overrides:
        routes = settings.setdefault("routes", {})
        if not isinstance(routes, dict) or any(not isinstance(v, dict) for v in routes.values()):
            raise AppError("INPUT-INVALID", "Agent routes must be JSON objects.")
        for task, override in overrides.items():
            routes[task] = routes.get(task, {}) | override
    parsed = SearchInput.model_validate(raw).model_dump()
    documents = [(path.name, read_file(path)) for path in resume_file or []]
    service = Service()
    search = service.create_search(parsed)
    for filename, contents in documents:
        service.upload_document(search["id"], filename, contents)
    output(search, json_output)


@searches.command("list")
@guarded
def list_searches(json_output: Json = False):
    rows = Service().store.list("searches")
    if json_output:
        output({"searches": rows}, True)
        return
    table = Table("ID", "Search", "Agent", "Target")
    for row in rows:
        table.add_row(row["id"], row["name"], row["settings"]["provider"], str(row["settings"]["target_results"]))
    console.print(table)


@searches.command("show")
@guarded
def show_search(search_id: str, json_output: Json = False):
    output(Service().search_detail(search_id), json_output)


@searches.command("configure")
@guarded
def configure_search(search_id: str, file: Annotated[Path, typer.Option(help="JSON object with search fields to update.")], json_output: Json = False):
    """Update objective, candidate, preferences, weights, routes, or budgets from JSON."""
    output(Service().update_search(search_id, json_file(file)), json_output)


@app.command("resume-upload")
@guarded
def upload_resume(search_id: str, text: str = "", file: Annotated[list[Path] | None, typer.Option()] = None, json_output: Json = False):
    """Replace résumé text and/or add PDF, DOCX, TXT, or MD supporting documents."""
    service = Service()
    if not text and not file:
        raise AppError("RESUME-EMPTY", "Provide --text or --file.")
    if text:
        service.update_search(search_id, {"candidate": {"resume_text": text}})
    documents = [service.upload_document(search_id, path.name, read_file(path)) for path in file or []]
    output({"search_id": search_id, "documents": documents, "updated": True}, json_output)


@app.command("import-opportunities")
@guarded
def import_opportunities(search_id: str, file: Path, json_output: Json = False):
    """Import job CSV/XLSX with company, title, listing_url or company-lead fields."""
    output(Service().import_opportunities(search_id, file.name, read_file(file)), json_output)


def watch(manager: Jobs, run_id: str, json_output: bool):
    try:
        with Progress(TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), disable=json_output) as progress:
            task = progress.add_task("Starting", total=1)
            while True:
                run = manager.store.get("runs", run_id)
                progress.update(task, description=f"{run['current_stage'] or 'starting'} · {run['current_item']}", completed=run["completed"], total=max(run["total"], 1))
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.25)
    except KeyboardInterrupt:
        manager.control(run_id, "pause")
        while manager.store.get("runs", run_id)["status"] in {"running", "queued"}:
            time.sleep(0.1)
        run = manager.store.get("runs", run_id)
    output({"run": run, "events": manager.store.events(run_id)}, json_output)
    if run["status"] != "completed":
        raise typer.Exit({"failed": 3, "budget_exhausted": 4, "paused": 5, "cancelled": 5}.get(run["status"], 3))


@app.command("run")
@guarded
def run(
    search_id: str, stage: str = "pipeline",
    allow_agent_tools: Annotated[bool, typer.Option(help="Approve agent commands, file changes, connected tools and internet access for this search.")] = False,
    profile_provider: str | None = None, profile_model: str | None = None,
    discovery_provider: str | None = None, discovery_model: str | None = None,
    research_provider: str | None = None, research_model: str | None = None,
    outreach_provider: str | None = None, outreach_model: str | None = None,
    json_output: Json = False,
):
    """Run report, pipeline, profile, discover, research, verify, or draft. Ctrl-C pauses safely."""
    service = Service()
    manager = Jobs(service)
    manager.recover_interrupted()
    if allow_agent_tools:
        service.update_search(search_id, {"settings": {"agent_tools_approved": True}})
    overrides = route_overrides(locals())
    if overrides:
        current = service.store.get("searches", search_id)["settings"]["routes"]
        service.update_search(search_id, {"settings": {"routes": current | {task: current.get(task, {}) | value for task, value in overrides.items()}}})
    run = manager.create(search_id, stage)
    watch(manager, run["id"], json_output)


@app.command("resume")
@guarded
def resume(run_id: str, json_output: Json = False):
    """Resume a paused run and keep completed work. Use resume-upload for CVs."""
    manager = Jobs(Service())
    manager.recover_interrupted()
    manager.control(run_id, "resume")
    watch(manager, run_id, json_output)


@app.command("pause")
@guarded
def pause(run_id: str, json_output: Json = False):
    output({"run": Jobs(Service()).control(run_id, "pause")}, json_output)


@app.command("cancel")
@guarded
def cancel(run_id: str, json_output: Json = False):
    output({"run": Jobs(Service()).control(run_id, "cancel")}, json_output)


@run_commands.command("list")
@guarded
def list_runs(search_id: Annotated[str | None, typer.Option("--search")] = None, json_output: Json = False):
    output({"runs": Service().store.list("runs", search_id)}, json_output)


@run_commands.command("show")
@guarded
def show_run(run_id: str, json_output: Json = False):
    store = Service().store
    output({"run": store.get("runs", run_id), "events": store.events(run_id)}, json_output)


@app.command("opportunities")
@guarded
def list_opportunities(search_id: str, status: str = "", kind: str = "", min_fit: int = 0, json_output: Json = False):
    opportunities = Service().search_detail(search_id)["opportunities"]
    opportunities = [opportunity for opportunity in opportunities if opportunity["fit_score"] >= min_fit and (not status or opportunity["status"] == status) and (not kind or opportunity["kind"] == kind)]
    if json_output:
        output({"opportunities": opportunities}, True)
        return
    table = Table("ID", "Company / Role", "Fit", "Evidence", "Eligibility", "Next")
    for opportunity in opportunities:
        table.add_row(opportunity["id"], f"{opportunity['company']} · {opportunity['title']}", str(opportunity["fit_score"]), str(opportunity["confidence"]), opportunity["eligibility"], opportunity["next_action"])
    console.print(table)


@app.command("review")
@guarded
def review(opportunity_id: str, status: Annotated[str, typer.Option(help="approved, rejected, suppressed, or drafted")], note: str = "", json_output: Json = False):
    """Record human review. Approval never sends or submits an application."""
    output(Service().update_opportunity(opportunity_id, {"status": status, "review_notes": note}), json_output)


@app.command("activity")
@guarded
def activity(opportunity_id: str, status: Annotated[str, typer.Option(help="new, shortlisted, applied, contacted, replied, interview, offer, rejected, withdrawn, not_interested, or bounced")], follow_up: str | None = None, note: str | None = None, json_output: Json = False):
    """Track application/contact outcomes and the next follow-up date locally."""
    patch = {"application_status": status}
    if follow_up is not None:
        patch["next_follow_up"] = follow_up
    if note is not None:
        patch["review_notes"] = note
    output(Service().update_opportunity(opportunity_id, patch), json_output)


@app.command("edit-opportunity")
@guarded
def edit_opportunity(opportunity_id: str, file: Annotated[Path, typer.Option()], json_output: Json = False):
    """Apply contact corrections or edited drafts from a JSON object."""
    output(Service().update_opportunity(opportunity_id, json_file(file)), json_output)


@app.command("export")
@guarded
def export_search(search_id: str, output_path: Annotated[Path, typer.Option("--output", "-o")], format: str = "csv", scope: str = "all", force: bool = False, json_output: Json = False):
    """Export CSV/XLSX. Scope: all, apply, outreach, or shortlist."""
    if output_path.exists() and not force:
        raise AppError("FILE-EXISTS", "Output exists. Choose another path or use --force.")
    raw = Service().export(search_id, format, scope)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    output({"path": str(output_path.resolve()), "format": format, "scope": scope, "bytes": len(raw)}, json_output)


@app.command("verify-import")
@guarded
def import_verification(search_id: str, file: Path, source: str = "", json_output: Json = False):
    """Import verifier CSV: email,status,verification_source,verification_at."""
    output(Service().import_verification(search_id, file.name, read_file(file), source), json_output)


@app.command("outcomes-import")
@guarded
def import_outcomes(search_id: str, file: Path, json_output: Json = False):
    """Import application/contact outcomes using the app's exported opportunity IDs."""
    output(Service().import_outcomes(search_id, file.name, read_file(file)), json_output)


@suppressions.command("add")
@guarded
def suppress(email: str = "", domain: str = "", reason: str = "Do not contact", json_output: Json = False):
    if bool(email) == bool(domain):
        raise AppError("SUPPRESSION-TARGET", "Provide exactly one of --email or --domain.")
    output(Service().suppress({"kind": "email" if email else "domain", "value": email or domain, "reason": reason}), json_output)


@suppressions.command("list")
@guarded
def list_suppressions(json_output: Json = False):
    output({"suppressions": Service().store.suppressions()}, json_output)


@suppressions.command("import")
@guarded
def import_suppressions(file: Path, json_output: Json = False):
    output(Service().import_suppressions(file.name, read_file(file)), json_output)


@app.command("demo")
@guarded
def create_demo(run_pipeline: Annotated[bool, typer.Option("--run")] = False, json_output: Json = False):
    """Create a clearly fictional offline search; optionally run all stages."""
    service = Service()
    search = service.create_demo()
    if run_pipeline:
        manager = Jobs(service)
        run = manager.create(search["id"])
        watch(manager, run["id"], json_output)
    else:
        output({"search": search}, json_output)


@app.command("backup")
@guarded
def backup(output_path: Annotated[Path, typer.Option("--output", "-o")], json_output: Json = False):
    """Create a consistent SQLite backup containing searches, docs, and outcomes."""
    if output_path.exists():
        raise AppError("FILE-EXISTS", "Choose a new backup path.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    store = Service().store
    with store.connect() as source, sqlite3.connect(output_path) as target:
        source.backup(target)
    output({"path": str(output_path.resolve()), "bytes": output_path.stat().st_size}, json_output)


if __name__ == "__main__":
    app()
