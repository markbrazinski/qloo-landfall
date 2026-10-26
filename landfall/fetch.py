"""The only page-fetch path (BUILD-PLAN §2, CONTRACTS §6): SSRF-guarded, robots-aware, recorded."""
import html
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


DOH = "https://1.1.1.1/dns-query"  # Cloudflare DNS over HTTPS, addressed by IP so it needs no lookup itself


def _doh(host: str) -> list[str]:
    """A records from encrypted DNS. Free, no key; used only when the system lookup fails."""
    import httpx
    r = httpx.get(DOH, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"}, timeout=5)
    return [a["data"] for a in r.json().get("Answer") or [] if a.get("type") == 1]


def _resolve(host: str, port: int) -> list[str]:
    """The system resolver first. If it gives no answer, encrypted DNS: some networks intercept plain DNS and fail
    real sites (stubbornsoda.com on the owner's network, 24 Oct 2026). Every address still passes the private-address
    check and is pinned for the connection. ponytail: one DoH provider; add a second if Cloudflare is ever down."""
    try:
        return [i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except socket.gaierror:
        try:
            ips = _doh(host)
        except Exception:
            ips = []
        if not ips:
            raise
        return ips


class _GuardedBackend(httpcore.SyncBackend):
    """Resolves at connect time and connects to the vetted IP, so DNS cannot change between check and use."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if config.ALLOW_PRIVATE_FETCH and host in config.HOST_MAP:  # synthetic-site recording only
            host, port = config.HOST_MAP[host]
        ips = _resolve(host, port)
        if not ips:
            raise FetchBlocked(f"{host} did not resolve")
        if not config.ALLOW_PRIVATE_FETCH and any(_bad_ip(ip) for ip in ips):
            raise FetchBlocked(f"{host} resolves to a private or reserved address")
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)


def check_url(url: str) -> str:
    """The one URL validator. Anything unparseable (e.g. 'https://[broken') is an invalid reference, never a crash."""
    try:
        parts = urlsplit(str(url).strip())
        parts.hostname, parts.port  # both parse lazily and can raise ValueError
    except ValueError:
        raise FetchBlocked("not a valid web address") from None
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
                try:
                    url = urljoin(url, headers["location"])
                except ValueError:
                    return {"url": url, "status": 0, "error": "blocked: redirect to an invalid address", "hops": hops}
                continue
            ctype = headers.get("content-type", "")
            charset = ctype.split("charset=")[-1].split(";")[0].strip() if "charset=" in ctype else "utf-8"
            return {"url": url, "status": status, "content_type": ctype, "hops": hops, "retry_after": headers.get("retry-after"),
                    "body": body.decode(charset, errors="replace") if "html" in ctype or "text" in ctype else ""}
    return {"url": url, "status": 0, "error": "too many redirects", "hops": hops}


def _guard_route(route, seen: dict) -> None:
    """Every request the rendered page makes: http(s) only, no images/media/fonts, never a private address."""
    req = route.request
    parts = urlsplit(req.url)
    if parts.scheme in ("data", "blob"):
        return route.continue_()
    if parts.scheme not in ("http", "https") or req.resource_type in ("image", "media", "font") or not parts.hostname:
        return route.abort()
    host = parts.hostname
    if host not in seen:
        try:
            seen[host] = config.ALLOW_PRIVATE_FETCH or not any(_bad_ip(ip) for ip in _resolve(host, parts.port or 443))
        except (OSError, ValueError):
            seen[host] = False
    return route.continue_() if seen[host] else route.abort()


def _live_render(url: str) -> dict:
    """Render one page in headless Chromium (pages that are JavaScript shells). The page's own host is pinned to the
    vetted address; every other request passes _guard_route. Counted as one fetch."""
    from playwright.sync_api import sync_playwright
    host = urlsplit(url).hostname or ""
    if config.ALLOW_PRIVATE_FETCH and host in config.HOST_MAP:
        target = "%s:%d" % config.HOST_MAP[host]
    else:
        try:
            ips = _resolve(host, 443)
        except OSError:
            return {"url": url, "status": 0, "error": "NoSuchHost"}
        if not config.ALLOW_PRIVATE_FETCH and any(_bad_ip(ip) for ip in ips):
            return {"url": url, "status": 0, "error": "blocked: private or reserved address"}
        target = ips[0]
    rid = ledger.reserve("fetch", "RENDER", url=url)
    outcome = "aborted"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[f"--host-resolver-rules=MAP {host} {target}"])
            try:
                page = browser.new_context(user_agent=UA).new_page()
                seen: dict = {host: True}
                page.route("**/*", lambda route: _guard_route(route, seen))
                resp = page.goto(url, wait_until="load", timeout=20000)
                page.wait_for_timeout(1500)  # let client-side rendering finish
                status, body, final = (resp.status if resp else 0), page.content(), page.url
            finally:
                browser.close()
        outcome = str(status)
        return {"url": final, "status": status, "content_type": "text/html", "hops": [], "rendered": True,
                "body": body[:MAX_BYTES]}
    except Exception as e:
        outcome = "transport_error"
        return {"url": url, "status": 0, "error": type(e).__name__}
    finally:
        ledger.finalize(rid, "fetch", "RENDER", url=url, outcome=outcome)


def render(url: str, cassette: Cassette, stage: str = "") -> dict:
    url = check_url(url)
    return cassette.call("fetch", "RENDER", {"url": url}, lambda: _live_render(url), stage)


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
        self.titles: list[str] = []  # each <title> separately; `title` keeps every one joined, as slice 1 read it

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        self._in_title = tag == "title"
        if self._in_title:
            self.titles.append("")

    def handle_endtag(self, tag):
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            self.titles[-1] += data


def site_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""  # an unparseable address belongs to no site
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, site: str) -> bool:
    h = site_of(url)
    return h == site or h.endswith("." + site)


# Off-site links that are never brand facts: social profiles, app stores, payments, maps, consent and review widgets.
NOT_BRAND_PAGES = ("instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com", "x.com", "youtube.com",
                   "youtu.be", "linkedin.com", "pinterest.com", "threads.net", "snapchat.com", "apps.apple.com",
                   "play.google.com", "google.com", "goo.gl", "wa.me", "whatsapp.com", "spotify.com", "paypal.com",
                   "klarna.com", "shopify.com", "shop.app", "onetrust.com", "cookiebot.com", "trustpilot.com", "vimeo.com")


def outbound(page_url: str, hrefs: list[str], limit: int = 15) -> list[str]:
    """Pages on other sites that a page links to, minus social, app-store, payment and widget links."""
    site, out = site_of(page_url), []
    for href in hrefs:
        try:
            u = urljoin(page_url, href).split("#")[0]
            h = site_of(u)
        except ValueError:
            continue
        if u.startswith("http") and h and not same_site(u, site) and u not in out and \
                not any(h == d or h.endswith("." + d) for d in NOT_BRAND_PAGES):
            out.append(u)
    return out[:limit]


def sitemap_urls(xml: str) -> list[str]:
    """The <loc> entries of a sitemap or sitemap index."""
    import re
    return [html.unescape(u.strip()) for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "")]


def needs_render(ex: dict) -> bool:
    """An almost empty page with almost no links is likely a JavaScript shell. Ordinary short pages (a store locator
    with 46 links, a 300-character FAQ) are not: those read as they are."""
    n = len(ex.get("text") or "")
    return n < 150 or (n < 400 and len(ex.get("links") or []) < 3)


def extract(page: dict) -> dict:
    """Readable text, title, same-site links and off-site links from a fetched page."""
    html = page.get("body", "")
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:
        pass
    site = site_of(page["url"])
    links = []
    for href in parser.links:
        try:
            u = urljoin(page["url"], href).split("#")[0]
        except ValueError:
            continue
        if u.startswith("http") and same_site(u, site) and u not in links:
            links.append(u)
    text = trafilatura.extract(html, include_tables=True, favor_recall=True) or ""
    # first_title: the document title alone. An icon's <title> (Stubborn Soda, GOT BAG) is joined into `title`, which
    # the Brand reader's recorded requests contain, so that stays unchanged; the Qloo name lookup uses first_title.
    first = " ".join(parser.titles[0].split()) if parser.titles else ""
    return {"title": parser.title.strip(), "first_title": first, "text": text, "links": links[:80],
            "outbound": outbound(page["url"], parser.links)}
