from pathlib import Path

from sqlalchemy import inspect

from job_search_agent.company_database import CompanyDirectory


def test_company_directory_is_minimal_and_upserts_by_portal_url(tmp_path: Path):
    directory = CompanyDirectory(f"sqlite:///{tmp_path / 'companies.db'}")
    directory.create_schema()

    directory.upsert("https://careers.example.com/jobs/#openings")
    directory.upsert(
        "https://careers.example.com/jobs",
        ["Example Corporation", "Example Corporation"],
    )

    assert directory.list_portals() == [
        {
            "company_name": "Example Corporation",
            "job_portal_url": "https://careers.example.com/jobs",
        }
    ]
    inspector = inspect(directory.engine)
    assert inspector.get_table_names() == ["company_portals"]
    assert {column["name"] for column in inspector.get_columns("company_portals")} == {
        "id",
        "company_name",
        "job_portal_url",
    }


def test_company_directory_merges_filtered_urls_and_www_aliases(tmp_path: Path):
    directory = CompanyDirectory(f"sqlite:///{tmp_path / 'companies.db'}")
    directory.create_schema()

    directory.upsert(
        "https://www.amazon.jobs/content/en/job-categories/data-science?country%5B%5D=IN",
        ["ADCI - Karnataka"],
    )
    directory.upsert(
        "https://amazon.jobs/content/en/job-categories/data-science#jobs-search",
        ["Amazon Development Centre"],
    )

    assert directory.list_portals() == [
        {
            "company_name": "Amazon",
            "job_portal_url": "https://amazon.jobs/content/en/job-categories/data-science",
        }
    ]


def test_company_directory_keeps_distinct_scan_paths_and_deduplicates_repeats(
    tmp_path: Path,
):
    directory = CompanyDirectory(f"sqlite:///{tmp_path / 'companies.db'}")
    directory.create_schema()

    for _ in range(10):
        directory.upsert(
            "https://www.amazon.jobs/content/en/job-categories/data-science#jobs-search"
        )
    directory.upsert("https://amazon.jobs/content/en/job-categories/software-development/")

    assert directory.list_portals() == [
        {
            "company_name": "Amazon",
            "job_portal_url": "https://amazon.jobs/content/en/job-categories/data-science",
        },
        {
            "company_name": "Amazon",
            "job_portal_url": (
                "https://amazon.jobs/content/en/job-categories/software-development"
            ),
        },
    ]
