"""
Task 3.6 end-to-end product flow check.

Starts the real server in-process (uvicorn on a real socket) against a
temporary DB and storage root, and drives the real app/ frontend with
Playwright Chromium against that server's own origin. Only the
YouTube, LLM/Ollama, classifier, and article-fetch boundaries are
replaced with process-global fakes; everything else (routes, DB,
storage, frontend JS) runs for real.

This file covers Session creation through run start, the post-brief_pause path
(proceed, completion, downloads, report JSON, ARIA sweep), and the skip-pause
control.

Run: python tests/e2e_product_flow.py
"""

import json
import os
import pathlib
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
from unittest.mock import patch

import pandas as pd
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("YOUTUBE_API_KEY", "e2e-test-key")
# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Set before import so the startup hook sees it.
os.environ.setdefault("APP_PASSWORD", "test-password")

# db._DB_PATH and storage._ROOT are module globals dereferenced per
# call (not read once at import time), so reassigning them here before
# importing server/adapter redirects every DB and file-system touch
# those modules make for the rest of this run. uvicorn's ASGI lifespan
# fires server.app's real startup hook (which calls db.init() itself),
# so db.init() must not be called explicitly here.
import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

import server
import adapter

from playwright.sync_api import sync_playwright

# Mutable control switch read on every draft_from_inputs call, so
# behavior can flip mid-run without stopping/restarting patches.
FAKES = {"draft_fails": False, "reconcile_all_excluded": False}

_FIXED_PROPOSALS = [
    ("Lower price", "Cheaper than before."),
    ("Better durability", "Lasts longer than the old model."),
]

# Playwright event capture, module-level so every case can inspect it.
_CONSOLE_ERRORS = []
_PAGE_ERRORS = []
_DIALOG_MESSAGES = []

# Cross-case state (ids captured as the flow progresses).
_STATE = {}

# All /api/* requests and responses the page makes, module-level so any
# case or failure handler can inspect what actually went over the wire.
_REQUESTS = []


def _register_network_capture(page):
    def on_request(request):
        if "/api/" not in request.url:
            return
        # post_data decodes the body as UTF-8 and raises on a binary one.
        # The upload route posts raw PNG bytes, and an exception raised
        # inside a Playwright event listener poisons the page connection.
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        _REQUESTS.append({"kind": "request", "method": request.method,
                           "url": request.url, "t": time.monotonic(),
                           "post_data": post_data})

    def on_response(response):
        if "/api/" not in response.url:
            return
        _REQUESTS.append({"kind": "response", "method": response.request.method,
                           "url": response.url, "status": response.status,
                           "t": time.monotonic()})

    page.on("request", on_request)
    page.on("response", on_response)


def _api_log(substring=None):
    lines = []
    for entry in _REQUESTS:
        if substring is not None and substring not in entry["url"]:
            continue
        if entry["kind"] == "request":
            lines.append(f"[{entry['t']:.3f}] -> {entry['method']} {entry['url']}")
        else:
            lines.append(f"[{entry['t']:.3f}] <- {entry['method']} {entry['url']} "
                          f"{entry['status']}")
    return "\n".join(lines) if lines else "(no matching /api/ traffic captured)"


def _fake_comments_df():
    rows = [
        {"group": "C", "kind": "auto", "video_id": "e2eAAAAAAA1",
         "comment": "Great value for the price", "likes": 5,
         "published_at": "2026-08-01T00:00:00+00:00", "is_reply": False,
         "reply_count": 0, "in_base": True, "theme": "Other",
         "sentiment": "positive", "sentiment_confidence": 0.9,
         "emotion": "joy", "emotion_confidence": 0.8},
        {"group": "C", "kind": "auto", "video_id": "e2eAAAAAAA1",
         "comment": "Feels sturdy and well built", "likes": 2,
         "published_at": "2026-08-02T00:00:00+00:00", "is_reply": False,
         "reply_count": 1, "in_base": True, "theme": "Other",
         "sentiment": "positive", "sentiment_confidence": 0.85,
         "emotion": "joy", "emotion_confidence": 0.7},
        {"group": "C", "kind": "auto", "video_id": "e2eAAAAAAA1",
         "comment": "Not sure this is worth it", "likes": 0,
         "published_at": "2026-08-03T00:00:00+00:00", "is_reply": True,
         "reply_count": 0, "in_base": True, "theme": "Other",
         "sentiment": "negative", "sentiment_confidence": 0.6,
         "emotion": "neutral", "emotion_confidence": 0.5},
    ]
    return pd.DataFrame(rows)


def _fake_meta_df():
    return pd.DataFrame([
        {"video_id": "e2eAAAAAAA1", "group": "C", "kind": "auto",
         "title": "t", "channel": "ch", "description": "d",
         "views": 100, "transcript": "We cut the price and built it tougher.",
         "has_transcript": True},
    ])


def _fake_affect_result():
    empty_table = pd.DataFrame()
    return {
        "emotion": {"table": empty_table, "low_confidence_pct": 0.0, "caveat": ""},
        "sentiment": {"table": empty_table, "low_confidence_pct": 0.0, "caveat": ""},
    }


