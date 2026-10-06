import json
from pathlib import Path

from job_search_agent.report import generate_html_report


def test_partial_scan_notice_and_escaped_warnings(tmp_path):
    source = tmp_path / "all.json"
    output = tmp_path / "report.html"
    source.write_text(json.dumps({
        "scan_id": 1, "all_jobs": [], "scan_status": "partial",
        "warnings": ["Cloudflare blocked <script>bad</script>"],
    }))
    generate_html_report(source, output, threshold=50)
    document = output.read_text()
    assert "Partial scan — results are incomplete" in document
    assert "Cloudflare blocked &lt;script&gt;bad&lt;/script&gt;" in document


def test_generates_structured_searchable_report(tmp_path: Path):
    source = tmp_path / "all.json"
    output = tmp_path / "report.html"
    source.write_text(
        json.dumps(
            {
                "scan_id": 7,
                "generated_at": "2026-09-13T00:00:00Z",
                "all_jobs": [
                    {
                        "job": {
                            "title": "AI Engineer",
                            "company": "Example & Co",
                            "location": "Bangalore, India",
                            "work_mode": "hybrid",
                            "url": "https://example.com/jobs/1",
                        },
                        "profile_name": "data",
                        "total_score": 62,
                        "shortlisted": True,
                        "hard_rejection_reason": None,
                        "rationale": "Strong match",
                    },
                    {
                        "job": {
                            "title": "Senior Engineer",
                            "company": "Example",
                            "location": "Bangalore",
                            "work_mode": "onsite",
                            "url": "https://example.com/jobs/2",
                        },
                        "profile_name": "data",
                        "total_score": 0,
                        "shortlisted": False,
                        "hard_rejection_reason": "title indicates a senior role",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    counts = generate_html_report(source, output, threshold=50)
    rendered = output.read_text(encoding="utf-8")

    assert counts == {"shortlisted": 1, "rejected": 1, "total": 2}
    assert "Job scan 7" in rendered
    assert "Example &amp; Co" not in rendered  # row data is safely JSON encoded for JS
    assert "Search title, company, location" in rendered
    assert "Seniority in title" in rendered
    assert "https://example.com/jobs/1" in rendered
