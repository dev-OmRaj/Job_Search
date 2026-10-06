import pytest

from job_search_agent.url_policy import UnsafeUrlError, ensure_public_http_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/jobs",
        "http://127.0.0.1/jobs",
        "http://169.254.169.254/latest/meta-data",
        "ftp://example.com/jobs",
    ],
)
def test_blocks_non_public_urls(url):
    with pytest.raises(UnsafeUrlError):
        ensure_public_http_url(url, resolve_dns=False)


def test_accepts_public_https_shape_without_dns_lookup():
    assert (
        ensure_public_http_url("https://careers.example.com/jobs", resolve_dns=False)
        == "https://careers.example.com/jobs"
    )
