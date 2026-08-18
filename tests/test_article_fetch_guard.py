"""
Offline self-check for the SSRF guard in assets.py and its 422 conversion
in server.py's POST /api/campaigns/{id}/assets/article route.

No live network request and no real DNS lookup: socket.getaddrinfo is
patched with a fake in-memory resolver, and httpx.Client is patched with a
fake context manager that records every call and returns a scripted
response. No model is loaded.

Run: python tests/test_article_fetch_guard.py
"""

import ipaddress
import itertools
import os
import pathlib
import socket
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

os.environ.setdefault("APP_PASSWORD", "test-password")

import server
import assets
import httpx

db.init()
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _new_campaign():
    sid = client.post("/api/sessions", json={"name": "S"}).json()["id"]
    return client.post(f"/api/sessions/{sid}/campaigns", json={"name": "C"}).json()["id"]


def _asset_count(campaign_id):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) c FROM assets WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def _asset_text(campaign_id):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT text FROM assets WHERE campaign_id = ? ORDER BY rowid DESC LIMIT 1",
            (campaign_id,)
        ).fetchone()
        return row["text"] if row else None
    finally:
        conn.close()


def _fake_getaddrinfo(host_map):
    """A resolver that answers a literal IP with itself, a mapped hostname
    from host_map with its configured addresses, and raises OSError
    (== "hostname does not resolve") for anything else. Never touches the
    real network."""
    def resolver(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            answers = [host]
        except ValueError:
            if host not in host_map:
                raise OSError(f"no mock DNS entry for {host!r}")
            answers = host_map[host]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, port))
            for a in answers
        ]
    return resolver


class FakeResponse:
    def __init__(self, status_code=200, is_redirect=False, location=None, text=""):
        self.status_code = status_code
        self.is_redirect = is_redirect
        self.headers = {}
        if location is not None:
            self.headers["location"] = location
        self.text = text

    def raise_for_status(self):
        pass


def _make_fake_client(items):
    """items: list of FakeResponse or Exception instances, consumed in
    call order. Returns (FakeClientClass, recorded) where recorded
    accumulates one dict per client.get() call."""
    recorded = []
    queue = list(items)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout=None, headers=None, extensions=None):
            recorded.append({
                "url": url, "timeout": timeout,
                "headers": headers, "extensions": extensions,
            })
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    return FakeClient, recorded


_REJECTION_MESSAGE = "That link points to a private address and cannot be fetched."
_REJECTION_BODY = {"error": "VALIDATION_ERROR", "message": _REJECTION_MESSAGE, "field": "url"}


# ---------------------------------------------------------------------------
# 1. Rejection through the HTTP route - private/malformed targets, no HTTP
#    call ever reached since _validate_url raises before client.get().
# ---------------------------------------------------------------------------

_PRIVATE_CASES = [
    ("http://127.0.0.1/x", {}),
    ("http://localhost/x", {"localhost": ["127.0.0.1"]}),
    ("http://10.0.0.1/x", {}),
    ("http://172.16.0.1/x", {}),
    ("http://192.168.1.1/x", {}),
    ("http://169.254.169.254/latest/meta-data/", {}),
    ("http://0.0.0.0/x", {}),
    ("http://[::1]/x", {}),
    ("http://[fd00::1]/x", {}),
    ("http://[::ffff:127.0.0.1]/x", {}),
    ("http://user:pw@example.com/x", {"example.com": ["93.184.216.34"]}),
    ("http://example.com:11434/", {"example.com": ["93.184.216.34"]}),
    ("http://mixed.example.test/x", {"mixed.example.test": ["93.184.216.34", "127.0.0.1"]}),
]


def test_route_rejects_private_and_malformed_targets():
    campaign_id = _new_campaign()
    for url, host_map in _PRIVATE_CASES:
        resolver = _fake_getaddrinfo(host_map)
        FakeClient, recorded = _make_fake_client([])
        before = _asset_count(campaign_id)
        with patch("socket.getaddrinfo", side_effect=resolver), \
             patch("httpx.Client", FakeClient):
            resp = client.post(f"/api/campaigns/{campaign_id}/assets/article", json={"url": url})
        assert resp.status_code == 422, f"{url}: {resp.text}"
        assert resp.json() == _REJECTION_BODY, f"{url}: {resp.json()}"
        assert _asset_count(campaign_id) == before, f"{url}: created an asset row"
        assert recorded == [], f"{url}: reached an HTTP request that should never fire"
    print("  ok  route rejects every private/malformed target with the 422 body and creates no asset")


