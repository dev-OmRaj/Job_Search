from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL could access a local or private network."""


def ensure_public_http_url(url: str, *, resolve_dns: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("Only absolute public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs containing credentials are not allowed")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeUrlError("Localhost URLs are not allowed")

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip and not literal_ip.is_global:
        raise UnsafeUrlError("Private, loopback, and link-local IP addresses are not allowed")

    if resolve_dns:
        try:
            default_port = 443 if parsed.scheme == "https" else 80
            addresses = {
                item[4][0] for item in socket.getaddrinfo(host, parsed.port or default_port)
            }
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"Could not resolve host {host!r}") from exc
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise UnsafeUrlError(f"Host {host!r} resolves to a non-public address")
    return url
