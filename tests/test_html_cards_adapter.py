from job_search_agent.adapters.html_cards import (
    extract_structured_html_jobs,
    html_job_complete_url,
    is_structured_html_job_listing,
)
from job_search_agent.models import PageDocument

HTML = """
<html><head><title>Job Listing - KPIT</title></head><body>
<div class="job-div"><div class="search_job_box">
  <h3>Executive - Finance</h3>
  <span class="job-loaction">Pune</span>
  <span class="job-time">Full Time</span>
  <span class="job-exp">Exp 0-1 years</span>
  <p class="skills-txt">Communication, MS Office</p>
  <a class="job-apply" href="https://apply.example.com/#/Career%20Portal/84080">Apply</a>
</div></div>
<a class="loadmore" href="/job-listing/?show_all=1">Load More</a>
</body></html>
"""


def test_detects_cards_and_show_all_url():
    assert is_structured_html_job_listing(HTML)
    assert html_job_complete_url(HTML, "https://example.com/job-listing/") == (
        "https://example.com/job-listing/?show_all=1"
    )


def test_extracts_structured_html_job_card():
    page = PageDocument(
        url="https://example.com/job-listing/?show_all=1",
        source_url="https://example.com/job-listing/",
        html=HTML,
        text="",
    )

    result = extract_structured_html_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    job = result.jobs[0]
    assert job.title == "Executive - Finance"
    assert job.company == "KPIT"
    assert job.location == "Pune"
    assert job.external_id == "84080"
    assert job.employment_type == "Full Time"
    assert job.experience_level == "0-1 years"
    assert job.skills == ["Communication", "MS Office"]


def test_ordinary_html_does_not_activate_adapter():
    assert not is_structured_html_job_listing("<h1>Careers</h1>")
