from job_search_agent.adapters.successfactors import (
    extract_successfactors_jobs,
    successfactors_config,
)
from job_search_agent.models import PageDocument

HTML = """
<html><head><title>FUJITSU LIMITED Jobs</title>
<script src="/platform/js/j2w/min/j2w.searchManager.min.js"></script></head><body>
<div>1 to 1 of 119 results</div>
<li data-testid="jobCard">
  <a data-testid="jobCardTitle_0" aria-label="Data Engineer, job posting 1 of 119"
     href="/job/Bangalore-Data-Engineer/12345-en_US">Data Engineer</a>
  <div data-testid="jobCardLocation">Bangalore, KA, IND,</div>
  <span data-help-id="jobCardFooterValue_0">India</span>
  <span data-help-id="jobCardFooterValue_0">9/12/26</span>
</li></body></html>
"""


def test_detects_successfactors_and_uses_search_route():
    config = successfactors_config(HTML, "https://jobs.example.com/")

    assert config is not None
    assert config.search_url == "https://jobs.example.com/search/"


def test_extracts_rendered_successfactors_card_and_total():
    result = extract_successfactors_jobs(
        PageDocument(
            url="https://jobs.example.com/search/?searchResultView=LIST",
            source_url="https://jobs.example.com/",
            html=HTML,
            text="",
        )
    )

    assert result is not None
    assert result.total_hits == 119
    assert result.raw_job_count == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Data Engineer"
    assert job.company == "FUJITSU LIMITED"
    assert job.location == "Bangalore, KA, IND, India"
    assert job.url == "https://jobs.example.com/job/Bangalore-Data-Engineer/12345-en_US"
    assert job.external_id == "12345"
    assert job.posted_at == "9/12/26"


def test_ordinary_page_does_not_activate_adapter():
    assert successfactors_config("<h1>Careers</h1>", "https://example.com/") is None


def test_successfactors_page_url_preserves_existing_filters():
    from job_search_agent.crawler import CareersCrawler

    url = CareersCrawler._successfactors_page_url(
        "https://jobs.example.com/search/?q=data&locationsearch=India&pageNumber=8",
        2,
    )

    assert url == (
        "https://jobs.example.com/search/?q=data&locationsearch=India&"
        "pageNumber=2&searchResultView=LIST"
    )
