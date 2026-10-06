import json

from job_search_agent.adapters.eurofins import (
    EurofinsConfig,
    eurofins_config,
    extract_eurofins_jobs,
    filter_eurofins_jobs,
    smartrecruiters_detail_url,
)
from job_search_agent.models import PageDocument


def test_detects_eurofins_feed_and_url_filters():
    html = """
    <input id="apiJobsURL"
           value="https://atsintegration.eurofins.com/ATSWebService.asmx/GetJobs?language=">
    """
    config = eurofins_config(
        html,
        "https://careers.eurofins.com/in/jobs?countryId=in&deptid=2,3,4,5,7",
    )

    assert config is not None
    assert config.endpoint_url.endswith("GetJobs?language=")
    assert config.filters == {
        "countryCode": ("in",),
        "departmentId": ("2", "3", "4", "5", "7"),
    }


def test_filters_feed_using_country_and_department_ids():
    config = EurofinsConfig(
        endpoint_url="https://atsintegration.eurofins.com/ATSWebService.asmx/GetJobs?language=",
        filters={"countryCode": ("in",), "departmentId": ("2", "5")},
    )
    jobs = [
        {"title": "India IT", "countryCode": "in", "departmentId": 5},
        {"title": "India Lab", "countryCode": "in", "departmentId": 9},
        {"title": "UK IT", "countryCode": "gb", "departmentId": 5},
    ]

    assert [job["title"] for job in filter_eurofins_jobs(jobs, config)] == ["India IT"]


def test_builds_smartrecruiters_detail_url():
    assert smartrecruiters_detail_url(
        {
            "applyUrl": (
                "https://jobs.smartrecruiters.com/Eurofins/"
                "744000149068379-dot-net-developer-with-angular-"
            )
        }
    ) == "https://api.smartrecruiters.com/v1/companies/Eurofins/postings/744000149068379"


def test_extracts_enriched_eurofins_job():
    page = PageDocument(
        url="https://atsintegration.eurofins.com/ATSWebService.asmx/GetJobs?language=",
        source_url=(
            "https://careers.eurofins.com/in/jobs?countryId=in&deptid=2,3,4,5,7"
        ),
        html=json.dumps(
            {
                "eurofins": {
                    "total": 1,
                    "raw_count": 1,
                    "jobs": [
                        {
                            "title": "Dot Net Developer",
                            "locationCity": "Bengaluru",
                            "countryName": "India",
                            "employmentType": "Full Time",
                            "refCode": "REF80939V",
                            "remoteJob": "false",
                            "applyUrl": (
                                "https://jobs.smartrecruiters.com/Eurofins/"
                                "744000149068379-dot-net-developer"
                            ),
                            "smartrecruitersDetail": {
                                "id": "744000149068379",
                                "name": "Dot Net Developer",
                                "refNumber": "REF80939V",
                                "postingUrl": (
                                    "https://jobs.smartrecruiters.com/Eurofins/"
                                    "744000149068379-dot-net-developer"
                                ),
                                "releasedDate": "2026-09-11T16:45:22.313Z",
                                "company": {"name": "Eurofins"},
                                "location": {
                                    "fullLocation": "Bengaluru, KA, India",
                                    "remote": False,
                                    "hybrid": True,
                                },
                                "experienceLevel": {"label": "Mid-Senior Level"},
                                "typeOfEmployment": {"label": "Full-time"},
                                "jobAd": {
                                    "sections": {
                                        "jobDescription": {
                                            "text": "<p>Requires 1-4 years of .NET experience.</p>"
                                        },
                                        "qualifications": {"text": "<p>Knowledge of Angular.</p>"},
                                    }
                                },
                            },
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_eurofins_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    job = result.jobs[0]
    assert job.company == "Eurofins"
    assert job.location == "Bengaluru, KA, India"
    assert job.work_mode == "hybrid"
    assert job.description == "Requires 1-4 years of .NET experience.\n\nKnowledge of Angular."
    assert job.external_id == "REF80939V"
    assert job.experience_level == "Mid-Senior Level"
    assert job.employment_type == "Full-time"


def test_regular_html_does_not_activate_eurofins_adapter():
    assert eurofins_config("<html></html>", "https://example.com/jobs") is None
