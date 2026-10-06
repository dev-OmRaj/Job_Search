import json

from job_search_agent.adapters.ashby import ashby_config, extract_ashby_jobs
from job_search_agent.models import PageDocument


def test_detects_embedded_ashby_job_from_company_url():
    config = ashby_config(
        "",
        "https://lgads.tv/careers/?ashby_jid=02b11bae-83d2-47f5-9ff2-6114f13ee360",
    )

    assert config is not None
    assert config.board_name == "lgads"
    assert config.endpoint_url == "https://api.ashbyhq.com/posting-api/job-board/lgads"


def test_detects_direct_ashby_board_url():
    config = ashby_config("", "https://jobs.ashbyhq.com/lgads/02b11bae")

    assert config is not None
    assert config.board_name == "lgads"


def test_extracts_ashby_job():
    page = PageDocument(
        url="https://api.ashbyhq.com/posting-api/job-board/lgads",
        source_url=(
            "https://lgads.tv/careers/"
            "?ashby_jid=02b11bae-83d2-47f5-9ff2-6114f13ee360"
        ),
        html=json.dumps(
            {
                "ashby": {
                    "board_name": "lgads",
                    "company": "LG Ad Solutions",
                    "total": 1,
                    "raw_count": 1,
                    "jobs": [
                        {
                            "id": "02b11bae-83d2-47f5-9ff2-6114f13ee360",
                            "title": "Software Engineer I",
                            "employmentType": "FullTime",
                            "location": "Bangalore, India",
                            "secondaryLocations": [],
                            "publishedAt": "2026-08-25T14:10:53.991+00:00",
                            "isListed": True,
                            "isRemote": False,
                            "workplaceType": "OnSite",
                            "jobUrl": (
                                "https://jobs.ashbyhq.com/lgads/"
                                "02b11bae-83d2-47f5-9ff2-6114f13ee360"
                            ),
                            "descriptionPlain": "Build large-scale advertising systems.",
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_ashby_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    job = result.jobs[0]
    assert job.title == "Software Engineer I"
    assert job.company == "LG Ad Solutions"
    assert job.location == "Bangalore, India"
    assert job.work_mode == "onsite"
    assert job.external_id == "02b11bae-83d2-47f5-9ff2-6114f13ee360"
    assert job.url.endswith("/lgads/02b11bae-83d2-47f5-9ff2-6114f13ee360")


def test_unrelated_url_does_not_activate_ashby_adapter():
    assert ashby_config("<html></html>", "https://example.com/careers") is None
