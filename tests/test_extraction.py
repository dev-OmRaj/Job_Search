from job_search_agent.adapters.phenom import extract_phenom_jobs, phenom_next_page_urls
from job_search_agent.extraction import deduplicate_jobs, extract_json_ld_jobs
from job_search_agent.models import JobPosting, PageDocument


def test_extracts_schema_org_job_posting():
    page = PageDocument(
        url="https://example.com/jobs/42",
        source_url="https://example.com/careers",
        depth=1,
        text="Senior Backend Engineer",
        html="""
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Senior Backend Engineer",
          "description": "<p>Build APIs with Python.</p>",
          "hiringOrganization": {"name": "Example Corp"},
          "jobLocation": {"address": {"addressLocality": "Bengaluru", "addressCountry": "IN"}},
          "employmentType": "FULL_TIME",
          "url": "/jobs/42",
          "skills": "Python, SQL"
        }
        </script>
        """,
    )

    jobs = extract_json_ld_jobs(page)

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Backend Engineer"
    assert jobs[0].company == "Example Corp"
    assert jobs[0].url == "https://example.com/jobs/42"
    assert jobs[0].skills == ["Python", "SQL"]


def test_jobs_with_same_landing_url_and_different_titles_are_not_collapsed():
    jobs = [
        JobPosting(
            title="Backend Engineer",
            url="https://example.com/careers",
            source_url="https://example.com/careers",
        ),
        JobPosting(
            title="Data Scientist",
            url="https://example.com/careers",
            source_url="https://example.com/careers",
        ),
    ]

    assert len(deduplicate_jobs(jobs)) == 2


def test_extracts_embedded_phenom_jobs_and_metadata():
    page = PageDocument(
        url="https://jobs.example.com/gb/en/c/data-jobs",
        source_url="https://jobs.example.com/gb/en/c/data-jobs",
        depth=0,
        text="Data jobs",
        html="""
        <html><head>
          <meta property="og:site_name" content="Example Pharma">
          <link rel="next" href="?from=10&amp;s=1">
          <script>
          var phApp = phApp || {
            "baseUrl": "https://jobs.example.com/gb/en/"
          };
          phApp.urlMap = {"job": "job/:jobSeqNo/:title"};
          </script>
          <script>
          phApp.ddo = {
            "eagerLoadRefineSearch": {
              "status": 200,
              "hits": 1,
              "totalHits": 21,
              "data": {
                "jobs": [{
                  "jobSeqNo": "EXAMPLE123ENGB",
                  "jobId": "R123",
                  "title": "Data Scientist",
                  "location": "London, United Kingdom",
                  "type": "Full time",
                  "descriptionTeaser": "Build machine-learning products.",
                  "applyUrl": "https://example.wd5.myworkdayjobs.com/jobs/R123/apply",
                  "postedDate": "2026-09-01T00:00:00.000+0000",
                  "ml_skills": ["Python", "machine learning"]
                }]
              }
            }
          };
          </script>
        </head></html>
        """,
    )

    result = extract_phenom_jobs(page)

    assert result is not None
    assert result.total_hits == 21
    assert result.raw_job_count == 1
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "Data Scientist"
    assert result.jobs[0].company == "Example Pharma"
    assert result.jobs[0].location == "London, United Kingdom"
    assert result.jobs[0].employment_type == "Full time"
    assert result.jobs[0].skills == ["Python", "machine learning"]
    assert result.jobs[0].external_id == "EXAMPLE123ENGB"
    assert result.jobs[0].url == (
        "https://jobs.example.com/gb/en/job/EXAMPLE123ENGB/data-scientist"
    )
    assert phenom_next_page_urls(page.html, str(page.url)) == [
        "https://jobs.example.com/gb/en/c/data-jobs?from=10&s=1"
    ]


def test_non_phenom_page_does_not_activate_phenom_adapter():
    page = PageDocument(
        url="https://example.com/jobs",
        source_url="https://example.com/jobs",
        text="Ordinary jobs page",
        html="<html><body>Ordinary jobs page</body></html>",
    )

    assert extract_phenom_jobs(page) is None
    assert phenom_next_page_urls(page.html, str(page.url)) == []


def test_generates_phenom_offset_pagination_when_html_has_no_next_link():
    html = """
    <script>phApp.ddo = {"eagerLoadRefineSearch": {
      "hits": 10, "totalHits": 25, "data": {"jobs": [{"title": "First"}]}
    }};</script>
    """

    assert phenom_next_page_urls(
        html,
        "https://careers.example.com/global/en/search-results?keywords=data%20science",
    ) == ["https://careers.example.com/global/en/search-results?keywords=data+science&from=10&s=1"]
    assert phenom_next_page_urls(
        html,
        "https://careers.example.com/global/en/search-results?keywords=data&from=10&s=1",
    ) == ["https://careers.example.com/global/en/search-results?keywords=data&s=1&from=20"]


def test_does_not_generate_phenom_pagination_after_last_batch():
    html = """
    <script>phApp.ddo = {"eagerLoadRefineSearch": {
      "hits": 5, "totalHits": 25, "data": {"jobs": [{"title": "Last"}]}
    }};</script>
    """

    assert (
        phenom_next_page_urls(
            html,
            "https://careers.example.com/global/en/search-results?from=20&s=1",
        )
        == []
    )
