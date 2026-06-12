"""Results feed — automatically pulls match results and populates the store.

Three providers are supported, selected automatically by environment:

* ``football-data``  — football-data.org, when ``FOOTBALL_DATA_API_KEY`` is set.
* ``url``            — any JSON endpoint, when ``WC_RESULTS_URL`` is set.
* ``sample``         — bundled ``data/sample_results.json`` (default; offline).

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


def select_provider() -> object:
    """Pick a provider based on the environment (see module docstring)."""
    if os.environ.get("FOOTBALL_DATA_API_KEY"):
        return FootballDataProvider(os.environ["FOOTBALL_DATA_API_KEY"])
    if os.environ.get("WC_RESULTS_URL"):
        return UrlProvider(os.environ["WC_RESULTS_URL"])
    return SampleProvider()


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
