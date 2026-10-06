import json

from job_search_agent.adapters.keka import (
    extract_keka_jobs,
    keka_config,
    keka_endpoint_urls,
)
from job_search_agent.models import PageDocument


def test_detects_keka_bootstrap_and_portal_configuration():
    bootstrap = """
    <script>fetch('/ats/documents/org/careerportal/page.html')</script>
    """
    config = keka_config(bootstrap, "https://example.keka.com/careers/")

    assert config is not None
    assert config.portal_document_url == (
        "https://example.keka.com/ats/documents/org/careerportal/page.html"
    )

    portal = """
    <script>const khConfig = {identifier: 'org-id',
    domain: 'https://example.keka.com/careers/', targetContainer: '#jobs'}</script>
    <script src="https://example.keka.com/careers/api/embedjobs/js/org-id"></script>
    """
    config = keka_config(portal, "https://example.keka.com/portal.html")

    assert config is not None
    assert config.identifier == "org-id"
    assert keka_endpoint_urls(config) == (
        "https://example.keka.com/careers/api/embedjobs/default/active/org-id",
        "https://example.keka.com/careers/api/organization/default/careerportalinfo",
    )


def test_extracts_complete_keka_job_record():
    payload = {
        "provider": "keka",
        "company": "Example",
        "domain": "https://example.keka.com/careers/",
        "portal_name": "default",
        "jobs": [
            {
                "id": 123,
                "title": "SDE Intern",
                "description": "<p>Work From Office using Python.</p>",
                "jobLocations": [{"name": "Gurugram, Haryana", "countryName": "India"}],
                "jobType": 2,
                "experience": "0 - 1 Year",
                "salaryRangeFormat": "INR 1,00,000 - 2,00,000",
                "publishedOn": "2026-08-20T08:34:30Z",
                "skillNames": ["Python"],
            }
        ],
    }
    page = PageDocument(
        url="https://example.keka.com/careers/api/embedjobs/default/active/org-id",
        source_url="https://example.keka.com/careers/",
        html=json.dumps(payload),
        text="",
    )

    result = extract_keka_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "SDE Intern"
    assert job.company == "Example"
    assert job.location == "Gurugram, Haryana, India"
    assert job.work_mode == "onsite"
    assert job.url == "https://example.keka.com/careers/jobdetails/123"
    assert job.external_id == "123"
    assert job.employment_type == "Full Time"
    assert job.experience_level == "0 - 1 Year"
    assert job.skills == ["Python"]


def test_non_keka_json_does_not_activate_adapter():
    page = PageDocument(
        url="https://example.com/jobs.json",
        source_url="https://example.com/careers",
        html='{"jobs": []}',
        text="",
    )

    assert extract_keka_jobs(page) is None
