from __future__ import annotations

import shutil
import webbrowser
from pathlib import Path

import httpx
import typer
from langchain_ollama import OllamaEmbeddings
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from .company_database import CompanyDirectory
from .config import load_preferences
from .crawler import CareersCrawler, configure_playwright_browser_path
from .database import Database, DatabaseEmbeddingCache
from .exporter import best_match_per_job
from .llm import OllamaCloudGateway
from .models import JobMatch
from .progress import ProgressCallback, ProgressUpdate
from .ranking import HybridRanker
from .report import generate_html_report
from .resumes import load_active_resume_profiles, load_resume_profiles
from .settings import Settings, get_settings
from .url_policy import ensure_public_http_url
from .workflow import JobSearchWorkflow, WorkflowServices

app = typer.Typer(no_args_is_help=True, help="Find and rank jobs on public company careers pages.")
console = Console()


class RichProgressReporter:
    def __init__(self, progress: Progress):
        self.progress = progress
        self.task_ids: dict[str, int] = {}

    def __call__(self, update: ProgressUpdate) -> None:
        task_id = self.task_ids.get(update.key)
        if task_id is None:
            task_id = self.progress.add_task(update.description, total=update.total)
            self.task_ids[update.key] = task_id

        changes: dict[str, object] = {"description": update.description}
        if update.completed is not None:
            changes["completed"] = update.completed
        if update.total is not None:
            changes["total"] = update.total
        self.progress.update(task_id, **changes)
        if update.finished:
            self.progress.stop_task(task_id)


def _progress_display(*, disabled: bool = False) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        expand=True,
        disable=disabled,
    )


def _database(settings: Settings) -> Database:
    database = Database(settings.database_url)
    database.create_schema()
    return database


def _company_directory(settings: Settings) -> CompanyDirectory:
    directory = CompanyDirectory(settings.company_database_url)
    directory.create_schema()
    return directory


def _terminal_link(url: str) -> Text:
    return Text(url, style=f"link {url}")


def _job_match_table(matches: list[JobMatch], *, title: str) -> Table:
    table = Table(title=title)
    table.add_column("Score", justify="right")
    table.add_column("Shortlisted")
    table.add_column("Job")
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Resume")
    table.add_column("URL")
    for match in matches:
        table.add_row(
            f"{match.total_score:.1f}",
            "yes" if match.shortlisted else "no",
            match.job.title,
            match.job.company,
            match.job.location,
            match.profile_name,
            _terminal_link(match.job.url),
        )
    return table


def _workflow(
    settings: Settings,
    preferences_path: Path,
    progress_callback: ProgressCallback | None = None,
    *,
    archive_exports: bool = False,
) -> tuple[JobSearchWorkflow, object]:
    preferences = load_preferences(preferences_path)
    database = _database(settings)
    cloud = OllamaCloudGateway(
        api_key=settings.ollama_api_key,
        host=settings.ollama_cloud_url,
        model=settings.ollama_cloud_model,
    )
    embeddings = OllamaEmbeddings(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_local_url,
    )
    services = WorkflowServices(
        crawler=CareersCrawler(
            preferences.crawler,
            user_agent=settings.user_agent,
            progress=progress_callback,
        ),
        cloud=cloud,
        ranker=HybridRanker(
            embeddings=embeddings,
            cloud=cloud,
            shortlist_threshold=preferences.shortlist_threshold,
            progress=progress_callback,
            embedding_batch_size=settings.embedding_batch_size,
            embedding_max_retries=settings.embedding_max_retries,
            embedding_retry_delay_seconds=settings.embedding_retry_delay_seconds,
            embedding_cache=DatabaseEmbeddingCache(
                database,
                settings.ollama_embedding_model,
            ),
        ),
        database=database,
        export_dir=settings.export_dir,
        resume_dir=settings.resume_dir,
        progress=progress_callback,
        company_directory=_company_directory(settings),
        archive_exports=archive_exports,
    )
    return JobSearchWorkflow(services), preferences


