import json

from job_search_agent.adapters.atlassian import atlassian_config, extract_atlassian_jobs
from job_search_agent.models import PageDocument


def test_detects_atlassian_careers_component():
    config = atlassian_config(
        '<script type="text/jsx-component">{"type": "Careers", "props": {}}</script>',
        "https://www.atlassian.com/company/careers/all-jobs?",
    )

    assert config is not None
    assert config.endpoint_url == "https://www.atlassian.com/endpoint/careers/listings"


def test_does_not_activate_on_unrelated_careers_component():
    assert (
        atlassian_config(
            '{"type": "Careers"}',
            "https://example.com/company/careers/all-jobs",
        )
        is None
    )


def test_extracts_atlassian_job_with_canonical_portal_url():
    page = PageDocument(
        url="https://www.atlassian.com/endpoint/careers/listings",
        source_url="https://www.atlassian.com/company/careers/all-jobs",
        html=json.dumps(
            {
                "atlassian": {
                    "total": 1,
                    "raw_count": 1,
                    "jobs": [
                        {
                            "portalJobPost": {
                                "id": 25642,
                                "portalUrl": (
                                    "https://careers-apac-atlassian.icims.com/jobs/"
                                    "25642/account-executive/job"
                                ),
                                "updatedDate": "2026-09-11 02:49 PM",
                            },
                            "id": 25642,
                            "title": "Account Executive, Enterprise (India)",
                            "type": "Full-Time",
                            "locations": ["Remote - India - Remote", "Remote - Remote"],
                            "overview": "<p>Lead enterprise accounts.</p>",
                            "responsibilities": "<p>Develop account plans.</p>",
                            "qualifications": "<p>15 or more years of sales experience.</p>",
                            "applyUrl": "https://example.com/apply",
                        }
                    ],
                }
            }
        ),
        text="",
    )

    result = extract_atlassian_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    job = result.jobs[0]
    assert job.company == "Atlassian"
    assert job.location == "Remote - India - Remote; Remote - Remote"
    assert job.work_mode == "remote"
    assert job.description == (
        "Lead enterprise accounts.\n\nDevelop account plans.\n\n"
        "15 or more years of sales experience."
    )
    assert job.url.endswith("/jobs/25642/account-executive/job")
    assert job.external_id == "25642"
    assert job.employment_type == "Full-Time"
