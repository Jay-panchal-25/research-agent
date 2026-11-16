"""SSRF guards (stdlib only)."""
import ipaddress
from urllib.parse import urlparse


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def check_url_shape(url: str) -> tuple[str, int]:
    """Only http/https on standard ports, with a hostname. Returns (host, port)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("only http/https URLs are allowed")
    if p.username or p.password:
        raise ValueError("credentials in URL are not allowed")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("non-standard port blocked")
    return p.hostname, port
