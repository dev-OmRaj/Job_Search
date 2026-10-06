import json
from pathlib import Path

from job_search_agent.database import Database
from job_search_agent.exporter import export_matches
from job_search_agent.models import JobMatch, JobPosting, JobPreferences, ResumeProfile
from job_search_agent.ranking import RankingResult


def test_persists_and_exports_match_history(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'jobs.db'}")
    database.create_schema()
    source_url = "https://example.com/careers"
    scan_id = database.start_scan(source_url)
    profile = ResumeProfile(
        name="backend",
        path=tmp_path / "resume.txt",
        file_hash="b" * 64,
        sanitized_summary="Python backend engineer",
        preferences=JobPreferences(),
    )
    job = JobPosting(
        title="Backend Engineer",
        company="Example",
        url="https://example.com/jobs/1",
        source_url=source_url,
    )
    match = JobMatch(
        job=job,
        profile_name="backend",
        semantic_score=40,
        preference_score=15,
        llm_score=10,
        total_score=65,
        shortlisted=True,
        rationale="Good fit",
    )
    ranking = RankingResult(
        matches=[match],
        profile_embeddings={"backend": [1.0, 0.0]},
        job_embeddings={job.identity_key: [1.0, 0.0]},
    )

    database.persist_ranking(scan_id=scan_id, profiles=[profile], result=ranking)
    database.finish_scan(scan_id, jobs_found=1)
    stored = database.list_matches(shortlisted_only=True)
    exports = export_matches([match], tmp_path / "exports", scan_id)

    assert stored[0]["profile"] == "backend"
    assert stored[0]["score"] == 65
    assert database.list_company_portals() == [
        {"job_portal_url": source_url, "company_names": ["Example"]}
    ]
    assert len(exports) == 4
    assert {path.suffix for path in exports} == {".csv", ".json"}
    assert {path.name for path in exports} == {
        "latest-all-jobs.csv",
        "latest-all-jobs.json",
        "latest-shortlisted.csv",
        "latest-shortlisted.json",
    }
    payload = json.loads(
        next(path for path in exports if path.name == "latest-shortlisted.json").read_text()
    )
    assert payload["shortlisted_jobs"][0]["profile_name"] == "backend"
    assert "matches" not in payload
    all_jobs_payload = json.loads(
        next(path for path in exports if path.name == "latest-all-jobs.json").read_text()
    )
    assert all_jobs_payload["all_jobs"][0]["job"]["title"] == "Backend Engineer"

    archived = export_matches([match], tmp_path / "exports", scan_id, archive=True)

    assert len(archived) == 8
    assert len(list((tmp_path / "exports").glob(f"scan-{scan_id}-*-all-jobs.csv"))) == 1
    assert len(list((tmp_path / "exports").glob(f"scan-{scan_id}-*-all-jobs.json"))) == 1
    assert len(list((tmp_path / "exports").glob(f"scan-{scan_id}-*-shortlisted.csv"))) == 1
    assert len(list((tmp_path / "exports").glob(f"scan-{scan_id}-*-shortlisted.json"))) == 1


def test_resume_sync_removes_stale_profiles_and_old_matches(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'sync.db'}")
    database.create_schema()
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text("Python engineer", encoding="utf-8")
    profile = ResumeProfile(
        name="backend",
        path=resume_path,
        file_hash="a" * 64,
        sanitized_summary="Python engineer",
        preferences=JobPreferences(),
    )
    stale = profile.model_copy(update={"name": "old_profile", "file_hash": "c" * 64})
    database.synchronize_resume_profiles([profile, stale])

    result = database.synchronize_resume_profiles([profile])

    assert result.active == 1
    assert result.removed == 1
    assert [item["name"] for item in database.list_profiles()] == ["backend"]

    empty_result = database.synchronize_resume_profiles([])

    assert empty_result.removed == 1
    assert database.list_profiles() == []


def test_rescan_refreshes_canonical_job_url(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'url-refresh.db'}")
    database.create_schema()
    source_url = "https://jobs.example.com/careers"
    profile = ResumeProfile(
        name="data",
        path=tmp_path / "resume.txt",
        file_hash="d" * 64,
        sanitized_summary="Data scientist",
        preferences=JobPreferences(),
    )
    stale_job = JobPosting(
        title="Data Scientist",
        company="Example",
        external_id="EXAMPLE-R1",
        url="https://ats.example.com/jobs/R1/apply",
        source_url=source_url,
    )

    def ranking_for(job):
        match = JobMatch(
            job=job,
            profile_name="data",
            semantic_score=40,
            preference_score=10,
            llm_score=10,
            total_score=60,
            shortlisted=True,
        )
        return RankingResult(
            matches=[match],
            profile_embeddings={"data": [1.0]},
            job_embeddings={job.identity_key: [1.0]},
        )

    first_scan = database.start_scan(source_url)
    database.persist_ranking(
        scan_id=first_scan,
        profiles=[profile],
        result=ranking_for(stale_job),
    )
    database.finish_scan(first_scan, jobs_found=1)
    corrected_job = stale_job.model_copy(
        update={"url": "https://jobs.example.com/job/EXAMPLE-R1/data-scientist"}
    )
    second_scan = database.start_scan(source_url)
    database.persist_ranking(
        scan_id=second_scan,
        profiles=[profile],
        result=ranking_for(corrected_job),
    )
    database.finish_scan(second_scan, jobs_found=1)

    assert {record["url"] for record in database.list_matches(shortlisted_only=True)} == {
        "https://jobs.example.com/job/EXAMPLE-R1/data-scientist"
    }
    latest = database.list_matches(shortlisted_only=True, latest_scan_only=True)
    assert len(latest) == 1
    assert latest[0]["scan_id"] == second_scan


def test_embedding_cache_is_persistent_and_model_scoped(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'embedding-cache.db'}"
    database = Database(database_url)
    database.create_schema()
    database.save_cached_embeddings(
        "embeddinggemma",
        [("first input", [1.0, 0.5]), ("second input", [0.25, 0.75])],
    )

    reopened = Database(database_url)
    reopened.create_schema()

    assert reopened.load_cached_embeddings(
        "embeddinggemma",
        ["first input", "missing input", "second input"],
    ) == [[1.0, 0.5], None, [0.25, 0.75]]
    assert reopened.load_cached_embeddings("different-model", ["first input"]) == [None]