def _fake_render(markdown, out_dir, cfg, debug_dir=None, _df=None, _transfer=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 e2e fake\n")


def _fake_export(df, themes, transfer, affect_result, meta_df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    headers = {
        "comments.csv": "comment\n",
        "key-messages.csv": "label\n",
        "themes.csv": "theme\n",
        "sentiment.csv": "sentiment\n",
        "emotions.csv": "emotion\n",
    }
    for name, header in headers.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(header)


def _fake_draft_from_inputs(text, images, cfg):
    if FAKES["draft_fails"]:
        raise RuntimeError("Ollama unreachable")
    return [
        {"label": lbl, "description": desc, "included": True,
         "order": i, "edited": False}
        for i, (lbl, desc) in enumerate(_FIXED_PROPOSALS)
    ]


def _fake_reconcile(existing, meta_df, cfg, context_map=None, images_map=None,
                    id_factory=uuid.uuid4, include_grounded=False):
    if existing:
        reconciled = existing
    else:
        reconciled = [
            {"id": str(id_factory()), "label": lbl, "description": desc,
             "included": True, "order": i, "edited": False}
            for i, (lbl, desc) in enumerate(_FIXED_PROPOSALS)
        ]
    if FAKES["reconcile_all_excluded"]:
        reconciled = [dict(row, included=False) for row in reconciled]
    return ("# grounded", reconciled)


def _patches():
    return [
        patch.object(server.assets, "extract_upload", return_value=""),
        patch.object(server.assets, "fetch_article", return_value={
            "title": "Fixture article", "text": "Fixture article body.",
            "retrieved_at": "2026-08-14T00:00:00+00:00",
        }),
        patch.object(server.pipeline_brief, "draft_from_inputs",
                    side_effect=_fake_draft_from_inputs),
        patch.object(adapter.pipeline_llm, "preflight", return_value=None),
        patch.object(adapter.collect, "fetch",
                    return_value=(_fake_comments_df(), _fake_meta_df())),
        patch.object(adapter.collect, "clean", side_effect=lambda df, cfg: df),
        patch.object(adapter.brief, "reconcile", side_effect=_fake_reconcile),
        patch.object(adapter.analyze, "build", return_value=[]),
        patch.object(adapter.analyze, "classify",
                    side_effect=lambda df, themes, points, cfg=None, on_progress=None: (df, {})),
        patch.object(adapter.analyze, "extend",
                    side_effect=lambda df, themes, points, summary, cfg, on_progress=None: (df, themes, 0.0)),
        patch.object(adapter.analyze, "affect",
                    side_effect=lambda df, cfg: (df, _fake_affect_result())),
        patch.object(adapter.pipeline_llm, "unload", return_value=None),
        patch.object(adapter.pipeline_report, "write", return_value="# report"),
        patch.object(adapter.pipeline_report, "render", side_effect=_fake_render),
        patch.object(adapter.pipeline_report, "export", side_effect=_fake_export),
    ]


def _start_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                         log_level="warning", access_log=False)
    srv = uvicorn.Server(cfg)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    deadline = time.time() + 30.0
    while time.time() < deadline:
        if srv.started:
            return srv, thread, port
        time.sleep(0.05)
    raise RuntimeError("server did not start")


def _stop_server(srv, thread):
    srv.should_exit = True
    thread.join(timeout=15.0)


def _expect(cond, message):
    if not cond:
        raise AssertionError(message)


def _require_run_id():
    run_id = _STATE.get("run_id")
    if not run_id:
        raise AssertionError(
            "no run id was captured; an earlier case failed before starting "
            "a run, so this case cannot run")
    return run_id


def _wait_text(page, selector, expected, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000.0
    last = None
    while time.time() < deadline:
        last = page.text_content(selector)
        if last == expected:
            return
        time.sleep(0.05)
    _expect(False, f"{selector} text never became {expected!r}, last was {last!r}")


def _wait_count(page, selector, expected, timeout_ms=5000):
    deadline = time.time() + timeout_ms / 1000.0
    last = None
    while time.time() < deadline:
        last = page.locator(selector).count()
        if last == expected:
            return
        time.sleep(0.05)
    _expect(False, f"{selector} count never became {expected}, last was {last}")


def _last_start_body(session_id):
    """Return the parsed JSON body of the most recent POST to this Session's
    runs route, or None if no such request was captured."""
    for entry in reversed(_REQUESTS):
        if (entry["kind"] == "request" and entry["method"] == "POST"
                and entry["url"].endswith(f"/api/sessions/{session_id}/runs")):
            raw = entry.get("post_data")
            return json.loads(raw) if raw else None
    return None


def _goto_campaign_at(page, base):
    page.goto(base + "/#/sessions/" + _STATE["session_id"] +
              "/campaigns/" + _STATE["campaign_id"])
    page.wait_for_selector("#btn-run")


def _dismiss_confirm_modals(page, limit=2):
    """Click through any confirmation modals the start path raises. Two can
    stack: the stale-Key-Message confirm and the overwrite confirm."""
    for _ in range(limit):
        cont = page.locator("#overlay-root [data-confirm-continue]")
        deadline = time.time() + 1.0
        found = False
        while time.time() < deadline:
            if cont.count() > 0:
                found = True
                break
            time.sleep(0.05)
        if not found:
            return
        cont.first.click()
        page.wait_for_timeout(100)


def _start_run_from_campaign(page, base, checked):
    """Set the skip-pause checkbox, press start, clear any confirm modal, and
    return the new run id read from the URL hash."""
    _goto_campaign_at(page, base)
    box = page.locator("#chk-skip-pause")
    _expect(box.count() == 1,
            "#chk-skip-pause was not rendered on the campaign screen, so the "
            "skip-pause path cannot be exercised")
    box.set_checked(checked)
    _expect(box.is_checked() is checked,
            f"#chk-skip-pause checked state was {box.is_checked()}, expected {checked}")
    page.click("#btn-run")
    _dismiss_confirm_modals(page)
    deadline = time.time() + 15.0
    last = None
    while time.time() < deadline:
        last = page.evaluate("location.hash")
        match = re.match(r"^#/runs/([^/]+)$", last)
        if match:
            return match.group(1)
        time.sleep(0.05)
    _expect(False, f"hash never reached #/runs/<id> after start, last was {last!r}")


def _poll_run_snapshot_for(page, base, run_id, predicate, deadline_s, label):
    """Same contract as _poll_run_snapshot, but for an explicit run id rather
    than _STATE['run_id']."""
    deadline = time.time() + deadline_s
    snap = None
    while time.time() < deadline:
        snap = page.request.get(base + "/api/runs/" + run_id).json()
        if predicate(snap):
            return snap
        time.sleep(0.2)
    _expect(False, f"{label}; last snapshot: {snap!r}")
    return snap


def _drain_paused_run(page, base, run_id):
    """Take a run that is sitting at brief_pause through to a terminal status,
    so the global one-run guard does not block the next case."""
    snap = page.request.get(base + "/api/runs/" + run_id).json()
    messages = []
    for i, bp in enumerate(snap.get("briefPoints", [])):
        messages.append({"id": bp["id"], "label": bp["label"],
                         "description": bp["description"],
                         "included": i == 0, "order": i})
    _expect(messages, f"run {run_id} paused with no briefPoints to include")
    resp = page.request.patch(base + "/api/runs/" + run_id + "/brief_points",
                              data=json.dumps({"messages": messages}),
                              headers={"content-type": "application/json"})
    _expect(resp.status == 200,
            f"PATCH /brief_points returned {resp.status}: {resp.text()}")
    resp = page.request.post(base + "/api/runs/" + run_id + "/proceed")
    _expect(resp.status == 200,
            f"POST /proceed returned {resp.status}: {resp.text()}")
    _poll_run_snapshot_for(page, base, run_id,
                           lambda s: s.get("status") in ("complete", "failed"),
                           120.0, f"run {run_id} never reached a terminal status")


# --- cases -------------------------------------------------------------

def live_mode_engaged(page, base):
    page.goto(base + "/#/home")
    page.wait_for_selector("#url-list, a.btn.primary[href=\"#/sessions/new\"]")

    deadline = time.time() + 10.0
    mode = None
    while time.time() < deadline:
        mode = page.evaluate("window.demoApi && window.demoApi.mode")
        if mode == "live":
            break
        time.sleep(0.05)
    _expect(mode == "live",
            "frontend never reached live mode; every backend assertion would be "
            f"meaningless against the in-memory demo store (actual value: {mode!r})\n"
            f"{_api_log('/api/sessions')}")

    sessions_ok = any(
        entry["kind"] == "response" and "/api/sessions" in entry["url"]
        and entry["status"] == 200
        for entry in _REQUESTS
    )
    _expect(sessions_ok,
            "no 200 response captured for /api/sessions\n" + _api_log())


def session_creation(page, base):
    page.goto(base + "/#/home")
    if page.locator('a.btn.primary[href="#/sessions/new"]').count() > 0:
        page.click('a.btn.primary[href="#/sessions/new"]')
    else:
        page.goto(base + "/#/sessions/new")
    page.fill("input#f-session-name", "E2E Session")
    page.fill("input#f-url", "https://www.youtube.com/watch?v=e2eAAAAAAA1")
    page.click("#btn-add-url")
    _wait_count(page, "#url-list .url-row", 1)
    page.click('#setup-form button[type=submit]')

    deadline = time.time() + 5.0
    match = None
    last = None
    while time.time() < deadline:
        last = page.evaluate("location.hash")
        match = re.match(r"^#/sessions/([^/]+)/campaigns/([^/]+)$", last)
        if match:
            break
        time.sleep(0.05)
    _expect(match is not None, f"hash never reached #/sessions/<id>/campaigns/<id>, "
            f"last was {last!r}")
    _STATE["session_id"], _STATE["campaign_id"] = match.group(1), match.group(2)


def upload_asset_then_draft(page, base):
    page.set_input_files("#file-input", {
        "name": "visual.png",
        "mimeType": "image/png",
        "buffer": b"\x89PNG\r\n\x1a\nfake",
    })
    _wait_count(page, ".asset-row", 1)
    _wait_count(page, "#km-container .km-row", 2)
    _expect(page.input_value("#km-label-0") == _FIXED_PROPOSALS[0][0],
            f"km-label-0 was {page.input_value('#km-label-0')!r}, "
            f"expected {_FIXED_PROPOSALS[0][0]!r}")


def article_asset(page, base):
    before = page.locator(".asset-row").count()
    page.fill("input#c-article", "https://example.com/e2e-article")
    page.click("#c-add-article")
    _wait_count(page, ".asset-row", before + 1)
    _expect(page.text_content("#article-err") in (None, ""),
            f"#article-err was {page.text_content('#article-err')!r}")


def setup_edit_add_delete_order(page, base):
    page.fill("#km-label-0", "Edited label")
    _expect(page.is_enabled("#km-save"), "#km-save did not become enabled after edit")

    mode = page.evaluate("window.demoApi.mode")
    _expect(mode == "live", "save would not issue HTTP in demo mode")

    rows = page.evaluate(
        "() => Array.from(document.querySelectorAll('.km-row')).map(row => ({"
        "label: (row.querySelector('input.km-label-in') || {}).value,"
        "description: (row.querySelector('textarea.km-desc-in') || {}).value"
        "}))"
    )
    _expect(
        all(r["label"].strip() != "" for r in rows)
        and all(len(r["label"]) <= 120 for r in rows)
        and all(len(r["description"]) <= 500 for r in rows),
        "a row fails client-side validation, so the save handler will return "
        f"before issuing a PATCH; rows: {rows!r}"
    )

    with page.expect_response(
            lambda r: "/key_messages" in r.url and r.request.method == "PATCH",
            timeout=10000) as resp_info:
        page.click("#km-save")
    resp = resp_info.value
    _expect(resp.status == 200,
            f"PATCH /key_messages returned {resp.status}, body: {resp.text()}")

    deadline = time.time() + 5.0
    saved = False
    while time.time() < deadline:
        if page.get_attribute("#km-save", "disabled") is not None:
            saved = True
            break
        time.sleep(0.05)
    _expect(saved, "#km-save did not become disabled after save")

    page.reload()
    page.wait_for_selector("#km-container")
    _expect(page.input_value("#km-label-0") == "Edited label",
            f"edited label did not survive reload, got "
            f"{page.input_value('#km-label-0')!r}")

    before_count = page.locator("#km-container .km-row").count()
    page.click("#km-add")
    _wait_count(page, "#km-container .km-row", before_count + 1)

    page.click('[data-km-del="2"]')
    _wait_count(page, "#km-container .km-row", before_count)

    pressed_before = page.get_attribute('[data-km-inc="0"]', "aria-pressed")
    page.click('[data-km-inc="0"]')
    deadline = time.time() + 5.0
    flipped = False
    while time.time() < deadline:
        if page.get_attribute('[data-km-inc="0"]', "aria-pressed") != pressed_before:
            flipped = True
            break
        time.sleep(0.05)
    _expect(flipped, "aria-pressed on data-km-inc=0 did not flip")

    label0_before = page.input_value("#km-label-0")
    label1_before = page.input_value("#km-label-1")
    page.click('[data-km-down="0"]')
    deadline = time.time() + 5.0
    swapped = False
    while time.time() < deadline:
        if (page.input_value("#km-label-0") == label1_before and
                page.input_value("#km-label-1") == label0_before):
            swapped = True
            break
        time.sleep(0.05)
    _expect(swapped, "first two Key Message labels did not swap after data-km-down=0")

    _expect(page.get_attribute('[data-km-up="0"]', "disabled") is not None,
            "data-km-up=0 was not disabled at the top of the list")


def draft_failure_is_stale_then_retry(page, base):
    FAKES["draft_fails"] = True
    before = page.locator(".asset-row").count()
    page.fill("input#c-article", "https://example.com/e2e-article-2")
    page.click("#c-add-article")
    _wait_count(page, ".asset-row", before + 1)

    _wait_text(page, ".km-status-msg", "Key Messages may be out of date.")
    _expect(page.locator("#km-retry").count() > 0, "#km-retry did not appear on stale state")

    FAKES["draft_fails"] = False
    page.click("#km-retry")
    _wait_count(page, ".km-status .km-status-msg", 0)
    # Three rows is correct, not two. setup_edit_add_delete_order renamed a row
    # earlier in this flow; PATCH /key_messages marks every submitted row
    # edited=1, and the draft merge preserves an edited row instead of letting a
    # later draft reclaim it. So a retry yields the two drafted rows plus the
    # preserved edited row. Reducing this to two would delete a user's edited
    # Key Message.
    _wait_count(page, "#km-container .km-row", 3)

    # Guard the reason the count was wrong before: a blank never-saved row used
    # to survive the merge and inflate the count. Every row must carry a label.
    row_count = page.locator("#km-container .km-row").count()
    for idx in range(row_count):
        value = page.locator(f"#km-label-{idx}").input_value().strip()
        _expect(value != "",
                f"Key Message row {idx} rendered with an empty label after "
                "retry, so a blank unclaimable row survived the draft merge")


def run_start(page, base):
    _expect(page.locator(".km-status .km-status-msg").count() == 0,
            "run_start requires a clean Key Message state; a stale or failed "
            "state changes the confirmation path")
    _expect(page.get_attribute("#km-save", "disabled") is not None,
            "run_start requires a saved editor; a dirty edit trips the "
            "unsaved-changes guard and blocks navigation")
    page.click("#btn-run")
    _expect(page.locator("#overlay-root .modal-backdrop").count() == 0,
            "confirmation modal appeared on a first run with no prior result")

    deadline = time.time() + 10.0
    run_id = None
    last = None
    while time.time() < deadline:
        last = page.evaluate("location.hash")
        match = re.match(r"^#/runs/([^/]+)$", last)
        if match:
            run_id = match.group(1)
            break
        time.sleep(0.05)
    _expect(run_id is not None, f"hash never reached #/runs/<id>, last was {last!r}")
    _STATE["run_id"] = run_id
    _expect(_DIALOG_MESSAGES == [], f"unexpected native dialog(s): {_DIALOG_MESSAGES}")


def _poll_run_snapshot(base, page, predicate, deadline_s, label):
    deadline = time.time() + deadline_s
    snap = None
    while time.time() < deadline:
        resp = page.request.get(base + "/api/runs/" + _STATE["run_id"])
        snap = resp.json()
        if predicate(snap):
            return snap
        time.sleep(0.2)
    _expect(False, f"{label}; last snapshot: {snap!r}")
    return snap


def brief_pause_reopen_persisted(page, base):
    run_id = _require_run_id()
    snap = _poll_run_snapshot(
        base, page, lambda s: s.get("stage") == "brief_pause", 60.0,
        "run never reached stage brief_pause")
    _expect(snap.get("stage") == "brief_pause",
            f"run never reached stage brief_pause, last stage was "
            f"{snap.get('stage')!r}, status {snap.get('status')!r}")

    page.goto(base + "/#/runs/" + run_id)
    page.wait_for_selector("#brief-review:visible")
    _wait_text(page, "#brief-h", "Key Messages we'll test for transfer")

    row_count = page.locator(".brief-item").count()
    _expect(row_count > 0, "no .brief-item rows rendered on brief_pause reopen")
    # The app renders EVERY point and marks excluded ones with the excluded
    # class, so the row count matches the total, not the included subset.
    total_count = len(snap.get("briefPoints", []))
    _expect(row_count == total_count,
            f".brief-item count {row_count} did not match total Key Message "
            f"count {total_count} from run snapshot briefPoints")

    excluded_expected = sum(
        1 for bp in snap.get("briefPoints", []) if not bp.get("included"))
    excluded_rendered = page.locator(".brief-item.excluded").count()
    _expect(excluded_rendered == excluded_expected,
            f".brief-item.excluded count {excluded_rendered} did not match the "
            f"{excluded_expected} excluded Key Messages in the run snapshot")


def brief_pause_all_excluded_rejected(page, base):
    _require_run_id()
    # paint() replaces the whole subtree on every change event. uncheck()
    # verifies element state after its click, so acting on a ":checked"
    # locator times out: the click lands, the repaint detaches the element,
    # and the retry re-resolves a selector that matches nothing once the last
    # box is cleared. Act on stable data-inc locators and read state back.
    total = page.locator("input[type=checkbox][data-inc]").count()
    _expect(total > 0,
            "no Key Message checkboxes were rendered, so the all-excluded "
            "rejection cannot be exercised")
    for idx in range(total):
        box = page.locator(f'input[type=checkbox][data-inc="{idx}"]')
        for _ in range(10):
            if not box.is_checked():
                break
            box.click()
            page.wait_for_timeout(50)
        _expect(not box.is_checked(),
                f"Key Message checkbox data-inc={idx} stayed checked after "
                "repeated clicks")

    requests_before = len(_REQUESTS)
    page.click("#bp-confirm", timeout=10000)
    _wait_text(page, "#bp-err", "Include at least one Key Message before continuing.")

    patch_seen = any(
        entry["kind"] == "request" and entry["method"] == "PATCH"
        and "/brief_points" in entry["url"]
        for entry in _REQUESTS[requests_before:]
    )
    _expect(not patch_seen,
            "a PATCH to /brief_points was captured even though every Key "
            f"Message was excluded, proving the gate is not client side\n"
            f"{_api_log('/brief_points')}")

    page.check('input[type=checkbox][data-inc="0"]')


def brief_pause_edit_and_proceed(page, base):
    _require_run_id()
    page.fill("#bp-label-0", "Run-edited message", timeout=10000)

    before_count = page.locator(".brief-item").count()
    page.click("#bp-add")
    _wait_count(page, ".brief-item", before_count + 1)
    new_idx = before_count
    page.fill(f"#bp-label-{new_idx}", "Added at pause")

    page.click(f'button[data-del="{new_idx}"]')
    _wait_count(page, ".brief-item", before_count)

    with page.expect_response(
            lambda r: "/brief_points" in r.url and r.request.method == "PATCH",
            timeout=15000) as patch_info:
        with page.expect_response(
                lambda r: "/proceed" in r.url and r.request.method == "POST",
                timeout=15000) as proceed_info:
            page.click("#bp-confirm")

    patch_resp = patch_info.value
    _expect(patch_resp.status == 200,
            f"PATCH /brief_points returned {patch_resp.status}, "
            f"body: {patch_resp.text()}")

    proceed_resp = proceed_info.value
    _expect(proceed_resp.status == 200,
            f"POST /proceed returned {proceed_resp.status}, "
            f"body: {proceed_resp.text()}")

    deadline = time.time() + 5.0
    hidden_or_empty = False
    while time.time() < deadline:
        if page.locator("#brief-review:visible").count() == 0:
            hidden_or_empty = True
            break
        time.sleep(0.05)
    _expect(hidden_or_empty, "#brief-review did not become hidden or empty after proceed")


def run_completes(page, base):
    run_id = _require_run_id()
    snap = _poll_run_snapshot(
        base, page, lambda s: s.get("status") in ("complete", "failed"), 120.0,
        "run never reached status complete or failed")
    if snap.get("status") == "failed":
        _expect(False,
                f"run finished with status failed at stage {snap.get('stage')!r}, "
                f"error: {snap.get('error')!r}")
    _expect(snap.get("status") == "complete",
            f"run finished with status {snap.get('status')!r}, expected complete")

    page.wait_for_selector("a#btn-results")
    href = page.get_attribute("a#btn-results", "href")
    expected_suffix = f"#/runs/{run_id}/results"
    _expect(href is not None and href.endswith(expected_suffix),
            f"a#btn-results href was {href!r}, expected to end with "
            f"{expected_suffix!r}")


def six_downloads_in_order(page, base):
    run_id = _require_run_id()
    page.goto(base + "/#/runs/" + run_id + "/results")
    page.wait_for_selector(".dl-row")

    buttons = page.locator(".dl-row button[data-artifact]")
    _expect(buttons.count() == 6,
            f"expected 6 download controls inside .dl-row, found {buttons.count()}")

    expected_kinds = ["report_pdf", "comments_csv", "key_messages_csv",
                       "themes_csv", "sentiment_csv", "emotions_csv"]
    expected_labels = ["report.pdf \u2193", "comments.csv \u2193",
                        "key-messages.csv \u2193", "themes.csv \u2193",
                        "sentiment.csv \u2193", "emotions.csv \u2193"]
    expected_filenames = {
        "report_pdf": "report.pdf",
        "comments_csv": "comments.csv",
        "key_messages_csv": "key-messages.csv",
        "themes_csv": "themes.csv",
        "sentiment_csv": "sentiment.csv",
        "emotions_csv": "emotions.csv",
    }

    actual_kinds = [buttons.nth(i).get_attribute("data-artifact") for i in range(6)]
    _expect(actual_kinds == expected_kinds,
            f"data-artifact order was {actual_kinds!r}, expected {expected_kinds!r}")

    actual_labels = [buttons.nth(i).text_content().strip() for i in range(6)]
    _expect(actual_labels == expected_labels,
            f"download control labels were {actual_labels!r}, "
            f"expected {expected_labels!r}")

    for i in range(6):
        kind = expected_kinds[i]
        btn = buttons.nth(i)
        _expect(btn.get_attribute("disabled") is None,
                f"download control for {kind!r} was disabled, expected enabled "
                "since the fakes write all six files")
        dis_wrap_ancestor = page.evaluate(
            "(el) => !!el.closest('span.dis-wrap')", btn.element_handle())
        _expect(not dis_wrap_ancestor,
                f"download control for {kind!r} had a span.dis-wrap ancestor, "
                "expected enabled since the fakes write all six files")

    for i in range(6):
        kind = expected_kinds[i]
        expected_filename = expected_filenames[kind]
        with page.expect_download(timeout=15000) as dl_info:
            buttons.nth(i).click()
        actual_filename = dl_info.value.suggested_filename
        _expect(actual_filename == expected_filename,
                f"artifact {kind!r} suggested_filename was {actual_filename!r}, "
                f"expected {expected_filename!r}")


def report_json_never_exposed(page, base):
    _expect(page.locator('[data-artifact="report_json"]').count() == 0,
            "found an element matching [data-artifact=\"report_json\"] on the "
            "results screen")
    _expect("report.json" not in page.content(),
            "the string 'report.json' appeared on the results screen")

    page.goto(base + "/#/files")
    page.wait_for_selector("#view")
    _expect("report.json" not in page.content(),
            "the string 'report.json' appeared on the Files screen")

    snap = page.request.get(base + "/api/runs/" + _STATE["run_id"]).json()
    artifacts = snap.get("artifacts", [])

    public_kinds = [a.get("kind") for a in artifacts]
    _expect("report_json" not in public_kinds,
            f"RunSnapshot.artifacts exposed kind 'report_json': {public_kinds!r}")

    public_filenames = [a.get("filename") for a in artifacts]
    _expect("report.json" not in public_filenames,
            f"RunSnapshot.artifacts exposed filename 'report.json': "
            f"{public_filenames!r}")

    allowed_kinds = ["report_pdf", "comments_csv", "key_messages_csv",
                     "themes_csv", "sentiment_csv", "emotions_csv"]
    _expect(public_kinds == allowed_kinds,
            f"RunSnapshot.artifacts kinds were {public_kinds!r}, expected "
            f"exactly the six public kinds in order {allowed_kinds!r}")


def aria_and_keyboard(page, base):
    page.goto(base + "/#/sessions/" + _STATE["session_id"] +
              "/campaigns/" + _STATE["campaign_id"])
    page.wait_for_selector("#km-container")

    _expect(page.get_attribute(".km-status", "role") == "status",
            "km-status role was not 'status'")
    _expect(page.get_attribute(".km-status", "aria-live") == "polite",
            "km-status aria-live was not 'polite'")

    _expect(page.get_attribute(".km-section", "aria-busy") is not None,
            "km-section had no aria-busy attribute")

    chips = page.locator("button.km-chip[data-km-inc]")
    chip_count = chips.count()
    _expect(chip_count > 0, "no button.km-chip[data-km-inc] elements found")
    for i in range(chip_count):
        val = chips.nth(i).get_attribute("aria-pressed")
        _expect(val in ("true", "false"),
                f"km-chip at index {i} had aria-pressed={val!r}, expected "
                "'true' or 'false'")

    _expect(page.get_attribute("#view", "tabindex") == "-1",
            "#view tabindex was not '-1'")

    label0_before = page.input_value("#km-label-0")
    label1_before = page.input_value("#km-label-1")
    page.focus('[data-km-up="1"]')
    is_active = page.evaluate(
        "(el) => document.activeElement === el",
        page.query_selector('[data-km-up="1"]'))
    _expect(is_active, "[data-km-up=\"1\"] did not become document.activeElement after focus")
    page.keyboard.press("Enter")

    deadline = time.time() + 5.0
    swapped = False
    while time.time() < deadline:
        if (page.input_value("#km-label-0") == label1_before and
                page.input_value("#km-label-1") == label0_before):
            swapped = True
            break
        time.sleep(0.05)
    _expect(swapped,
            "first two Key Message labels did not swap after Enter on "
            f"[data-km-up=\"1\"]; before: {[label0_before, label1_before]!r}, "
            f"after: {[page.input_value('#km-label-0'), page.input_value('#km-label-1')]!r}")


def skip_pause_control_is_accessible(page, base):
    _goto_campaign_at(page, base)
    box = page.locator("#chk-skip-pause")
    _expect(box.count() == 1, "#chk-skip-pause was not rendered")
    _expect(box.get_attribute("type") == "checkbox",
            "#chk-skip-pause is not an input of type checkbox")
    _expect(box.is_checked() is False,
            "#chk-skip-pause was checked on a fresh render; unchecked is the "
            "locked default")

    label = page.locator('label[for="chk-skip-pause"]')
    _expect(label.count() == 1,
            'no label[for="chk-skip-pause"] found, so the control is not '
            "label-associated")
    label_text = label.first.text_content()
    _expect("Skip the review step and run straight through" in label_text,
            f"label text was {label_text!r}")
    _expect("We will not pause to ask you to confirm the Key Messages."
            in label_text,
            f"helper text missing from label, text was {label_text!r}")

    box.focus()
    is_active = page.evaluate(
        "() => document.activeElement === document.getElementById('chk-skip-pause')")
    _expect(is_active, "#chk-skip-pause did not become document.activeElement")
    page.keyboard.press("Space")
    _expect(box.is_checked() is True,
            "#chk-skip-pause did not toggle on when Space was pressed")
    page.keyboard.press("Space")
    _expect(box.is_checked() is False,
            "#chk-skip-pause did not toggle off when Space was pressed again")


def skip_pause_failed_start_retains_state(page, base):
    _goto_campaign_at(page, base)
    page.locator("#chk-skip-pause").check()

    def _fail(route):
        route.fulfill(status=500, content_type="application/json",
                      body=json.dumps({"error": "SERVER_ERROR",
                                       "message": "Injected start failure.",
                                       "field": None}))

    page.route("**/api/sessions/*/runs", _fail)
    try:
        page.click("#btn-run")
        _dismiss_confirm_modals(page)
        deadline = time.time() + 10.0
        recovered = False
        while time.time() < deadline:
            if (page.locator("#chk-skip-pause").count() == 1
                    and page.get_attribute("#btn-run", "disabled") is None
                    and page.get_attribute("#chk-skip-pause", "disabled") is None):
                recovered = True
                break
            time.sleep(0.05)
        _expect(recovered,
                "#btn-run and #chk-skip-pause were not both re-enabled after a "
                "failed start")
        _expect(page.locator("#chk-skip-pause").is_checked() is True,
                "#chk-skip-pause lost its checked value after a failed start")
        is_active = page.evaluate(
            "() => document.activeElement === "
            "document.getElementById('chk-skip-pause')")
        _expect(is_active,
                "focus was not returned to #chk-skip-pause after a failed start")
        _expect(page.evaluate("location.hash").startswith("#/sessions/"),
                "the app navigated away from the campaign screen after a failed "
                f"start; hash is {page.evaluate('location.hash')!r}")
    finally:
        page.unroute("**/api/sessions/*/runs", _fail)

    # The failure path raises a native alert, which the module dialog handler
    # records. It is expected here, so clear it rather than letting
    # no_console_errors report it as an unexplained dialog.
    _expect(any("Injected start failure." in m for m in _DIALOG_MESSAGES),
            f"no alert carrying the injected start error was raised; dialogs "
            f"were {_DIALOG_MESSAGES!r}")
    _DIALOG_MESSAGES.clear()

    # Chromium logs the injected 500 as a console error. It is this case's own
    # fixture, not an app defect, so assert it arrived and then clear it rather
    # than letting no_console_errors report it.
    injected = [m for m in _CONSOLE_ERRORS if "500" in m]
    _expect(injected,
            "no console error recorded for the injected 500 response; "
            f"console errors were {_CONSOLE_ERRORS!r}")
    for message in injected:
        _CONSOLE_ERRORS.remove(message)


def skip_pause_unchecked_sends_false_and_pauses(page, base):
    run_id = _start_run_from_campaign(page, base, checked=False)
    body = _last_start_body(_STATE["session_id"])
    _expect(body is not None, "no POST body was captured for the start request")
    _expect(body.get("skipPause") is False,
            f"start body was {body!r}, expected skipPause exactly False")

    snap = _poll_run_snapshot_for(
        page, base, run_id, lambda s: s.get("stage") == "brief_pause", 60.0,
        "an unchecked start never reached stage brief_pause")
    _expect(snap.get("skipPause") is False,
            f"RunSnapshot.skipPause was {snap.get('skipPause')!r}, expected "
            "the JSON boolean False")

    page.goto(base + "/#/runs/" + run_id)
    page.wait_for_selector("#brief-review:visible")

    _drain_paused_run(page, base, run_id)


def skip_pause_checked_runs_straight_through(page, base):
    run_id = _start_run_from_campaign(page, base, checked=True)
    body = _last_start_body(_STATE["session_id"])
    _expect(body is not None, "no POST body was captured for the start request")
    _expect(body.get("skipPause") is True,
            f"start body was {body!r}, expected skipPause exactly True")

    seen_stages = []
    deadline = time.time() + 120.0
    snap = None
    while time.time() < deadline:
        snap = page.request.get(base + "/api/runs/" + run_id).json()
        stage = snap.get("stage")
        if stage not in seen_stages:
            seen_stages.append(stage)
        _expect(stage != "brief_pause",
                "a run started with skipPause:true entered stage brief_pause; "
                f"stages observed: {seen_stages!r}")
        if snap.get("status") in ("complete", "failed"):
            break
        time.sleep(0.1)
    _expect(snap is not None and snap.get("status") == "complete",
            f"a skipPause:true run did not complete without intervention; last "
            f"snapshot: {snap!r}, stages observed: {seen_stages!r}")
    _expect(snap.get("skipPause") is True,
            f"RunSnapshot.skipPause was {snap.get('skipPause')!r}, expected "
            "the JSON boolean True")
    _expect(page.locator("#brief-review:visible").count() == 0,
            "#brief-review was visible after a skipPause:true run completed")


def skip_pause_checked_zero_included_still_pauses(page, base):
    FAKES["reconcile_all_excluded"] = True
    try:
        run_id = _start_run_from_campaign(page, base, checked=True)
        body = _last_start_body(_STATE["session_id"])
        _expect(body is not None,
                "no POST body was captured for the start request")
        _expect(body.get("skipPause") is True,
                f"start body was {body!r}, expected skipPause exactly True")

        snap = _poll_run_snapshot_for(
            page, base, run_id, lambda s: s.get("stage") == "brief_pause", 60.0,
            "a skipPause:true run whose reconciled Key Messages are all "
            "excluded never reached brief_pause; the backend must override the "
            "skip rather than classify with nothing included")
        _expect(all(not bp.get("included") for bp in snap.get("briefPoints", [])),
                f"the reconcile fake did not exclude every point: "
                f"{snap.get('briefPoints')!r}")

        page.goto(base + "/#/runs/" + run_id)
        page.wait_for_selector("#brief-review:visible")

        _drain_paused_run(page, base, run_id)
    finally:
        FAKES["reconcile_all_excluded"] = False


def no_console_errors(page, base):
    _expect(_CONSOLE_ERRORS == [], f"console errors: {_CONSOLE_ERRORS}")
    _expect(_PAGE_ERRORS == [], f"page errors: {_PAGE_ERRORS}")
    _expect(_DIALOG_MESSAGES == [], f"native dialogs: {_DIALOG_MESSAGES}")


# ponytail: still not covered here - the missing-artifact disabled state,
# because the fakes in this file write all six files so that path never
# fires; backend restart recovery, out of scope by a locked decision; and
# reduced motion plus the dropped-control selector sweep, already covered by
# the Task 3.1 verification and a static CSS media query.


def main():
    srv = thread = port = browser = pw = None
    all_patches = _patches()
    for p in all_patches:
        p.start()

    try:
        srv, thread, port = _start_server()
        base = f"http://127.0.0.1:{port}"

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        # http_credentials answers the Basic challenge for navigations,
        # fetch/XHR, EventSource, and downloads from one place.
        context = browser.new_context(
            http_credentials={"username": "office", "password": "test-password"}
        )
        page = context.new_page()
        _register_network_capture(page)
        page.on("console", lambda msg: _CONSOLE_ERRORS.append(msg.text)
               if msg.type == "error" else None)
        page.on("pageerror", lambda exc: _PAGE_ERRORS.append(str(exc)))
        page.on("dialog", lambda dialog: (_DIALOG_MESSAGES.append(dialog.message),
                                          dialog.accept()))

        tests = [
            ("live_mode_engaged", live_mode_engaged),
            ("session_creation", session_creation),
            ("upload_asset_then_draft", upload_asset_then_draft),
            ("article_asset", article_asset),
            ("setup_edit_add_delete_order", setup_edit_add_delete_order),
            ("draft_failure_is_stale_then_retry", draft_failure_is_stale_then_retry),
            ("run_start", run_start),
            ("brief_pause_reopen_persisted", brief_pause_reopen_persisted),
            ("brief_pause_all_excluded_rejected", brief_pause_all_excluded_rejected),
            ("brief_pause_edit_and_proceed", brief_pause_edit_and_proceed),
            ("run_completes", run_completes),
            ("six_downloads_in_order", six_downloads_in_order),
            ("report_json_never_exposed", report_json_never_exposed),
            ("aria_and_keyboard", aria_and_keyboard),
            ("skip_pause_control_is_accessible", skip_pause_control_is_accessible),
            ("skip_pause_failed_start_retains_state", skip_pause_failed_start_retains_state),
            ("skip_pause_unchecked_sends_false_and_pauses", skip_pause_unchecked_sends_false_and_pauses),
            ("skip_pause_checked_runs_straight_through", skip_pause_checked_runs_straight_through),
            ("skip_pause_checked_zero_included_still_pauses", skip_pause_checked_zero_included_still_pauses),
            ("no_console_errors", no_console_errors),
        ]

        failed = 0
        for name, fn in tests:
            try:
                fn(page, base)
                print(f"  ok  {name}")
            except AssertionError as exc:
                print(f"  FAIL {name}: {exc}")
                print(traceback.format_exc())
                print(_api_log())
                try:
                    diag = page.evaluate(
                        "() => ({"
                        "demoApiMode: window.demoApi ? window.demoApi.mode : null,"
                        "hash: location.hash,"
                        "kmContainerExists: !!document.querySelector('#km-container'),"
                        "kmRowCount: document.querySelectorAll('.km-row').length,"
                        "labelValues: Array.from(document.querySelectorAll("
                        "'input.km-label-in')).map(el => el.value),"
                        "descValues: Array.from(document.querySelectorAll("
                        "'textarea.km-desc-in')).map(el => el.value),"
                        "kmSaveExists: !!document.querySelector('#km-save'),"
                        "kmSaveDisabled: (document.querySelector('#km-save') || {})"
                        ".disabled,"
                        "modalBackdropCount: document.querySelectorAll("
                        "'#overlay-root .modal-backdrop').length"
                        "})"
                    )
                    print(json.dumps(diag, indent=2))
                except Exception as diag_exc:
                    print(f"  (diagnostic evaluate failed: {diag_exc})")
                failed += 1
            except Exception as exc:
                print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
                print(traceback.format_exc())
                print(_api_log())
                try:
                    diag = page.evaluate(
                        "() => ({"
                        "demoApiMode: window.demoApi ? window.demoApi.mode : null,"
                        "hash: location.hash,"
                        "kmContainerExists: !!document.querySelector('#km-container'),"
                        "kmRowCount: document.querySelectorAll('.km-row').length,"
                        "labelValues: Array.from(document.querySelectorAll("
                        "'input.km-label-in')).map(el => el.value),"
                        "descValues: Array.from(document.querySelectorAll("
                        "'textarea.km-desc-in')).map(el => el.value),"
                        "kmSaveExists: !!document.querySelector('#km-save'),"
                        "kmSaveDisabled: (document.querySelector('#km-save') || {})"
                        ".disabled,"
                        "modalBackdropCount: document.querySelectorAll("
                        "'#overlay-root .modal-backdrop').length"
                        "})"
                    )
                    print(json.dumps(diag, indent=2))
                except Exception as diag_exc:
                    print(f"  (diagnostic evaluate failed: {diag_exc})")
                failed += 1

        if failed:
            print(f"\nFAIL ({failed}/{len(tests)} failed)")
            return 1
        print(f"\nPASS ({len(tests)}/{len(tests)})")
        return 0
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception as exc:
            print(f"  teardown warning: browser.close() failed: {exc}")
        try:
            if pw is not None:
                pw.stop()
        except Exception as exc:
            print(f"  teardown warning: playwright stop failed: {exc}")
        try:
            if srv is not None and thread is not None:
                _stop_server(srv, thread)
        except Exception as exc:
            print(f"  teardown warning: server shutdown failed: {exc}")
        try:
            for p in all_patches:
                p.stop()
        except Exception as exc:
            print(f"  teardown warning: patch stop failed: {exc}")
        try:
            db._DB_PATH = _ORIG_DB_PATH
            storage._ROOT = _ORIG_STORAGE_ROOT
        except Exception as exc:
            print(f"  teardown warning: global restore failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
