"""Generate ``data/sample_results.json`` — a full, deterministic group stage.

Used by the default ``sample`` feed provider so the site has lively demo data
out of the box (and so the feed has something to ingest offline / in tests).
Scores are derived deterministically from team names, and a handful of teams are
written with *external* spellings (e.g. "South Korea", "Turkey") to exercise the
feed's alias normalisation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "wcsweepstake" / "tournament_data.json").read_text("utf-8"))

# Canonical -> external spelling, to prove the feed's alias mapping works.
EXTERNAL = {
    "Korea Republic": "South Korea",
    "Türkiye": "Turkey",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "Curaçao": "Curacao",
    "Congo DR": "DR Congo",
}


def goals(team: str, opponent: str) -> int:
    h = hashlib.md5(f"{team}|{opponent}".encode()).hexdigest()
    return int(h[:2], 16) % 4  # 0..3, deterministic


def main() -> None:
    results = []
    for fx in DATA["fixtures"]:
        home, away = fx["home"], fx["away"]
        results.append({
            "stage": "GROUP_STAGE",
            "group": fx["group"],
            "match": fx["match"],
            "date": fx["date"],
            "home": EXTERNAL.get(home, home),
            "away": EXTERNAL.get(away, away),
            "home_score": goals(home, away),
            "away_score": goals(away, home),
            "status": "FINISHED",
        })
    out = ROOT / "data" / "sample_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Wrote {out.relative_to(ROOT)} with {len(results)} finished group matches")


if __name__ == "__main__":
    main()
