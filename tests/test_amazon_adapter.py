import json

from job_search_agent.adapters.amazon import (
    amazon_request_payload,
    amazon_search_config,
    extract_amazon_jobs,
)
from job_search_agent.models import PageDocument


def amazon_cms_html(*, render_search: bool = True, filters: list[dict] | None = None) -> str:
    payload = {
        "runtimeConfig": {
            "searchEndpoint": "/api/jobs/search",
            "searchKey": "public-search-key",
        },
        "props": {
            "pageProps": {
                "content": {
                    "locale": "en",
                    "renderSearch": render_search,
                    "searchFilters": filters or [],
                }
            }
        },
    }
    return (
        '<html><script id="jobs-cms-next-data" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></html>"
    )


def test_reads_amazon_category_and_country_search_configuration():
    config = amazon_search_config(
        amazon_cms_html(filters=[{"jobCategories": ["Data Science"]}]),
        "https://amazon.jobs/content/en/job-categories/data-science?country%5B%5D=IN",
    )

    assert config is not None
    assert config.endpoint_url == "https://amazon.jobs/api/jobs/search?is_als=true"
    payload = amazon_request_payload(config, start=100)
    assert payload["start"] == 100
    assert payload["size"] == 100
    assert payload["filterFacets"] == [
        {
            "name": "category",
            "requestedFacetCount": 9999,
            "values": [{"name": "Data Science"}],
        }
    ]
    country = next(item for item in payload["locationFacets"][0] if item["name"] == "country")
    assert country["values"] == [{"name": "IN"}]


def test_category_directory_defaults_to_unfiltered_amazon_search():
    config = amazon_search_config(
        amazon_cms_html(render_search=False),
        "https://amazon.jobs/content/en/job-categories/",
    )

    assert config is not None
    assert config.request_template["filterFacets"] == []


def test_unrelated_amazon_content_page_does_not_activate_adapter():
    config = amazon_search_config(
        amazon_cms_html(render_search=False),
        "https://amazon.jobs/content/en/how-we-hire",
    )

    assert config is None


def test_extracts_structured_amazon_api_jobs_with_public_detail_urls():
    response = {
        "found": 1,
        "start": 0,
        "searchHits": [
            {
                "fields": {
                    "title": ["Data Scientist II"],
                    "icimsJobId": ["1234567"],
                    "companyName": ["Amazon Development Centre (India) Private Limited"],
                    "location": ["IN, KA, Bengaluru"],
                    "locations": [
                        json.dumps(
                            {
                                "location": "IN, KA, Bengaluru",
                                "normalizedLocation": "Bengaluru, Karnataka, India",
                                "type": "ONSITE",
                            }
                        )
                    ],
                    "scheduleTypeId": ["Full-Time"],
                    "description": ["<p>Build machine-learning products.</p>"],
                    "basicQualifications": ["Python and SQL"],
                    "updatedDate": ["1789164586"],
                }
            }
        ],
    }
    page = PageDocument(
        url="https://amazon.jobs/api/jobs/search?is_als=true&start=0&size=100",
        source_url="https://amazon.jobs/content/en/job-categories/data-science",
        html=json.dumps(response),
        text="",
    )

    result = extract_amazon_jobs(page)

    assert result is not None
    assert result.total_hits == 1
    assert result.raw_job_count == 1
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Data Scientist II"
    assert job.external_id == "1234567"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.work_mode == "onsite"
    assert job.employment_type == "Full-Time"
    assert "Python and SQL" in job.description
    assert job.url == "https://amazon.jobs/en/jobs/1234567/data-scientist-ii"
