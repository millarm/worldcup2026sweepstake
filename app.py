"""Flask backend for the World Cup 2026 sweepstake.

Routes
------
GET  /                       -> the single-page frontend
GET  /api/state              -> full computed tournament state (+ feed status)
GET  /api/fixtures           -> group fixtures with scores/points
GET  /api/bracket            -> knockout bracket
GET  /api/leaderboard        -> sweepstake leaderboard + prize allocation
POST /api/results/group      -> {match, home, away}            (admin)
DEL  /api/results/group/<m>  -> clear a group result           (admin)
POST /api/results/ko         -> {match_no, score1, score2, override} (admin)
POST /api/feed/refresh       -> pull results from the feed and populate (admin)
GET  /api/feed/status        -> last feed run
POST /api/admin/login        -> validate the admin password
POST /api/admin/reset        -> wipe all stored results        (admin)

The admin panel is password-protected: admin routes require the password in the
``X-Admin-Token`` header (or ``admin_password`` JSON field).  The password is
``ADMIN_PASSWORD``/``ADMIN_TOKEN`` from the environment, falling back to a
built-in default so the deployed site works out of the box.
"""
from __future__ import annotations

import hmac
import os
import threading
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory

from wcsweepstake import DATA, compute_state, feed
from wcsweepstake.store import Store

app = Flask(__name__, static_folder="static", static_url_path="/static")
store = Store()

# Password protecting the admin panel (feed refresh, score entry, reset).
# Must be set via the ADMIN_PASSWORD (or legacy ADMIN_TOKEN) environment
# variable.  When unset, all admin routes return 503.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_TOKEN") or None


def _maybe_autoseed() -> None:
    """Populate an empty store from the feed on first boot when WC_AUTOSEED is set.

    Lets a fresh Replit deploy show a lively demo immediately. Off by default so
    a clean install starts empty.
    """
    if not os.environ.get("WC_AUTOSEED"):
        return
    if store.group_results() or store.last_feed():
        return
    try:
        feed.apply_feed(store)
    except Exception:  # never let a feed hiccup stop the server starting
        pass


def _maybe_start_auto_feed() -> None:
    """Start the background poller when WC_FEED_AUTO is set (see feed.start_auto_feed)."""
    if not os.environ.get("WC_FEED_AUTO"):
        return
    interval = int(os.environ.get("WC_FEED_INTERVAL", "900"))
    feed.start_auto_feed(store, interval=interval)


def _maybe_start_scheduled_feed() -> None:
    """Start the schedule-based feed when WC_FEED_SCHEDULED is set.

    Fires a feed refresh after each game's scheduled end time.  Configure with:
      WC_FEED_SCHEDULED=1         — enable
      WC_GAME_DURATION_MINS=115   — minutes after KO to refresh (default 115)
      WC_FIXTURE_TZ=UTC           — IANA timezone of the ko times in fixture data
    """
    if not os.environ.get("WC_FEED_SCHEDULED"):
        return
    duration_mins = int(os.environ.get("WC_GAME_DURATION_MINS", "115"))
    tz_name = os.environ.get("WC_FIXTURE_TZ", "UTC")
    feed.start_scheduled_feed(store, duration_mins=duration_mins, tz_name=tz_name)


_maybe_autoseed()
_maybe_start_auto_feed()
_maybe_start_scheduled_feed()

# Lock that prevents more than one background lazy-refresh running at a time.
_lazy_refresh_lock = threading.Lock()


def _maybe_lazy_refresh() -> None:
    """Fire a background feed refresh if results are overdue, non-blocking.

    Called on every state-returning request.  The current request returns
    immediately with the existing data; the *next* request will see fresh
    results.  The lock ensures only one background refresh runs at a time
    even if many visitors hit the site simultaneously.

    Controlled by the same env vars as the scheduled feed:
      WC_GAME_DURATION_MINS  — minutes after KO before results are expected
      WC_FIXTURE_TZ          — IANA timezone of the fixture ko times
    """
    last = store.last_feed()
    last_ran_at = last["ran_at"] if last else None
    duration_mins = int(os.environ.get("WC_GAME_DURATION_MINS", "115"))
    tz_name = os.environ.get("WC_FIXTURE_TZ", "UTC")
    if not feed.is_refresh_overdue(last_ran_at, duration_mins, tz_name):
        return
    if not _lazy_refresh_lock.acquire(blocking=False):
        return  # another refresh is already in flight
    def _run():
        try:
            feed.apply_feed(store)
        finally:
            _lazy_refresh_lock.release()
    threading.Thread(target=_run, name="wc-lazy-refresh", daemon=True).start()


