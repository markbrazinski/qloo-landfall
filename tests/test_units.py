"""Component checks: quote existence, fetch guard, cassette, ledger."""
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from landfall import config, fetch, ledger
from landfall.brand import quote_found
from landfall.cassette import Cassette, NoRecording, key_of

PAGE = "Tidewell is sparkling cold-brewed tea, brewed slowly in Portland. We ship within the US only — not abroad yet."


@pytest.mark.parametrize("quote,ok", [
    ("brewed slowly in Portland", True),                        # exact
    ("BREWED   slowly in\nportland", True),                     # case and whitespace
    ("We ship within the US only - not abroad yet", True),      # dash variants and punctuation
    ("We ship worldwide", False),                               # fabricated
    ("the US", False),                                          # on the page, but too short to prove anything
])
def test_quote_existence(quote, ok):
    assert quote_found(quote, PAGE) is ok


def test_paraphrased_value_with_exact_quote_is_allowed():
    # Only the quote must match the page; the claim value ("Ships to the United States only") may paraphrase.
    assert quote_found("We ship within the US only", PAGE)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.8/",
                                 "http://[::1]/", "file:///etc/passwd", "ftp://example.com/"])
def test_private_and_non_web_addresses_are_refused(url):
    with pytest.raises(fetch.FetchBlocked):
        fetch.check_url(url)


def test_names_resolving_to_private_addresses_are_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    r = fetch._live_get("https://rebinding.example/")
    assert r["status"] == 0 and r["error"].startswith("blocked")


def test_redirect_to_metadata_address_is_refused(monkeypatch):
    """A public page that redirects to the instance metadata address is stopped at that hop."""
    import http.server
    import threading

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    port = srv.server_address[1]
    # Treat the test server as public so the first hop is allowed; the redirect target must still be refused.
    real = fetch._bad_ip
    monkeypatch.setattr(fetch, "_bad_ip", lambda ip: False if ip == "127.0.0.1" else real(ip))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", port))])
    r = fetch._live_get(f"http://public-looking.example:{port}/")
    srv.server_close()
    assert r["status"] == 0 and "blocked" in r["error"] and r["hops"]


def test_cassette_keys_are_order_independent_and_replay_misses_raise(tmp_path):
    assert key_of("q", "/x", {"a": 1, "b": 2}) == key_of("q", "/x", {"b": 2, "a": 1})
    rec = Cassette("record", tmp_path)
    assert rec.call("fetch", "GET", {"url": "u"}, lambda: {"status": 200}) == {"status": 200}
    rep = Cassette("replay", replay_dirs=[tmp_path])
    assert rep.call("fetch", "GET", {"url": "u"}, lambda: 1 / 0) == {"status": 200}  # never calls live
    with pytest.raises(NoRecording):
        rep.call("fetch", "GET", {"url": "other"}, lambda: 1 / 0)


def test_cassette_survives_unicode_line_separators(tmp_path):
    rec = Cassette("record", tmp_path)
    rec.call("fetch", "GET", {"url": "u"}, lambda: {"body": "a b\x85c"})
    assert Cassette("replay", replay_dirs=[tmp_path]).call("fetch", "GET", {"url": "u"}, None) == {"body": "a b\x85c"}


