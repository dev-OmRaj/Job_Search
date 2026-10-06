import json

from job_search_agent.adapters.eightfold import eightfold_config, extract_eightfold_jobs
from job_search_agent.models import PageDocument


def test_detects_eightfold_group():
    config = eightfold_config(
        '<script>window._EF_GROUP_ID = "qualcomm.com";</script>'
        '<script src="https://static.vscdn.net/app.js"></script>',
        "https://careers.qualcomm.com/careers",
    )

    assert config is not None
    assert config.group_id == "qualcomm.com"
    assert config.search_url == "https://careers.qualcomm.com/api/pcsx/search"
    assert "start=20" in config.search_page_url(20)


def test_extracts_enriched_eightfold_job():
    page = PageDocument(
        url="https://careers.qualcomm.com/api/pcsx/search",
        source_url="https://careers.qualcomm.com/careers",
        html=json.dumps(
            {
                "eightfold": {
                    "origin": "https://careers.qualcomm.com",
                    "company": "Qualcomm",
                    "total": 1,
                    "raw_count": 1,
                    "positions": [
                        {
                            "id": 446720933648,
                            "displayJobId": "3095927",
                            "name": "Engineer, Associate",
                            "locations": ["Hyderabad, Telangana, India"],
                            "postedTs": 1789257600,
                            "workLocationOption": "onsite",
                            "positionUrl": "/careers/job/446720933648",
                            "jobDescription": "<p>Build and test wireless systems.</p>",
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_eightfold_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    assert result.missing_detail_count == 0
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Engineer, Associate"
    assert job.company == "Qualcomm"
    assert job.location == "Hyderabad, Telangana, India"
    assert job.work_mode == "onsite"
    assert job.description == "Build and test wireless systems."
    assert job.external_id == "3095927"
    assert job.url == "https://careers.qualcomm.com/careers/job/446720933648"


def test_regular_html_does_not_activate_eightfold():
    assert eightfold_config("<html></html>", "https://example.com/careers") is None
