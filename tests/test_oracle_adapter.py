import json

from job_search_agent.adapters.oracle import extract_oracle_jobs, oracle_config
from job_search_agent.models import PageDocument


def test_detects_oracle_recruiting_cloud_configuration():
    config = oracle_config(
        """
        <html><head>
          <base href="/en/sites/Honeywell"
                data-apibaseurl="https://example.fa.ocs.oraclecloud.com:443"
                data-vanitybaseurl="https://careers.honeywell.com/"
                data-sitenumber="CX_1">
        </head></html>
        """,
        "https://careers.honeywell.com/en/sites/Honeywell/jobs",
    )

    assert config is not None
    assert config.api_origin == "https://example.fa.ocs.oraclecloud.com:443"
    assert config.site_name == "CX_1"
    assert config.search_url.endswith("/recruitingCEJobRequisitions")
    assert config.public_job_url("157215") == (
        "https://careers.honeywell.com/en/sites/Honeywell/job/157215"
    )
    assert config.search_finder(limit=100, offset=200) == (
        "findReqs;siteNumber=CX_1,limit=100,offset=200,sortBy=POSTING_DATES_DESC"
    )


def test_extracts_oracle_job_with_complete_location_and_description():
    page = PageDocument(
        url=(
            "https://example.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitions"
        ),
        source_url="https://careers.honeywell.com/en/sites/Honeywell/jobs",
        html=json.dumps(
            {
                "oracle": {
                    "api_origin": "https://example.fa.ocs.oraclecloud.com",
                    "public_origin": "https://careers.honeywell.com",
                    "locale": "en",
                    "site_number": "CX_1",
                    "company": "Honeywell",
                    "total": 1,
                    "raw_count": 1,
                    "jobs": [
                        {
                            "Id": "157215",
                            "Title": "Computer Vision Intern",
                            "PrimaryLocation": "Bangalore, Karnataka, India",
                            "secondaryLocations": [{"Name": "Pune, Maharashtra, India"}],
                            "WorkplaceType": "Hybrid",
                            "JobSchedule": "Full time",
                            "ExternalPostedStartDate": "2026-09-10T00:00:00+00:00",
                            "ExternalDescriptionStr": "<p>Build vision models.</p>",
                            "ExternalQualificationsStr": "<ul><li>Python</li></ul>",
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_oracle_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    job = result.jobs[0]
    assert job.title == "Computer Vision Intern"
    assert job.company == "Honeywell"
    assert job.location == "Bangalore, Karnataka, India; Pune, Maharashtra, India"
    assert job.work_mode == "hybrid"
    assert job.description == "Build vision models.\n\nPython"
    assert job.external_id == "157215"
    assert job.url == "https://careers.honeywell.com/en/sites/Honeywell/job/157215"


def test_regular_html_does_not_activate_oracle_adapter():
    assert oracle_config("<html></html>", "https://example.com/careers") is None
