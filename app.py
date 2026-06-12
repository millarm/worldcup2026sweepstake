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
POST /api/admin/reset        -> wipe all stored results        (admin)

Admin routes require the ``X-Admin-Token`` header to equal ``ADMIN_TOKEN`` when
that environment variable is set; if it is unset (local dev) they are open.
"""
from __future__ import annotations

import os
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory

from wcsweepstake import DATA, compute_state, feed
from wcsweepstake.store import Store

app = Flask(__name__, static_folder="static", static_url_path="/static")
store = Store()


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


_maybe_autoseed()


def _state() -> dict:
    state = compute_state(store.group_results(), store.ko_results())
    state["feed"] = store.last_feed()
    return state


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = os.environ.get("ADMIN_TOKEN")
        if token and request.headers.get("X-Admin-Token") != token:
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


# --------------------------------------------------------------------------- #
#  Write APIs (admin)
# --------------------------------------------------------------------------- #
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