def _state() -> dict:
    state = compute_state(store.group_results(), store.ko_results())
    state["feed"] = store.last_feed()
    return state


def _password_ok(supplied: str | None) -> bool:
    """Constant-time check of a supplied admin password.

    Returns False (never grants access) when ADMIN_PASSWORD is not configured.
    """
    if not ADMIN_PASSWORD:
        return False
    return bool(supplied) and hmac.compare_digest(str(supplied), ADMIN_PASSWORD)


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Password may arrive as the X-Admin-Token header (used by the panel) or
        # an `admin_password` field in a JSON body.
        supplied = request.headers.get("X-Admin-Token")
        if supplied is None and request.is_json:
            supplied = (request.get_json(silent=True) or {}).get("admin_password")
        if not _password_ok(supplied):
            return jsonify({"error": "unauthorised"}), 401
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- #
#  Frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "tournament": DATA["tournament"]})


# --------------------------------------------------------------------------- #
#  Read APIs
# --------------------------------------------------------------------------- #
@app.get("/api/state")
def api_state():
    _maybe_lazy_refresh()
    return jsonify(_state())


@app.get("/api/fixtures")
def api_fixtures():
    state = _state()
    return jsonify({"fixtures": state["fixtures"]})


@app.get("/api/bracket")
def api_bracket():
    state = _state()
    return jsonify({"bracket": state["bracket"], "third_placed": state["third_placed"]})


@app.get("/api/leaderboard")
def api_leaderboard():
    state = _state()
    return jsonify({
        "leaderboard": state["leaderboard"],
        "prizes": state["prizes"],
        "prize_pot": state["prize_pot"],
    })


@app.get("/api/feed/status")
def api_feed_status():
    return jsonify({"feed": store.last_feed()})


@app.get("/api/feed/schedule")
def api_feed_schedule():
    """Return upcoming feed-refresh times as ISO-8601 UTC strings.

    Derived from fixture kick-off times plus the configured game duration.
    Only future times are included (past ones have already been handled).
    """
    import datetime as dt
    duration_mins = int(os.environ.get("WC_GAME_DURATION_MINS", "115"))
    tz_name = os.environ.get("WC_FIXTURE_TZ", "UTC")
    now = dt.datetime.now(dt.timezone.utc)
    times = feed.scheduled_refresh_times(duration_mins, tz_name)
    upcoming = [t.isoformat() for t in times if t > now]
    return jsonify({"schedule": upcoming})


# --------------------------------------------------------------------------- #
#  Write APIs (admin)
# --------------------------------------------------------------------------- #
@app.post("/api/admin/login")
def api_admin_login():
    """Validate the admin password so the panel can unlock its controls."""
    body = request.get_json(force=True, silent=True) or {}
    if _password_ok(body.get("password")):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "incorrect password"}), 401


@app.post("/api/results/group")
@admin_required
def api_set_group():
    body = request.get_json(force=True, silent=True) or {}
    try:
        match = str(body["match"])
        home, away = int(body["home"]), int(body["away"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "expected {match, home, away}"}), 400
    if match not in {fx["match"] for fx in DATA["fixtures"]}:
        return jsonify({"error": f"unknown match {match}"}), 404
    store.set_group_result(match, home, away)
    return jsonify(_state())


@app.delete("/api/results/group/<match>")
@admin_required
def api_clear_group(match):
    store.clear_group_result(match)
    return jsonify(_state())


@app.post("/api/results/ko")
@admin_required
def api_set_ko():
    body = request.get_json(force=True, silent=True) or {}
    try:
        match_no = int(body["match_no"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "expected {match_no, score1?, score2?, override?}"}), 400
    store.set_ko_result(
        match_no,
        score1=body.get("score1"), score2=body.get("score2"),
        override=body.get("override"),
    )
    return jsonify(_state())


@app.post("/api/feed/refresh")
@admin_required
def api_feed_refresh():
    summary = feed.apply_feed(store)
    state = _state()
    state["feed_summary"] = summary
    return jsonify(state)


@app.post("/api/admin/reset")
@admin_required
def api_reset():
    store.clear_all()
    return jsonify(_state())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
