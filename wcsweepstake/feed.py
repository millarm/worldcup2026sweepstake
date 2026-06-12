"""Results feed — automatically pulls match results and populates the store.

Providers are selected automatically by environment:

* ``football-data``  — football-data.org, when ``FOOTBALL_DATA_API_KEY`` is set.
* ``url``            — any JSON endpoint, when ``WC_RESULTS_URL`` is set.
* ``espn``           — ESPN's public scoreboard API, no key required (default).
* ``sample``         — bundled ``data/sample_results.json`` (``WC_FEED_SOURCE=sample``).

:func:`start_auto_feed` runs a background thread that refreshes on an interval so
the site updates itself without manual intervention.

Every provider yields a list of *normalised* results::

    {"home": str, "away": str, "home_score": int, "away_score": int,
     "status": "FINISHED", "stage": str | None}

:func:`apply_feed` maps each finished result onto the right group fixture or
knockout match (resolving the live bracket so knockout pairings are known) and
writes it to the :class:`~wcsweepstake.store.Store`.  Team names from external
sources are normalised through :data:`TEAM_ALIASES` to our canonical spellings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import DATA, engine

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_results.json"

# External / provider spellings -> our canonical team names.
TEAM_ALIASES = {
    "south korea": "Korea Republic", "korea republic": "Korea Republic",
    "republic of korea": "Korea Republic",
    "usa": "USA", "united states": "USA", "united states of america": "USA",
    "turkey": "Türkiye", "turkiye": "Türkiye", "türkiye": "Türkiye",
    "iran": "IR Iran", "ir iran": "IR Iran",
    "ivory coast": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
    "côte d'ivoire": "Côte d'Ivoire",
    "cape verde": "Cabo Verde", "cabo verde": "Cabo Verde",
    "curacao": "Curaçao", "curaçao": "Curaçao",
    "czech republic": "Czechia", "czechia": "Czechia",
    "dr congo": "Congo DR", "democratic republic of the congo": "Congo DR",
    "congo dr": "Congo DR", "congo dr (congo)": "Congo DR",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "colombia": "Colombia", "columbia": "Colombia",
}

_CANONICAL = {t["country"] for t in DATA["teams"]}


def canonical_team(name: str) -> str | None:
    """Map a provider team name to our canonical spelling (or ``None``)."""
    if not name:
        return None
    name = name.strip()
    if name in _CANONICAL:
        return name
    return TEAM_ALIASES.get(name.lower())


# --------------------------------------------------------------------------- #
#  Providers
# --------------------------------------------------------------------------- #
class SampleProvider:
    name = "sample"

    def __init__(self, path: Path | None = None):
        self.path = path or SAMPLE_PATH

    def fetch(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))


class UrlProvider:
    """Reads a plain JSON array of normalised results from ``WC_RESULTS_URL``."""
    name = "url"

    def __init__(self, url: str):
        self.url = url

    def fetch(self) -> list[dict]:
        import requests  # imported lazily so the package works without network
        resp = requests.get(self.url, timeout=20)
        resp.raise_for_status()
        return resp.json()


class FootballDataProvider:
    """football-data.org (free tier covers major competitions).

    Set ``FOOTBALL_DATA_API_KEY``; optionally ``FOOTBALL_DATA_COMPETITION``
    (defaults to ``WC``).  Normalises the API's match objects to our schema.
    """
    name = "football-data"

    def __init__(self, api_key: str, competition: str | None = None):
        self.api_key = api_key
        self.competition = competition or os.environ.get("FOOTBALL_DATA_COMPETITION", "WC")

    def fetch(self) -> list[dict]:
        import requests
        url = f"https://api.football-data.org/v4/competitions/{self.competition}/matches"
        resp = requests.get(url, headers={"X-Auth-Token": self.api_key}, timeout=20)
        resp.raise_for_status()
        out = []
        for m in resp.json().get("matches", []):
            score = (m.get("score") or {}).get("fullTime") or {}
            out.append({
                "home": (m.get("homeTeam") or {}).get("name"),
                "away": (m.get("awayTeam") or {}).get("name"),
                "home_score": score.get("home"),
                "away_score": score.get("away"),
                "status": m.get("status"),
                "stage": m.get("stage"),
            })
        return out


def parse_espn_scoreboard(data: dict) -> list[dict]:
    """Normalise an ESPN scoreboard payload into our result dicts.

    Split out from the HTTP call so it can be unit-tested with canned JSON.
    """
    out = []
    for event in data.get("events", []) or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        status_type = ((comp.get("status") or {}).get("type")) or {}
        home = away = None
        for c in comp.get("competitors") or []:
            if c.get("homeAway") == "home":
                home = c
            elif c.get("homeAway") == "away":
                away = c
        if not home or not away:
            continue

        def team_name(c):
            t = c.get("team") or {}
            return (t.get("displayName") or t.get("name")
                    or t.get("shortDisplayName") or t.get("abbreviation"))

        def score(c):
            try:
                return int(c.get("score"))
            except (TypeError, ValueError):
                return None

        finished = bool(status_type.get("completed"))
        out.append({
            "home": team_name(home), "away": team_name(away),
            "home_score": score(home), "away_score": score(away),
            "status": "FINISHED" if finished else (status_type.get("state") or "SCHEDULED").upper(),
            "stage": (event.get("season") or {}).get("slug"),
        })
    return out


class EspnProvider:
    """Live results from ESPN's public scoreboard API — no API key required.

    Walks the tournament's days (start .. min(today, end)) and aggregates
    finished matches. League slug and window are configurable via env
    (``WC_ESPN_LEAGUE``, ``WC_TOURNAMENT_START``/``_END``).
    """
    name = "espn"
    BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

    def __init__(self, league=None, start=None, end=None, dates=None):
        self.league = league or os.environ.get("WC_ESPN_LEAGUE", "fifa.world")
        self.start = start or os.environ.get("WC_TOURNAMENT_START", "2026-06-11")
        self.end = end or os.environ.get("WC_TOURNAMENT_END", "2026-07-19")
        self.dates = dates

    def _days(self) -> list[str]:
        import datetime as dt
        start = dt.date.fromisoformat(self.start)
        end = min(dt.date.fromisoformat(self.end), dt.date.today())
        days, d = [], start
        while d <= end:
            days.append(d.strftime("%Y%m%d"))
            d += dt.timedelta(days=1)
        return days

    def fetch(self) -> list[dict]:
        import requests
        url = self.BASE.format(league=self.league)
        out = []
        for day in (self.dates or self._days()):
            try:
                resp = requests.get(url, params={"dates": day}, timeout=15)
                resp.raise_for_status()
                out.extend(parse_espn_scoreboard(resp.json()))
            except Exception:
                continue  # skip a bad day, keep collecting the rest
        return out


def select_provider() -> object:
    """Pick a provider based on the environment.

    Priority: football-data.org (if ``FOOTBALL_DATA_API_KEY``) -> custom URL
    (``WC_RESULTS_URL``) -> ``WC_FEED_SOURCE`` (``espn`` default, or ``sample``).
    """
    if os.environ.get("FOOTBALL_DATA_API_KEY"):
        return FootballDataProvider(os.environ["FOOTBALL_DATA_API_KEY"])
    if os.environ.get("WC_RESULTS_URL"):
        return UrlProvider(os.environ["WC_RESULTS_URL"])
    source = os.environ.get("WC_FEED_SOURCE", "espn").lower()
    if source == "sample":
        return SampleProvider()
    return EspnProvider()


# --------------------------------------------------------------------------- #
#  On-demand overdue check
# --------------------------------------------------------------------------- #
def is_refresh_overdue(last_ran_at: str | None,
                       duration_mins: int = 115,
                       tz_name: str = "UTC") -> bool:
    """Return True if a scheduled refresh time has passed since the last feed run.

    Used to trigger a lazy background refresh when a visitor hits the site,
    so stale results are corrected on the next request without needing a
    long-running scheduler thread.

    ``last_ran_at`` is the ISO-8601 timestamp from :meth:`Store.last_feed`
    (``None`` if the feed has never run).
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    times = scheduled_refresh_times(duration_mins, tz_name)

    if last_ran_at is None:
        return any(t <= now for t in times)

    try:
        last = _dt.datetime.fromisoformat(last_ran_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return False

    return any(last < t <= now for t in times)


# --------------------------------------------------------------------------- #
#  Automatic background polling
# --------------------------------------------------------------------------- #
_auto_thread = None


def start_auto_feed(store, interval: int = 900, provider=None):
    """Start a daemon thread that refreshes the feed every ``interval`` seconds.

    Idempotent — a second call while one is running is a no-op. The first poll
    happens after ``interval`` (initial population is handled by autoseed).
    """
    import threading
    global _auto_thread
    if _auto_thread and _auto_thread.is_alive():
        return _auto_thread

    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                apply_feed(store, provider)
            except Exception:
                pass  # transient feed/network errors must not kill the poller

    _auto_thread = threading.Thread(target=loop, name="wc-auto-feed", daemon=True)
    _auto_thread._stop_event = stop  # exposed for tests
    _auto_thread.start()
    return _auto_thread


# --------------------------------------------------------------------------- #
#  Schedule-based feed triggers
# --------------------------------------------------------------------------- #
_scheduled_thread = None


def scheduled_refresh_times(duration_mins: int = 115, tz_name: str = "UTC") -> list:
    """Return a sorted list of UTC datetimes when a feed refresh should fire.

    Each datetime is ``duration_mins`` after the scheduled kick-off of each
    fixture (i.e. roughly when the game should have ended).  Duplicate times
    (simultaneous kick-offs) are collapsed to a single entry.

    ``tz_name`` is the IANA timezone the ``ko`` times in the fixture data are
    expressed in (e.g. ``"UTC"``, ``"America/New_York"``).  Defaults to UTC.
    """
    import datetime as dt
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone.utc

    duration = dt.timedelta(minutes=duration_mins)
    seen: set = set()
    times: list = []
    for fx in DATA["fixtures"]:
        try:
            date_str = fx["date"]       # "YYYY-MM-DD"
            ko_str = fx["ko"]           # "HH:MM"
            naive = dt.datetime.fromisoformat(f"{date_str}T{ko_str}:00")
            aware = naive.replace(tzinfo=tz)
            fire_at = (aware + duration).astimezone(dt.timezone.utc)
            key = fire_at.replace(second=0, microsecond=0)
            if key not in seen:
                seen.add(key)
                times.append(fire_at)
        except Exception:
            continue
    times.sort()
    return times


def start_scheduled_feed(store, duration_mins: int = 115, tz_name: str = "UTC",
                         provider=None):
    """Start a daemon thread that fires a feed refresh after each scheduled game ends.

    The thread sleeps until the next game's expected end time, refreshes the
    feed, then moves on to the next game.  Games whose scheduled end time is
    already in the past are skipped on startup (a one-time catch-up refresh is
    run immediately if any past games were skipped).

    Idempotent — a second call while one is running is a no-op.

    Environment variables (read by :func:`app._maybe_start_scheduled_feed`):
    - ``WC_FEED_SCHEDULED=1``      — enables this scheduler
    - ``WC_GAME_DURATION_MINS``    — minutes after KO to refresh (default 115)
    - ``WC_FIXTURE_TZ``            — IANA tz of the ``ko`` times (default UTC)
    """
    import threading

    global _scheduled_thread
    if _scheduled_thread and _scheduled_thread.is_alive():
        return _scheduled_thread

    stop = threading.Event()

    def _loop():
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        times = scheduled_refresh_times(duration_mins, tz_name)

        future = [t for t in times if t > now]
        past   = [t for t in times if t <= now]

        if past:
            # Catch-up: any games that have already ended — refresh once on startup.
            try:
                apply_feed(store, provider)
            except Exception:
                pass

        for fire_at in future:
            now = _dt.datetime.now(_dt.timezone.utc)
            wait_secs = (fire_at - now).total_seconds()
            if wait_secs > 0:
                if stop.wait(wait_secs):
                    break  # shutdown requested
            try:
                apply_feed(store, provider)
            except Exception:
                pass  # transient errors must not kill the scheduler

    _scheduled_thread = threading.Thread(target=_loop, name="wc-scheduled-feed",
                                         daemon=True)
    _scheduled_thread._stop_event = stop  # exposed for tests
    _scheduled_thread.start()
    return _scheduled_thread


# --------------------------------------------------------------------------- #
#  Applying a feed to the store
# --------------------------------------------------------------------------- #
def _group_fixture_index() -> dict[frozenset, dict]:
    """{frozenset({home, away}): fixture} for unordered pair matching."""
    return {frozenset({fx["home"], fx["away"]}): fx for fx in DATA["fixtures"]}


def _is_finished(result: dict) -> bool:
    if str(result.get("status", "FINISHED")).upper() != "FINISHED":
        return False
    return result.get("home_score") is not None and result.get("away_score") is not None


def apply_feed(store, provider=None) -> dict:
    """Fetch results from ``provider`` and write any new/changed ones to ``store``.

    Returns a summary ``{"source", "fetched", "updated", "unmatched", "ok",
    "message"}`` and records a row in the store's feed log.
    """
    provider = provider or select_provider()
    summary = {"source": getattr(provider, "name", "unknown"),
               "fetched": 0, "updated": 0, "unmatched": [], "ok": True, "message": ""}
    try:
        raw = provider.fetch()
    except Exception as exc:  # network/parse failure -> logged, not fatal
        summary["ok"] = False
        summary["message"] = f"{type(exc).__name__}: {exc}"
        store.log_feed(summary["source"], 0, False, summary["message"])
        return summary

    summary["fetched"] = len(raw)
    group_index = _group_fixture_index()
    group_results = store.group_results()
    ko_results = store.ko_results()
    updated = 0

    # --- Group-stage results first (they unlock knockout pairings) --------- #
    pending_ko: list[dict] = []
    for item in raw:
        if not _is_finished(item):
            continue
        home = canonical_team(item.get("home"))
        away = canonical_team(item.get("away"))
        hs, as_ = item.get("home_score"), item.get("away_score")
        if not home or not away:
            summary["unmatched"].append(f"{item.get('home')} v {item.get('away')}")
            continue
        fx = group_index.get(frozenset({home, away}))
        if fx:
            # Store in the fixture's own home/away orientation.
            if home == fx["home"]:
                gh, ga = hs, as_
            else:
                gh, ga = as_, hs
            prev = group_results.get(fx["match"])
            if not prev or prev.get("home") != gh or prev.get("away") != ga:
                store.set_group_result(fx["match"], gh, ga)
                group_results[fx["match"]] = {"home": gh, "away": ga}
                updated += 1
        else:
            pending_ko.append({"home": home, "away": away, "hs": hs, "as": as_})

    # --- Knockout results: resolve the live bracket to find pairings ------- #
    if pending_ko:
        standings = engine.group_standings(DATA["teams"], group_results)
        for item in pending_ko:
            matches = engine.resolve_bracket(standings, ko_results)
            target = None
            for number, m in matches.items():
                if m.team1 and m.team2 and frozenset({m.team1, m.team2}) == \
                        frozenset({item["home"], item["away"]}):
                    target = (number, m)
                    break
            if not target:
                summary["unmatched"].append(f"{item['home']} v {item['away']}")
                continue
            number, m = target
            if item["home"] == m.team1:
                s1, s2 = item["hs"], item["as"]
            else:
                s1, s2 = item["as"], item["hs"]
            prev = ko_results.get(number)
            if not prev or prev.get("score1") != s1 or prev.get("score2") != s2:
                store.set_ko_result(number, s1, s2)
                ko_results[number] = {"score1": s1, "score2": s2,
                                      "override": (prev or {}).get("override")}
                updated += 1

    summary["updated"] = updated
    summary["message"] = (f"Applied {updated} result(s) from {summary['source']}; "
                          f"{len(summary['unmatched'])} unmatched.")
    store.log_feed(summary["source"], updated, True, summary["message"])
    return summary
