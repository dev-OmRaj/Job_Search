import json

from job_search_agent.adapters.greenhouse import (
    extract_greenhouse_jobs,
    greenhouse_jobs_config,
)
from job_search_agent.models import PageDocument


def test_discovers_standard_greenhouse_board_api():
    config = greenhouse_jobs_config(
        """
        <iframe src="https://boards.greenhouse.io/embed/job_board?for=examplecompany">
        </iframe>
        """,
        "https://example.com/careers",
    )

    assert config is not None
    assert config.board_token == "examplecompany"
    assert config.source_kind == "public_api"
    assert config.endpoint_url == (
        "https://boards-api.greenhouse.io/v1/boards/examplecompany/jobs?content=true"
    )


def test_discovers_graviton_style_local_greenhouse_mirror():
    config = greenhouse_jobs_config(
        """
        <div id="gh-board"></div>
        <script src="javascripts/greenhouse-embed-local.js"></script>
        """,
        "https://www.gravitontrading.com/greenhouse-embed-local.html",
    )

    assert config is not None
    assert config.board_token is None
    assert config.source_kind == "local_mirror"
    assert config.endpoint_url == ("https://www.gravitontrading.com/data/greenhouse_jobs.json")


def test_extracts_standard_greenhouse_api_job():
    page = PageDocument(
        url=(
            "https://boards-api.greenhouse.io/v1/boards/gravitonresearchcapital/jobs?content=true"
        ),
        source_url="https://www.gravitontrading.com/careers.html",
        html=json.dumps(
            {
                "jobs": [
                    {
                        "id": 5099344002,
                        "title": "Software Engineer",
                        "company_name": "Graviton Research Capital LLP",
                        "absolute_url": (
                            "https://boards.greenhouse.io/gravitonresearchcapital/jobs/5099344002"
                        ),
                        "location": {"name": "Gurugram, Haryana, India"},
                        "content": "&lt;p&gt;Build Python systems.&lt;/p&gt;",
                        "first_published": "2026-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        text="",
    )

    result = extract_greenhouse_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Software Engineer"
    assert job.company == "Graviton Research Capital LLP"
    assert job.location == "Gurugram, Haryana, India"
    assert job.description == "Build Python systems."
    assert job.external_id == "5099344002"
    assert job.url.endswith("/jobs/5099344002")


def test_extracts_local_greenhouse_mirror_job_details():
    page = PageDocument(
        url="https://www.gravitontrading.com/data/greenhouse_jobs.json",
        source_url="https://www.gravitontrading.com/careers.html",
        html=json.dumps(
            {
                "board": {"name": "Graviton Research Capital LLP"},
                "jobs": [
                    {
                        "title": "Machine Learning Researcher",
                        "url": (
                            "https://boards.greenhouse.io/gravitonresearchcapital/jobs/8456543002"
                        ),
                        "gh_jid": "8456543002",
                        "location_text": "Gurugram, Haryana, India",
                        "details": {
                            "description_text": "Research machine-learning systems.",
                            "fetched_at": "2026-09-11T00:00:00Z",
                        },
                    }
                ],
            }
        ),
        text="",
    )

    result = extract_greenhouse_jobs(page)

    assert result is not None
    assert len(result.jobs) == 1
    assert result.jobs[0].company == "Graviton Research Capital LLP"
    assert result.jobs[0].external_id == "8456543002"
    assert result.jobs[0].description == "Research machine-learning systems."


def test_non_greenhouse_json_does_not_activate_adapter():
    page = PageDocument(
        url="https://example.com/api/jobs",
        source_url="https://example.com/careers",
        html='{"jobs": [{"title": "Ordinary job", "url": "/jobs/1"}]}',
        text="",
    )

    assert extract_greenhouse_jobs(page) is None
