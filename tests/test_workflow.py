import json
import sqlite3
from pathlib import Path

import pytest

from job_search_agent.company_database import CompanyDirectory
from job_search_agent.crawler import CrawlReport
from job_search_agent.database import Database
from job_search_agent.models import (
    CrawlerConfig,
    JobPreferences,
    LLMFitAssessment,
    PageDocument,
    ProjectPreferences,
)
from job_search_agent.ranking import HybridRanker
from job_search_agent.workflow import JobSearchWorkflow, WorkflowServices


class FakeCrawler:
    def crawl(self, source_url):
        return CrawlReport(
            pages=[
                PageDocument(
                    url="https://example.com/jobs/1",
                    source_url=source_url,
                    depth=1,
                    text="Backend Engineer",
                    html="""
                    <script type="application/ld+json">
                    {
                      "@type": "JobPosting",
                      "title": "Backend Engineer",
                      "description": "Python and SQL services",
                      "hiringOrganization": {"name": "Example"},
                      "url": "https://example.com/jobs/1"
                    }
                    </script>
                    """,
                )
            ],
            errors=[],
        )


class IncompletePhenomCrawler:
    def crawl(self, source_url):
        return CrawlReport(
            pages=[
                PageDocument(
                    url=source_url,
                    source_url=source_url,
                    text="One of two jobs",
                    html="""
                    <script>phApp.ddo = {"eagerLoadRefineSearch": {
                      "hits": 1,
                      "totalHits": 2,
                      "data": {"jobs": [{
                        "title": "Backend Engineer",
                        "company": "Example",
                        "descriptionTeaser": "Python and SQL services",
                        "jobUrl": "https://example.com/jobs/1"
                      }]}
                    }};</script>
                    """,
                )
            ],
            errors=[],
        )


class EmptyCrawler:
    def crawl(self, source_url):
        return CrawlReport(pages=[], errors=[])


class SalesforceFeedCrawler:
    def crawl(self, source_url):
        return CrawlReport(pages=[PageDocument(
            url=source_url, source_url=source_url, text="",
            html=json.dumps({"salesforce": {
                "board_url": "https://www.salesforce.com/company/careers/jobs",
                "total": 1, "coverage_complete": True, "jobs": [{
                    "Job_Requisition_Ref_ID": "JR1", "Job_Posting_Title": "Software Engineer",
                    "Job_Requisition_Primary_Location": "India - Bangalore",
                    "Job_Description": "Python and SQL services", "_full_details": True,
                }],
            }}),
        )], errors=[])


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeCloud:
    def assess_fit(self, profile, job):
        return LLMFitAssessment(fit_score=15, rationale="Strong match")

    def extract_jobs(self, page):  # pragma: no cover - JSON-LD avoids cloud extraction
        raise AssertionError("Cloud extraction should not run for structured job data")


@pytest.mark.parametrize("partial", [False, True])
def test_langgraph_workflow_runs_end_to_end_with_test_doubles(tmp_path: Path, monkeypatch, partial):
    if partial:
        original_crawl = FakeCrawler.crawl

        def partial_crawl(self, source_url):
            report = original_crawl(self, source_url)
            report.errors.append("Cloudflare blocked a job detail page")
            return report

        monkeypatch.setattr(FakeCrawler, "crawl", partial_crawl)
    resume = tmp_path / "backend.txt"
    resume.write_text("Backend engineer with Python and SQL experience", encoding="utf-8")
    preferences = ProjectPreferences(
        shortlist_threshold=50,
        job_preferences=JobPreferences(preferred_job_terms=["Backend Engineer"]),
        crawler=CrawlerConfig(delay_seconds=0),
    )
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.create_schema()
    cloud = FakeCloud()
    progress_updates = []
    company_directory = CompanyDirectory(f"sqlite:///{tmp_path / 'companies.db'}")
    company_directory.create_schema()
    workflow = JobSearchWorkflow(
        WorkflowServices(
            crawler=FakeCrawler(),
            cloud=cloud,
            ranker=HybridRanker(
                embeddings=FakeEmbeddings(),
                cloud=cloud,
                shortlist_threshold=50,
            ),
            database=database,
            export_dir=tmp_path / "exports",
            resume_dir=tmp_path,
            progress=progress_updates.append,
            company_directory=company_directory,
        )
    )

    result = workflow.run("https://example.com/careers", preferences)

    assert result.jobs_found == 1
    assert result.matches_evaluated == 1
    assert result.shortlisted_jobs == 1
    assert len(result.export_files) == 4
    assert database.list_matches(shortlisted_only=True)[0]["profile"] == "backend"
    assert company_directory.list_portals() == [
        {
            "company_name": "Example",
            "job_portal_url": "https://example.com/careers",
        }
    ]
    workflow_updates = [update for update in progress_updates if update.key == "workflow"]
    assert workflow_updates[-1].completed == 6
    assert workflow_updates[-1].finished is True
    assert workflow_updates[-1].description == ("Partial scan finished" if partial else "Scan complete")
    assert result.coverage_complete is False if partial else result.coverage_complete is None
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT status FROM scans").fetchone()[0] == (
            "partial" if partial else "completed"
        )
    payload = json.loads((tmp_path / "exports/latest-all-jobs.json").read_text())
    assert payload["scan_status"] == ("partial" if partial else "completed")
    assert len(database.list_matches(latest_scan_only=True)) == 1


