import json

from job_search_agent.adapters.nextjs import extract_nextjs_jobs
from job_search_agent.models import PageDocument


def _page(records, source_url="https://jobs.example.com/en/jobs?locations=India"):
    chunk = json.dumps({"jobs": records}, separators=(",", ":"))
    flight = json.dumps([1, chunk])
    return PageDocument(
        url="https://jobs.example.com/en/jobs?locations=India",
        source_url=source_url,
        html=(
            "<html><head><title>Working at Example | Jobs and Careers at EXAMPLE</title>"
            f"</head><body><script>self.__next_f.push({flight})</script></body></html>"
        ),
        text="",
    )


def test_extracts_and_filters_embedded_nextjs_jobs():
    records = [
        {
            "instance_id": "R100-0",
            "job_title": "Associate Engineer",
            "job_req_id": "R100",
            "job_location": {
                "address": {
                    "city": "Bengaluru",
                    "region": "Karnataka",
                    "country": "India",
                }
            },
            "hiring_organization": {"name": ""},
            "employment_unit": "Software Development",
            "employment_type": "Full time; Hybrid",
            "date_posted": "2026-09-01",
            "job_profile": "Engineer 1",
        },
        {
            "instance_id": "R200-0",
            "job_title": "Senior Engineer",
            "job_req_id": "R200",
            "job_location": {
                "address": {"city": "London", "region": "England", "country": "UK"}
            },
            "hiring_organization": {"name": ""},
            "employment_type": "Office-based",
        },
    ]

    result = extract_nextjs_jobs(_page(records))

    assert result is not None
    assert result.raw_job_count == 2
    assert result.total_hits == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Associate Engineer"
    assert job.company == "EXAMPLE"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.work_mode == "hybrid"
    assert job.url == "https://jobs.example.com/en/jobs/R100-0"
    assert job.external_id == "R100-0"


def test_non_job_nextjs_data_does_not_activate_adapter():
    assert extract_nextjs_jobs(_page([{"title": "Article"}])) is None
