import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from job_search_agent import cli
from job_search_agent.models import ProjectPreferences, ScanResult


@pytest.mark.parametrize("open_browser", [True, False])
def test_scan_generates_report_in_export_directory(tmp_path, monkeypatch, open_browser):
    export_dir = tmp_path / "custom exports"
    export_dir.mkdir()
    (export_dir / "latest-all-jobs.json").write_text(
        json.dumps({"scan_id": 42, "all_jobs": []}), encoding="utf-8"
    )
    result = ScanResult(
        scan_id=42, source_url="https://example.com/careers", jobs_found=0,
        matches_evaluated=0, shortlisted_jobs=0, export_files=[],
    )
    monkeypatch.setattr(cli, "ensure_public_http_url", lambda url: url)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(export_dir=export_dir))
    monkeypatch.setattr(cli, "_workflow", lambda *args, **kwargs: (
        SimpleNamespace(run=lambda *args: result), ProjectPreferences()
    ))
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url, **kwargs: opened.append(url) or False)
    args = ["scan", "https://example.com/careers", "--no-progress"]
    if not open_browser:
        args.append("--no-open-report")

    invocation = CliRunner().invoke(cli.app, args)

    assert invocation.exit_code == 0, invocation.output
    report = export_dir / "latest-report.html"
    assert "Job scan 42 report" in report.read_text(encoding="utf-8")
    assert opened == ([report.resolve().as_uri()] if open_browser else [])
    if open_browser:
        assert "Could not open a browser" in invocation.output