def test_ledger_caps_floor_expiry_and_notice(monkeypatch):
    monkeypatch.setattr(config, "CAP_FETCH", 5)
    for _ in range(4):
        ledger.finalize(ledger.reserve("fetch", "GET"), "fetch", "GET", outcome="200")
    notices = [json.loads(line) for line in config.LEDGER_PATH.read_text().split("\n") if '"notice"' in line]
    assert notices and "80% of fetch" in notices[0]["text"]
    ledger.finalize(ledger.reserve("fetch", "GET"), "fetch", "GET", outcome="200")
    with pytest.raises(ledger.BudgetExhausted):
        ledger.reserve("fetch", "GET")
    ledger.record("qloo", "/search", month_remaining=999)
    with pytest.raises(ledger.BudgetExhausted, match="floor"):
        ledger.reserve("qloo", "/search")
    monkeypatch.setattr(config, "EXPIRES", datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(ledger.BudgetExhausted, match="expired"):
        ledger.reserve("bedrock", "Converse", exposure_usd=0.01)


def test_concurrent_admissions_cannot_exceed_a_cap(monkeypatch):
    """Review finding 4: two interleaved admissions with cap 1 both succeeded. Reservation is atomic now."""
    import threading
    monkeypatch.setattr(config, "CAP_FETCH", 1)
    admitted, refused, gate = [], [], threading.Barrier(8)

    def worker():
        gate.wait()
        try:
            admitted.append(ledger.reserve("fetch", "GET"))
        except ledger.BudgetExhausted:
            refused.append(1)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(admitted) == 1 and len(refused) == 7
    assert ledger.totals()["fetch"] == 1 and ledger.totals()["unresolved"] == 1


def test_usd_reservation_counts_exposure_before_dispatch(monkeypatch):
    monkeypatch.setattr(config, "CAP_USD", 0.05)
    ledger.reserve("bedrock", "Converse", exposure_usd=0.04)  # in flight, unresolved
    with pytest.raises(ledger.BudgetExhausted, match="usd cap"):
        ledger.reserve("bedrock", "Converse", exposure_usd=0.02)
    t = ledger.totals()
    assert t["unresolved"] == 1 and t["unresolved_usd"] == pytest.approx(0.04) and t["usd"] == pytest.approx(0.04)


def test_bedrock_transport_failure_is_still_an_accounted_attempt(tmp_path, monkeypatch):
    """Review finding 4: a simulated http.send timeout recorded zero attempts."""
    from types import SimpleNamespace

    from botocore.exceptions import ReadTimeoutError

    from landfall import nova

    class FakeHTTP:
        def __init__(self, **kw):
            pass

        def send(self, request):
            raise ReadTimeoutError(endpoint_url="https://bedrock.invalid/")

    monkeypatch.setattr(nova, "URLLib3Session", FakeHTTP)
    monkeypatch.setattr(nova, "_session", lambda cas, stage="": nova.boto3.Session(
        aws_access_key_id="x", aws_secret_access_key="x", region_name="us-east-1"))
    m = nova.make_model(Cassette("record", tmp_path / "rec"), "timeout_test")
    body = json.dumps({"inferenceConfig": {"maxTokens": 1000}}).encode()
    with pytest.raises(ReadTimeoutError):
        m.client.meta.events.emit("before-send.bedrock-runtime.Converse",
                                  request=SimpleNamespace(body=body, url="https://bedrock.invalid/model/m/converse"))
    t = ledger.totals()
    assert t["bedrock_calls"] == 1 and t["unresolved"] == 0
    assert t["usd"] == pytest.approx(nova._exposure(body, json.loads(body)), abs=1e-6)  # full exposure kept, not zero
    last = json.loads(config.LEDGER_PATH.read_text().strip().split("\n")[-1])
    assert last["outcome"] == "transport_error" and last["cost_known"] is False


def test_qloo_retries_account_every_attempt(monkeypatch):
    import httpx

    from landfall import qloo
    replies = iter([httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, text="{}")])
    monkeypatch.setattr(qloo.httpx, "get", lambda *a, **k: next(replies))
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    assert qloo._live("/search", {"query": "x"})["status"] == 200
    assert ledger.totals()["qloo"] == 2 and ledger.totals()["unresolved"] == 0


def test_unfinalised_reservation_stays_unresolved_exposure():
    ledger.reserve("bedrock", "Converse", exposure_usd=0.003)  # e.g. the process died mid-dispatch
    t = ledger.totals()
    assert t["bedrock_calls"] == 1 and t["unresolved"] == 1 and t["usd"] == pytest.approx(0.003)


def test_nova_cost_uses_verified_prices():
    assert ledger.nova_cost(1000, 1000) == pytest.approx(0.00033 + 0.00275)
    assert ledger.nova_cost(0, 0, grounding_calls=2) == pytest.approx(0.06)


def test_dns_falls_back_to_encrypted_dns_and_keeps_the_address_checks(monkeypatch):
    """SYNTHETIC: the system resolver fails (as an intercepting network does); DNS over HTTPS answers."""
    import socket

    from landfall import fetch

    def no_answer(*a, **k):
        raise socket.gaierror(8, "nodename nor servname provided, or not known")
    monkeypatch.setattr(fetch.socket, "getaddrinfo", no_answer)
    monkeypatch.setattr(fetch, "_doh", lambda host: ["45.223.17.132"])
    assert fetch._resolve("brand.example", 443) == ["45.223.17.132"]
    connected = []
    monkeypatch.setattr(fetch.httpcore.SyncBackend, "connect_tcp", lambda self, host, *a, **k: connected.append(host))
    fetch._GuardedBackend().connect_tcp("brand.example", 443)
    assert connected == ["45.223.17.132"]  # pinned to the vetted address
    monkeypatch.setattr(fetch, "_doh", lambda host: ["10.0.0.5"])
    with pytest.raises(fetch.FetchBlocked):  # a private answer from DoH is refused like any other
        fetch._GuardedBackend().connect_tcp("brand.example", 443)
    monkeypatch.setattr(fetch, "_doh", lambda host: [])
    with pytest.raises(socket.gaierror):  # no answer anywhere: the original failure stands (reported as DNS)
        fetch._resolve("brand.example", 443)
