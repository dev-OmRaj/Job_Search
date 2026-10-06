import json

from job_search_agent.adapters.workday import (
    extract_workday_jobs,
    workday_config,
    workday_running_total,
)
from job_search_agent.models import PageDocument


def test_detects_workday_tenant_site_and_locale():
    config = workday_config("https://visa.wd5.myworkdayjobs.com/en-US/Visa")

    assert config is not None
    assert config.tenant == "visa"
    assert config.site == "Visa"
    assert config.locale == "en-US"
    assert config.endpoint_url == "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs"
    assert config.detail_url("/job/Bangalore/Engineer_R123") == (
        "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/job/Bangalore/Engineer_R123"
    )


def test_detects_workday_site_without_locale():
    config = workday_config("https://visa.wd5.myworkdayjobs.com/Visa")

    assert config is not None
    assert config.site == "Visa"
    assert config.public_job_url("/job/Bangalore/Engineer_R123") == (
        "https://visa.wd5.myworkdayjobs.com/en-US/Visa/job/Bangalore/Engineer_R123"
    )


def test_extracts_enriched_workday_job():
    page = PageDocument(
        url="https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs",
        source_url="https://visa.wd5.myworkdayjobs.com/Visa",
        html=json.dumps(
            {
                "workday": {
                    "company": "Visa",
                    "total": 1,
                    "raw_count": 1,
                    "jobs": [
                        {
                            "title": "Software Engineer",
                            "locationsText": "Bangalore, India",
                            "postedOn": "Posted Yesterday",
                            "bulletFields": ["R123"],
                            "publicUrl": (
                                "https://visa.wd5.myworkdayjobs.com/en-US/Visa/"
                                "job/Bangalore/Engineer_R123"
                            ),
                            "jobPostingInfo": {
                                "title": "Software Engineer",
                                "location": "Bangalore, India",
                                "additionalLocations": ["Mumbai, India"],
                                "jobDescription": "<p>Hybrid role building Python services.</p>",
                                "jobReqId": "R123",
                                "timeType": "Full time",
                                "postedOn": "Posted Yesterday",
                            },
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_workday_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Software Engineer"
    assert job.location == "Bangalore, India; Mumbai, India"
    assert job.work_mode == "hybrid"
    assert job.description == "Hybrid role building Python services."
    assert job.external_id == "R123"
    assert job.employment_type == "Full time"


def test_non_workday_payload_does_not_activate_adapter():
    page = PageDocument(
        url="https://example.com/jobs",
        source_url="https://example.com/careers",
        html='{"jobs": []}',
        text="",
    )

    assert extract_workday_jobs(page) is None


def test_pagination_preserves_first_page_total_when_later_pages_report_zero():
    total = workday_running_total(0, {"total": 760}, offset=0, returned=20)
    total = workday_running_total(total, {"total": 0}, offset=20, returned=20)
    total = workday_running_total(total, {"total": 0}, offset=40, returned=20)

    assert total == 760


def test_pagination_total_never_falls_below_loaded_result_count():
    assert workday_running_total(0, {}, offset=40, returned=20) == 60