def test_workflow_warns_when_phenom_job_coverage_is_incomplete(tmp_path: Path):
    (tmp_path / "backend.txt").write_text(
        "Backend engineer with Python and SQL experience",
        encoding="utf-8",
    )
    preferences = ProjectPreferences(crawler=CrawlerConfig(delay_seconds=0))
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.create_schema()
    cloud = FakeCloud()
    workflow = JobSearchWorkflow(
        WorkflowServices(
            crawler=IncompletePhenomCrawler(),
            cloud=cloud,
            ranker=HybridRanker(
                embeddings=FakeEmbeddings(),
                cloud=cloud,
                shortlist_threshold=50,
            ),
            database=database,
            export_dir=tmp_path / "exports",
            resume_dir=tmp_path,
        )
    )

    result = workflow.run("https://example.com/careers", preferences)

    assert result.jobs_found == 1
    assert any(
        "portal reported 2 jobs, but only 1 result card was scanned" in error
        for error in result.errors
    )
    assert result.portal_reported_jobs == 2
    assert result.result_cards_scanned == 1
    assert result.duplicate_result_cards == 0
    assert result.coverage_complete is False


def test_zero_job_scan_is_not_added_to_company_directory(tmp_path: Path):
    (tmp_path / "backend.txt").write_text("Backend engineer", encoding="utf-8")
    preferences = ProjectPreferences(crawler=CrawlerConfig(delay_seconds=0))
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.create_schema()
    company_directory = CompanyDirectory(f"sqlite:///{tmp_path / 'companies.db'}")
    company_directory.create_schema()
    cloud = FakeCloud()
    workflow = JobSearchWorkflow(
        WorkflowServices(
            crawler=EmptyCrawler(),
            cloud=cloud,
            ranker=HybridRanker(
                embeddings=FakeEmbeddings(),
                cloud=cloud,
                shortlist_threshold=50,
            ),
            database=database,
            export_dir=tmp_path / "exports",
            resume_dir=tmp_path,
            company_directory=company_directory,
        )
    )

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    previous = export_dir / "latest-all-jobs.json"
    previous.write_text("previous results", encoding="utf-8")
    with pytest.raises(RuntimeError, match="No jobs could be verified"):
        workflow.run("https://jobs.example.com/not-a-listing", preferences)
    assert previous.read_text(encoding="utf-8") == "previous results"
    assert company_directory.list_portals() == []


def test_original_salesforce_feed_reaches_ranking_and_exports_with_coverage(tmp_path):
    (tmp_path / "backend.txt").write_text("Backend engineer with Python and SQL", encoding="utf-8")
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.create_schema()
    cloud = FakeCloud()
    workflow = JobSearchWorkflow(WorkflowServices(
        crawler=SalesforceFeedCrawler(), cloud=cloud,
        ranker=HybridRanker(embeddings=FakeEmbeddings(), cloud=cloud, shortlist_threshold=50),
        database=database, export_dir=tmp_path / "exports", resume_dir=tmp_path,
    ))
    source = "https://www.salesforce.com/company/careers/jobs/?country=India"
    result = workflow.run(source, ProjectPreferences())
    assert result.jobs_found == 1
    assert result.portal_reported_jobs == 1
    assert result.coverage_complete
    assert result.errors == []
    assert result.matches[0].job.url.startswith("https://www.salesforce.com/company/careers/jobs/JR1/")
    assert result.matches[0].job.source_url == source
