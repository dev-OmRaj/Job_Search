import json

import httpx
import pytest

from job_search_agent.adapters.salesforce import (
    extract_salesforce_jobs,
    filter_jobs,
    original_job_url,
    salesforce_config,
)
from job_search_agent.models import PageDocument

BOARD = "https://www.salesforce.com/company/careers/jobs"


def record(job_id, country="India", employee_type="Intern"):
    return {
        "Job_Requisition_Ref_ID": job_id, "Job_Posting_Title": "Summer 2027 Intern – Software Engineer",
        "Job_Requisition_Primary_Location": f"{country} - Bangalore",
        "Countries": [country], "Employee_Type": employee_type,
        "Job_Description": "<p>Build Python services. Requires 0–2 years of experience.</p>",
        "Time_Type": "Full time", "_full_details": True,
    }


def test_original_url_filters_use_or_within_category_and_and_between_categories():
    jobs = [record("JR1"), record("JR2", employee_type="New Grads"),
            record("JR3", employee_type="Regular"), record("JR4", employee_type="Contract"),
            record("JR5", country="United States")]
    url = BOARD + "?country=India&employeeTypes=Intern&employeeTypes=New+Grads&employeeTypes=Regular&page=3"
    assert [raw["Job_Requisition_Ref_ID"] for raw in filter_jobs(jobs, url)] == ["JR1", "JR2", "JR3"]
    assert filter_jobs(jobs, BOARD + "?country=France") == []


def test_extracts_full_description_and_original_salesforce_job_url():
    page = PageDocument(url=BOARD, source_url=BOARD + "?country=India",
                        html=json.dumps({"salesforce": {"board_url": BOARD,
                            "total": 1, "coverage_complete": True, "jobs": [record("JR337715")]}}), text="")
    result = extract_salesforce_jobs(page)
    assert result is not None
    job = result.jobs[0]
    assert job.url == BOARD + "/JR337715/summer-2027-intern-software-engineer/"
    assert "0–2 years" in job.description
    assert job.description_complete
    assert job.employment_type == "internship"
    assert job.source_url == BOARD + "?country=India"
    assert result.coverage_complete


def test_listing_only_description_is_not_treated_as_complete():
    raw = record("JR1")
    raw["_full_details"] = False
    page = PageDocument(url=BOARD, source_url=BOARD,
                        html=json.dumps({"salesforce": {"board_url": BOARD,
                            "coverage_complete": True, "jobs": [raw]}}), text="")
    result = extract_salesforce_jobs(page)
    assert result is not None
    assert not result.jobs[0].description_complete
    assert not result.coverage_complete


def test_unknown_filter_does_not_silently_expand_scan_scope():
    with pytest.raises(ValueError, match="Unsupported Salesforce URL filters"):
        filter_jobs([record("JR1")], BOARD + "?unhandledFilter=Intern")


def test_detector_is_scoped_to_original_salesforce_careers_page():
    html = '<script src="https://c.salesforce.com/digital/xsf/components/v1-stable/careers.js"></script>'
    assert salesforce_config(html, BOARD + "?country=India") is not None
    assert salesforce_config(html, "https://unrelated.example/company/careers/jobs") is None
    assert salesforce_config(html, "https://www.salesforce.com/products/") is None
    assert original_job_url(BOARD, "JR1", "Développeur C++") == BOARD + "/JR1/developpeur-c/"


@pytest.mark.parametrize("full_fails", [False, True])
def test_feed_backup_and_full_detail_merge(monkeypatch, full_fails):
    from job_search_agent import crawler as crawler_module
    from job_search_agent.adapters.salesforce import SalesforceConfig
    from job_search_agent.crawler import CareersCrawler
    from job_search_agent.models import CrawlerConfig

    crawler = CareersCrawler(CrawlerConfig(delay_seconds=0), user_agent="test")
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **kwargs: url)
    monkeypatch.setattr(crawler, "_allowed_by_robots", lambda client, url: True)
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path.endswith("jobs_1.json") or (full_fails and "jobs_2" in request.url.path):
            return httpx.Response(503)
        raw = record("JR1")
        raw["Job_Description"] = "Listing summary" if "jobs_1" in request.url.path else "Full requirements and responsibilities."
        return httpx.Response(200, json={"Total_Jobs": 1, "Report_Entry": [raw]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        page, errors = crawler._fetch_salesforce_jobs(client, SalesforceConfig(BOARD), BOARD + "?country=India")
    assert page is not None
    result = extract_salesforce_jobs(page)
    assert result is not None
    assert any(url.endswith("jobs_1_backup.json") for url in requested)
    assert result.jobs[0].description_complete is (not full_fails)
    if full_fails:
        assert errors
        assert not result.coverage_complete
    else:
        assert errors == []
        assert result.jobs[0].description == "Full requirements and responsibilities."