def test_route_rejects_redirect_to_private_target():
    campaign_id = _new_campaign()
    host_map = {"redirect-to-private.example": ["93.184.216.34"]}
    FakeClient, recorded = _make_fake_client([
        FakeResponse(status_code=302, is_redirect=True, location="http://127.0.0.1/"),
    ])
    before = _asset_count(campaign_id)
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient):
        resp = client.post(
            f"/api/campaigns/{campaign_id}/assets/article",
            json={"url": "http://redirect-to-private.example/x"},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json() == _REJECTION_BODY, resp.json()
    assert _asset_count(campaign_id) == before, "redirect-to-private created an asset row"
    assert len(recorded) == 1, "expected exactly the first hop to be requested"
    print("  ok  a public host that redirects to a private address is rejected and creates no asset")


def test_route_rejects_non_http_scheme_before_fetching():
    campaign_id = _new_campaign()
    before = _asset_count(campaign_id)
    resp = client.post(
        f"/api/campaigns/{campaign_id}/assets/article",
        json={"url": "file:///etc/passwd"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json() == {
        "error": "VALIDATION_ERROR",
        "message": "Articles need a full http:// or https:// link.",
        "field": "url",
    }, resp.json()
    assert _asset_count(campaign_id) == before, "file:// URL created an asset row"
    print("  ok  a file:// URL is rejected by the pre-filter with its own message and creates no asset")


# ---------------------------------------------------------------------------
# 2. Direct assets._validate_url assertions.
# ---------------------------------------------------------------------------

def test_validate_url_rejects_scheme_fragment_and_missing_host():
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo({})):
        try:
            assets._validate_url("file:///etc/passwd")
            assert False, "file:// scheme did not raise BlockedUrl"
        except assets.BlockedUrl:
            pass

        try:
            assets._validate_url("http://example.com/x#frag")
            assert False, "a fragment did not raise BlockedUrl"
        except assets.BlockedUrl:
            pass

        try:
            assets._validate_url("http:///x")
            assert False, "a missing hostname did not raise BlockedUrl"
        except assets.BlockedUrl:
            pass
    print("  ok  _validate_url raises BlockedUrl for a non-http(s) scheme, a fragment, and a missing hostname")


# ---------------------------------------------------------------------------
# 3. Acceptance.
# ---------------------------------------------------------------------------

def test_public_200_creates_asset_and_extracts_title_and_text():
    host_map = {"site.example": ["93.184.216.34"]}
    html = "<html><head><title>Hello Title</title></head><body><p>Body text here</p></body></html>"

    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", _make_fake_client([FakeResponse(status_code=200, text=html)])[0]):
        result = assets.fetch_article("http://site.example/page")
    assert result["title"] == "Hello Title", result
    assert "Body text here" in result["text"], result
    assert isinstance(result["retrieved_at"], str) and result["retrieved_at"], result

    campaign_id = _new_campaign()
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", _make_fake_client([FakeResponse(status_code=200, text=html)])[0]):
        resp = client.post(
            f"/api/campaigns/{campaign_id}/assets/article",
            json={"url": "http://site.example/page"},
        )
    assert resp.status_code == 201, resp.text
    assert _asset_count(campaign_id) == 1
    print("  ok  a public 200 response creates an asset and extracts title/text from the fake HTML")


def test_relative_redirect_between_public_hosts_is_followed():
    host_map = {"hop.example": ["93.184.216.34"]}
    FakeClient, recorded = _make_fake_client([
        FakeResponse(status_code=302, is_redirect=True, location="/next"),
        FakeResponse(status_code=200, text="<html><title>T2</title><body>Final Text</body></html>"),
    ])
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient):
        result = assets.fetch_article("http://hop.example/start")
    assert len(recorded) == 2, recorded
    assert "Final Text" in result["text"], result
    print("  ok  a relative Location redirect between two public URLs is followed and fetched")


def test_six_redirects_fail_safely():
    host_map = {"loop.example": ["93.184.216.34"]}
    items = [
        FakeResponse(status_code=302, is_redirect=True, location="/next")
        for _ in range(assets._MAX_REDIRECTS + 1)
    ]
    FakeClient, recorded = _make_fake_client(items)
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient):
        try:
            assets.fetch_article("http://loop.example/start")
            assert False, "an endless redirect chain did not raise BlockedUrl"
        except assets.BlockedUrl:
            pass
    assert len(recorded) == assets._MAX_REDIRECTS + 1, recorded
    print("  ok  a redirect chain longer than _MAX_REDIRECTS raises BlockedUrl after exactly _MAX_REDIRECTS + 1 requests")