@app.command("init")
def initialize() -> None:
    """Create editable config files and data directories without overwriting files."""

    for directory in (Path("resumes"), Path("data"), Path("data/exports")):
        directory.mkdir(parents=True, exist_ok=True)
    for template, destination in (
        (Path(".env.example"), Path(".env")),
        (Path("preferences.example.yaml"), Path("preferences.yaml")),
    ):
        if destination.exists():
            console.print(f"[yellow]Kept existing[/yellow] {destination}")
        else:
            shutil.copyfile(template, destination)
            console.print(f"[green]Created[/green] {destination}")


@app.command()
def doctor(
    preferences_path: Path = typer.Option(Path("preferences.yaml"), "--preferences", "-p"),
    show_progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Show or hide diagnostic progress",
    ),
) -> None:
    """Check credentials, resume files, local embeddings, and browser setup."""

    settings = get_settings()
    checks: list[tuple[str, bool, str]] = []
    progress_display = _progress_display(disabled=not show_progress)
    progress_display.start()
    progress_reporter = RichProgressReporter(progress_display)
    progress_reporter(ProgressUpdate("diagnostics", "Checking configuration", completed=0, total=5))
    checks.append(
        (
            "Ollama Cloud API key",
            bool(settings.ollama_api_key),
            "configured" if settings.ollama_api_key else "missing from .env",
        )
    )
    progress_reporter(ProgressUpdate("diagnostics", "Checked configuration", completed=1, total=5))

    try:
        preferences = load_preferences(preferences_path)
        profiles = load_resume_profiles(preferences, settings.resume_dir)
        checks.append(("Resume profiles", True, f"{len(profiles)} readable"))
    except Exception as exc:
        checks.append(("Resume profiles", False, str(exc)))
    progress_reporter(ProgressUpdate("diagnostics", "Checked resumes", completed=2, total=5))

    try:
        response = httpx.get(
            f"{settings.ollama_local_url.rstrip('/')}/api/tags",
            timeout=5,
        )
        response.raise_for_status()
        model_names = {
            item.get("name", "").split(":")[0] for item in response.json().get("models", [])
        }
        found = settings.ollama_embedding_model.split(":")[0] in model_names
        checks.append(
            (
                "Local EmbeddingGemma",
                found,
                "available" if found else f"run: ollama pull {settings.ollama_embedding_model}",
            )
        )
    except Exception as exc:
        checks.append(("Local Ollama embedding API", False, str(exc)))
    progress_reporter(
        ProgressUpdate("diagnostics", "Checked local embeddings", completed=3, total=5)
    )

    if settings.ollama_api_key:
        try:
            response = httpx.get(
                f"{settings.ollama_cloud_url.rstrip('/')}/api/tags",
                headers={"Authorization": f"Bearer {settings.ollama_api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            names = {item.get("name") for item in response.json().get("models", [])}
            model_ok = settings.ollama_cloud_model in names
            checks.append(
                (
                    "Ollama Cloud model",
                    model_ok,
                    settings.ollama_cloud_model
                    if model_ok
                    else "model unavailable to this account",
                )
            )
        except Exception as exc:
            checks.append(("Ollama Cloud connection", False, str(exc)))
    progress_reporter(ProgressUpdate("diagnostics", "Checked Ollama Cloud", completed=4, total=5))

    try:
        from playwright.sync_api import sync_playwright

        configure_playwright_browser_path()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser_version = browser.version
            browser.close()
            checks.append(
                (
                    "Playwright Chromium",
                    True,
                    f"Chromium {browser_version}",
                )
            )
    except Exception as exc:
        checks.append(("Playwright Chromium", False, str(exc)))
    progress_reporter(
        ProgressUpdate(
            "diagnostics",
            "Diagnostics complete",
            completed=5,
            total=5,
            finished=True,
        )
    )
    progress_display.stop()

    table = Table(title="Job Search Agent diagnostics")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    for name, passed, details in checks:
        table.add_row(name, "[green]PASS[/green]" if passed else "[red]FAIL[/red]", details)
    console.print(table)
    if any(not passed for _, passed, _ in checks):
        raise typer.Exit(1)


@app.command()
def scan(
    careers_url: str = typer.Argument(..., help="Public company careers-page URL"),
    preferences_path: Path = typer.Option(Path("preferences.yaml"), "--preferences", "-p"),
    show_progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Show or hide live scan progress",
    ),
    archive: bool = typer.Option(
        False,
        "--archive",
        help="Also create timestamped shortlisted CSV/JSON snapshots",
    ),
    open_report: bool = typer.Option(
        True,
        "--open-report/--no-open-report",
        help="Open the generated HTML report in your default browser after scanning",
    ),
) -> None:
    """Scan, rank, export CSV/JSON, and generate an HTML report."""

    try:
        ensure_public_http_url(careers_url)
        settings = get_settings()
        with _progress_display(disabled=not show_progress) as progress:
            workflow, preferences = _workflow(
                settings,
                preferences_path,
                RichProgressReporter(progress),
                archive_exports=archive,
            )
            result = workflow.run(careers_url, preferences)
    except Exception as exc:
        console.print(f"[red]Scan failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    recommended = best_match_per_job(result.matches)
    shortlisted = [match for match in recommended if match.shortlisted]
    console.print(
        _job_match_table(
            recommended,
            title=f"Scan {result.scan_id}: all jobs — recommended resume per job",
        )
    )
    console.print(
        _job_match_table(
            shortlisted,
            title=f"Scan {result.scan_id}: shortlisted jobs",
        )
    )
    console.print(
        f"Found {result.jobs_found} jobs; {result.shortlisted_jobs} shortlisted; "
        f"{result.matches_evaluated} profile/job matches evaluated."
    )
    console.print(
        f"Crawled {result.pages_scanned} relevant pages; "
        f"skipped {len(result.skipped_pages)} policy, off-site, or non-HTML pages."
    )
    if result.coverage_complete is False:
        console.print("[yellow]Partial scan: some pages or job details could not be verified. Results are incomplete.[/yellow]")
    if result.portal_reported_jobs is not None:
        coverage = "complete" if result.coverage_complete else "incomplete"
        console.print(
            f"Portal reported {result.portal_reported_jobs} result cards; "
            f"scanned {result.result_cards_scanned}; "
            f"removed {result.duplicate_result_cards} duplicates; "
            f"coverage {coverage}."
        )
    for path in result.export_files:
        console.print(f"Exported: {path}")
    for error in result.errors:
        console.print(f"[yellow]Warning:[/yellow] {error}")

    report_path = settings.export_dir / "latest-report.html"
    try:
        generate_html_report(
            settings.export_dir / "latest-all-jobs.json",
            report_path,
            threshold=preferences.shortlist_threshold,
        )
    except Exception as exc:
        console.print(f"[yellow]Scan saved, but HTML report generation failed:[/yellow] {exc}")
        return
    console.print(f"Report created: {report_path.resolve()}")
    if open_report:
        try:
            if not webbrowser.open(report_path.resolve().as_uri(), new=2):
                console.print("[yellow]Could not open a browser. Open the report path above.[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Could not open the report in a browser:[/yellow] {exc}")


@app.command("jobs")
def list_jobs(
    shortlisted: bool = typer.Option(False, "--shortlisted", help="Show shortlisted matches only"),
    history: bool = typer.Option(
        False,
        "--history",
        help="Show matches from every scan instead of only the latest completed scan",
    ),
    limit: int = typer.Option(100, min=1, max=1000),
) -> None:
    """Show the latest stored matches, or the complete history on request."""

    records = _database(get_settings()).list_matches(
        shortlisted_only=shortlisted,
        latest_scan_only=not history,
    )
    if shortlisted and not history:
        best_by_job = {}
        for record in records:
            current = best_by_job.get(record["job_identity"])
            if current is None or record["score"] > current["score"]:
                best_by_job[record["job_identity"]] = record
        records = sorted(best_by_job.values(), key=lambda item: item["score"], reverse=True)
    records = records[:limit]
    table = Table(title="Historical job matches" if history else "Latest scan job matches")
    for column in ("Score", "Shortlisted", "Job", "Company", "Location", "Resume", "URL"):
        table.add_column(column, justify="right" if column == "Score" else "left")
    for record in records:
        table.add_row(
            f"{record['score']:.1f}",
            "yes" if record["shortlisted"] else "no",
            record["job_title"],
            record["company"],
            record["location"],
            record["profile"],
            _terminal_link(record["url"]),
        )
    console.print(table)


@app.command("report")
def create_report(
    input_path: Path = typer.Option(
        Path("data/exports/latest-all-jobs.json"),
        "--input",
        "-i",
        help="All-jobs JSON export to visualize",
    ),
    output_path: Path = typer.Option(
        Path("data/exports/latest-report.html"),
        "--output",
        "-o",
        help="HTML dashboard destination",
    ),
    preferences_path: Path = typer.Option(Path("preferences.yaml"), "--preferences", "-p"),
) -> None:
    """Generate a searchable HTML dashboard from an all-jobs JSON export."""

    try:
        preferences = load_preferences(preferences_path)
        counts = generate_html_report(
            input_path,
            output_path,
            threshold=preferences.shortlist_threshold,
        )
    except Exception as exc:
        console.print(f"[red]Report generation failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"Report created: {output_path} "
        f"({counts['total']} jobs; {counts.get('shortlisted', 0)} shortlisted)"
    )


@app.command("companies")
def list_companies() -> None:
    """Show each unique successful scan URL grouped by company."""

    records = _company_directory(get_settings()).list_portals()
    console.print("Unique successful scan URLs")
    for record in records:
        line = Text(f"{record['company_name']} | ")
        line.append(
            record["job_portal_url"],
            style=f"link {record['job_portal_url']}",
        )
        console.print(line, soft_wrap=True)


@app.command("sync-companies")
def sync_companies() -> None:
    """Backfill the company directory from existing scan history."""

    settings = get_settings()
    database = _database(settings)
    directory = _company_directory(settings)
    records = database.list_company_portals()
    for record in records:
        directory.upsert(record["job_portal_url"], record["company_names"])
    console.print(f"Company directory sync complete: {len(records)} job portals stored.")


@app.command("sync-resumes")
def sync_resumes(
    preferences_path: Path = typer.Option(Path("preferences.yaml"), "--preferences", "-p"),
    show_progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Show or hide resume loading progress",
    ),
) -> None:
    """Remove stale resume metadata and register usable discovered profiles."""

    settings = get_settings()
    try:
        preferences = load_preferences(preferences_path)
        with _progress_display(disabled=not show_progress) as progress:
            report = load_active_resume_profiles(
                preferences,
                settings.resume_dir,
                RichProgressReporter(progress),
            )
        result = _database(settings).synchronize_resume_profiles(report.profiles)
    except Exception as exc:
        console.print(f"[red]Resume sync failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"Resume sync complete: {result.active} active, {result.removed} stale removed, "
        f"{result.replaced} replaced."
    )
    for error in report.errors:
        console.print(f"[yellow]Warning:[/yellow] {error}")


@app.command("profiles")
def list_profiles() -> None:
    """Show resume profiles already stored in the database."""

    records = _database(get_settings()).list_profiles()
    table = Table(title="Stored resume profiles")
    table.add_column("Name")
    table.add_column("Resume path")
    table.add_column("SHA-256")
    table.add_column("Updated")
    for record in records:
        table.add_row(
            record["name"],
            record["path"],
            record["file_hash"][:12],
            str(record["updated_at"]),
        )
    console.print(table)


if __name__ == "__main__":
    app()
