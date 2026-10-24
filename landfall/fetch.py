"""The only page-fetch path (BUILD-PLAN §2, CONTRACTS §6): SSRF-guarded, robots-aware, recorded."""
import ipaddress
import socket
import ssl
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import httpcore
import trafilatura

from . import config, ledger
from .cassette import Cassette

UA = "LandfallBot/0.1 (+https://github.com/landfall; brand research on request of the brand owner)"
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
TIMEOUT = {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}


class _StdlibTLS(ssl.SSLContext):
    """TLS as Python's standard library does it: verified, but no ALPN offer. Shopify/Cloudflare storefronts answer 429
    to the ALPN handshake httpcore sends by default and 200 to this (diagnosed 23 Oct 2026). Identity stays honest."""

    def set_alpn_protocols(self, protocols):
        pass


_TLS = _StdlibTLS(ssl.PROTOCOL_TLS_CLIENT)
_TLS.load_verify_locations(certifi.where())


class FetchBlocked(Exception):
    """The URL is not allowed (scheme, private address, robots)."""


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified


class _GuardedBackend(httpcore.SyncBackend):
    """Resolves at connect time and connects to the vetted IP, so DNS cannot change between check and use."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if config.ALLOW_PRIVATE_FETCH and host in config.HOST_MAP:  # synthetic-site recording only
            host, port = config.HOST_MAP[host]
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [i[4][0] for i in infos]
        if not ips:
            raise FetchBlocked(f"{host} did not resolve")
        if not config.ALLOW_PRIVATE_FETCH and any(_bad_ip(ip) for ip in ips):
            raise FetchBlocked(f"{host} resolves to a private or reserved address")
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)


def check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchBlocked("only http and https web addresses can be read")
    host = parts.hostname
    try:  # literal IPs are checked here; names are checked at connect time
        if not config.ALLOW_PRIVATE_FETCH and _bad_ip(host):
            raise FetchBlocked("private or reserved address")
    except ValueError:
        pass
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _live_get(url: str) -> dict:
    """GET with manual redirects; every hop is re-checked. Returns a JSON-able response dict."""
    hops = []
    with httpcore.ConnectionPool(network_backend=_GuardedBackend(), ssl_context=_TLS) as pool:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                url = check_url(url)
            except FetchBlocked as e:
                return {"url": url, "status": 0, "error": f"blocked: {e}", "hops": hops}
            rid = ledger.reserve("fetch", "GET", url=url)
            outcome = "aborted"  # finalised on every path, including unexpected exceptions
            try:
                with pool.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5", "Accept-Language": "en, *;q=0.5"},
                                 extensions={"timeout": TIMEOUT}) as r:
                    body = b""
                    for chunk in r.iter_stream():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    status = r.status
                    headers = {k.decode().lower(): v.decode(errors="replace") for k, v in r.headers}
                outcome = str(status)
            except FetchBlocked as e:
                outcome = "blocked"
                return {"url": url, "status": 0, "error": f"blocked: {e}", "hops": hops}
            except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError, OSError) as e:
                outcome = "transport_error"
                name = type(e).__name__
                if isinstance(e, socket.gaierror) or "gaierror" in str(e) or name == "ConnectError" and "nodename" in str(e):
                    name = "NoSuchHost"
                return {"url": url, "status": 0, "error": name, "hops": hops}
            finally:
                ledger.finalize(rid, "fetch", "GET", url=url, outcome=outcome)
            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                hops.append(url)
                url = urljoin(url, headers["location"])
                continue
            ctype = headers.get("content-type", "")
            charset = ctype.split("charset=")[-1].split(";")[0].strip() if "charset=" in ctype else "utf-8"
            return {"url": url, "status": status, "content_type": ctype, "hops": hops, "retry_after": headers.get("retry-after"),
                    "body": body.decode(charset, errors="replace") if "html" in ctype or "text" in ctype else ""}
    return {"url": url, "status": 0, "error": "too many redirects", "hops": hops}


def get(url: str, cassette: Cassette, stage: str = "") -> dict:
    url = check_url(url)
    return cassette.call("fetch", "GET", {"url": url}, lambda: _live_get(url), stage)


def robots_for(url: str, cassette: Cassette, stage: str = "") -> urllib.robotparser.RobotFileParser | None:
    """The site's parsed robots.txt, or None when there is none (no restriction stated)."""
    p = urlsplit(url)
    r = get(f"{p.scheme}://{p.netloc}/robots.txt", cassette, stage)
    if r.get("status") != 200:
        return None
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.get("body", "").splitlines())
    return rp


def robots_allows(url: str, rp: urllib.robotparser.RobotFileParser | None) -> bool:
    return rp is None or rp.can_fetch("LandfallBot", url)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.title, self._in_title = [], "", False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def site_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, site: str) -> bool:
    h = site_of(url)
    return h == site or h.endswith("." + site)


def extract(page: dict) -> dict:
    """Readable text, title and same-site links from a fetched page."""
    html = page.get("body", "")
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:
        pass
    site = site_of(page["url"])
    links = []
    for href in parser.links:
        u = urljoin(page["url"], href).split("#")[0]
        if u.startswith("http") and same_site(u, site) and u not in links:
            links.append(u)
    text = trafilatura.extract(html, include_tables=True, favor_recall=True) or ""
    return {"title": parser.title.strip(), "text": text, "links": links[:80]}