def test_timeout_preserves_empty_text_behavior():
    host_map = {"slow.example": ["93.184.216.34"]}
    FakeClient, recorded = _make_fake_client([httpx.TimeoutException("slow")])
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient):
        result = assets.fetch_article("http://slow.example/x")
    assert result["title"] == "" and result["text"] == "", result
    assert isinstance(result["retrieved_at"], str) and result["retrieved_at"], result
    assert len(recorded) == 1, recorded

    campaign_id = _new_campaign()
    FakeClient2, _ = _make_fake_client([httpx.TimeoutException("slow")])
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient2):
        resp = client.post(
            f"/api/campaigns/{campaign_id}/assets/article",
            json={"url": "http://slow.example/x"},
        )
    assert resp.status_code == 201, resp.text
    assert not _asset_text(campaign_id), "timed-out fetch stored non-empty text"
    print("  ok  a timeout during fetch does not raise, returns empty text, and the route still saves a 201 asset")


# ---------------------------------------------------------------------------
# 4. Anti-rebinding assertion.
# ---------------------------------------------------------------------------

def test_request_is_pinned_to_resolved_address_not_hostname():
    host_map = {"pin.example": ["93.184.216.34"]}
    FakeClient, recorded = _make_fake_client([
        FakeResponse(status_code=200, text="<html><title>X</title></html>"),
    ])
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
         patch("httpx.Client", FakeClient):
        assets.fetch_article("http://pin.example/page")
    assert len(recorded) == 1, recorded
    call = recorded[0]
    assert "93.184.216.34" in call["url"], call
    assert "pin.example" not in call["url"], call
    assert call["headers"]["Host"] == "pin.example", call
    assert call["extensions"]["sni_hostname"] == "pin.example", call
    print("  ok  the request is sent to the resolved address, not the hostname, with Host/SNI still the original hostname")


# ---------------------------------------------------------------------------
# 5. Shared-budget assertion.
# ---------------------------------------------------------------------------

def test_redirect_hops_share_one_shrinking_budget():
    host_map = {"budget.example": ["93.184.216.34"]}
    FakeClient, recorded = _make_fake_client([
        FakeResponse(status_code=302, is_redirect=True, location="/next"),
        FakeResponse(status_code=200, text="<html><title>X</title></html>"),
    ])
    clock = itertools.count(1_000.0, 0.5)  # advances 0.5s on every time.monotonic() call
    original_budget = assets._FETCH_BUDGET_SECONDS
    assets._FETCH_BUDGET_SECONDS = 2.0
    try:
        with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(host_map)), \
             patch("httpx.Client", FakeClient), \
             patch("time.monotonic", side_effect=lambda: next(clock)):
            assets.fetch_article("http://budget.example/start")
    finally:
        assets._FETCH_BUDGET_SECONDS = original_budget

    assert len(recorded) == 2, recorded
    assert recorded[1]["timeout"] < recorded[0]["timeout"], recorded
    print("  ok  a redirect chain shares one shrinking deadline: the second hop's timeout is strictly smaller than the first's")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_route_rejects_private_and_malformed_targets,
        test_route_rejects_redirect_to_private_target,
        test_route_rejects_non_http_scheme_before_fetching,
        test_validate_url_rejects_scheme_fragment_and_missing_host,
        test_public_200_creates_asset_and_extracts_title_and_text,
        test_relative_redirect_between_public_hosts_is_followed,
        test_six_redirects_fail_safely,
        test_timeout_preserves_empty_text_behavior,
        test_request_is_pinned_to_resolved_address_not_hostname,
        test_redirect_hops_share_one_shrinking_budget,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
