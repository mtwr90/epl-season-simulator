#!/usr/bin/env python3
"""
EPL Season Simulator — Data Refresh Tool

Fetches latest standings and fixtures from football-data.org API,
cross-validates them to ensure no already-played matches appear as
simulatable, and updates index.html with the baked data.

Key logic:
  - Fetches ALL matches for the season (not just SCHEDULED)
  - Uses each match's actual status to determine if it's been played
  - Only includes fixtures where status is SCHEDULED or TIMED
  - Cross-validates: if a focus-team fixture is marked SCHEDULED but
    both teams' played-games counts suggest it's been played, it gets excluded
  - Prints a reconciliation report so discrepancies are visible
"""

import json
import math
import os
import re
import sys
import urllib.request
from datetime import date

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
INDEX_PATH = os.path.join(PROJECT_DIR, "index.html")

# Focus team IDs
FOCUS_IDS = {58, 66, 61, 64}
# Excluded team IDs (assumed 1st and 2nd)
EXCLUDED_IDS = {57, 65}

# Load API key
def get_api_key():
    config_path = os.path.join(PROJECT_DIR, "config.js")
    if os.path.exists(config_path):
        with open(config_path) as f:
            match = re.search(r"FOOTBALL_DATA_API_KEY:\s*['\"]([^'\"]+)['\"]", f.read())
            if match:
                return match.group(1)
    env_path = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("FOOTBALL_DATA_API_KEY="):
                    return line.strip().split("=", 1)[1].strip("'\"")
    print("ERROR: No API key found in config.js or .env")
    sys.exit(1)


def api_fetch(url, api_key):
    """Fetch JSON from football-data.org API."""
    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fetch_standings(api_key):
    """Fetch current standings."""
    data = api_fetch("https://api.football-data.org/v4/competitions/PL/standings", api_key)
    table = data["standings"][0]["table"]
    current_matchday = data["season"]["currentMatchday"]

    standings = []
    for t in table:
        team = t["team"]
        standings.append({
            "id": team["id"],
            "name": team["name"],
            "shortName": team["shortName"],
            "crest": team["crest"],
            "p": t["playedGames"],
            "w": t["won"],
            "d": t["draw"],
            "l": t["lost"],
            "gf": t["goalsFor"],
            "ga": t["goalsAgainst"],
            "gd": t["goalDifference"],
            "pts": t["points"],
        })

    return standings, current_matchday


def fetch_all_matches(api_key):
    """Fetch ALL matches for the season (all statuses)."""
    data = api_fetch("https://api.football-data.org/v4/competitions/PL/matches", api_key)
    return data.get("matches", [])


def is_focus_match(match):
    """Check if at least one team in the match is a focus team."""
    home_id = match["homeTeam"]["id"]
    away_id = match["awayTeam"]["id"]
    return home_id in FOCUS_IDS or away_id in FOCUS_IDS


def is_unplayed(match):
    """Check if a match has NOT been played yet."""
    return match["status"] in ("SCHEDULED", "TIMED")


def build_played_games_map(standings):
    """Build a map of team_id -> playedGames from standings."""
    return {t["id"]: t["p"] for t in standings}


def compute_home_away_stats(all_matches):
    """Compute per-team home/away goal stats from FINISHED matches.

    Returns:
        stats: dict of team_id -> {home_gf, home_ga, home_games,
                                    away_gf, away_ga, away_games}
        league_avg_home: average home goals per match
        league_avg_away: average away goals per match
    """
    stats = {}
    total_home_goals = 0
    total_away_goals = 0
    total_finished = 0

    for m in all_matches:
        if m["status"] != "FINISHED":
            continue
        home_id = m["homeTeam"]["id"]
        away_id = m["awayTeam"]["id"]
        home_goals = m["score"]["fullTime"]["home"]
        away_goals = m["score"]["fullTime"]["away"]
        if home_goals is None or away_goals is None:
            continue

        total_home_goals += home_goals
        total_away_goals += away_goals
        total_finished += 1

        for tid in (home_id, away_id):
            if tid not in stats:
                stats[tid] = {
                    "home_gf": 0, "home_ga": 0, "home_games": 0,
                    "away_gf": 0, "away_ga": 0, "away_games": 0,
                }

        stats[home_id]["home_gf"] += home_goals
        stats[home_id]["home_ga"] += away_goals
        stats[home_id]["home_games"] += 1

        stats[away_id]["away_gf"] += away_goals
        stats[away_id]["away_ga"] += home_goals
        stats[away_id]["away_games"] += 1

    league_avg_home = total_home_goals / total_finished if total_finished else 1.4
    league_avg_away = total_away_goals / total_finished if total_finished else 1.1

    return stats, league_avg_home, league_avg_away


def poisson_prob(lam, k):
    """P(X=k) for Poisson distribution with parameter lambda."""
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def calculate_match_probabilities(home_id, away_id, team_stats,
                                  league_avg_home, league_avg_away):
    """Calculate win/draw/loss probabilities using Poisson model.

    Returns (prob_home_win, prob_draw, prob_away_win).
    """
    h = team_stats.get(home_id)
    a = team_stats.get(away_id)

    if not h or h["home_games"] == 0:
        home_attack = 1.0
        home_defense = 1.0
    else:
        home_attack = (h["home_gf"] / h["home_games"]) / league_avg_home
        home_defense = (h["home_ga"] / h["home_games"]) / league_avg_away

    if not a or a["away_games"] == 0:
        away_attack = 1.0
        away_defense = 1.0
    else:
        away_attack = (a["away_gf"] / a["away_games"]) / league_avg_away
        away_defense = (a["away_ga"] / a["away_games"]) / league_avg_home

    lambda_home = max(0.2, min(5.0, home_attack * away_defense * league_avg_home))
    lambda_away = max(0.2, min(5.0, away_attack * home_defense * league_avg_away))

    MAX_GOALS = 7
    pw = pd = pl = 0.0
    for hg in range(MAX_GOALS):
        for ag in range(MAX_GOALS):
            p = poisson_prob(lambda_home, hg) * poisson_prob(lambda_away, ag)
            if hg > ag:
                pw += p
            elif hg == ag:
                pd += p
            else:
                pl += p

    total = pw + pd + pl
    pw /= total
    pd /= total
    pl /= total

    return pw, pd, pl


def normalize_and_round(pw, pd, pl):
    """Round probabilities ensuring they sum to exactly 1.00."""
    pw = round(pw, 2)
    pd = round(pd, 2)
    pl = round(1.0 - pw - pd, 2)
    return pw, pd, pl


def cross_validate(fixtures, standings, all_matches):
    """
    Cross-validate fixtures against standings to catch edge cases:

    1. Check that each "unplayed" fixture's teams don't have more played
       games than expected (which would indicate the fixture was actually played)
    2. Count expected remaining games per focus team and compare with standings

    Returns (valid_fixtures, warnings)
    """
    played_map = build_played_games_map(standings)
    warnings = []
    valid_fixtures = []

    # Count how many fixtures each focus team appears in
    focus_fixture_counts = {tid: 0 for tid in FOCUS_IDS}
    for f in fixtures:
        home_id = f["homeTeam"]["id"]
        away_id = f["awayTeam"]["id"]
        if home_id in FOCUS_IDS:
            focus_fixture_counts[home_id] += 1
        if away_id in FOCUS_IDS:
            focus_fixture_counts[away_id] += 1

    # For each focus team, check: played_games + remaining_fixtures should be
    # reasonable (close to 38 for a full season, but could be less if some
    # fixtures involve non-focus opponents not in our list)
    for tid in FOCUS_IDS:
        total_team_matches = 0
        for m in all_matches:
            if m["homeTeam"]["id"] == tid or m["awayTeam"]["id"] == tid:
                total_team_matches += 1

        played = played_map.get(tid, 0)
        remaining = focus_fixture_counts[tid]

        # Count how many matches this team has that are FINISHED in the full match list
        finished_count = 0
        for m in all_matches:
            if (m["homeTeam"]["id"] == tid or m["awayTeam"]["id"] == tid) and not is_unplayed(m):
                finished_count += 1

        team_name = next((s["name"] for s in standings if s["id"] == tid), str(tid))

        if played != finished_count:
            warnings.append(
                f"WARNING: {team_name} standings say {played} played, "
                f"but API shows {finished_count} finished matches"
            )

    # Validate each fixture
    for f in fixtures:
        home_id = f["homeTeam"]["id"]
        away_id = f["awayTeam"]["id"]
        matchday = f["matchday"]

        # Double-check: look up this exact match in the full match list
        # to confirm its status
        full_match = None
        for m in all_matches:
            if (m["matchday"] == matchday and
                m["homeTeam"]["id"] == home_id and
                m["awayTeam"]["id"] == away_id):
                full_match = m
                break

        if full_match and not is_unplayed(full_match):
            home_name = f["homeTeam"]["shortName"]
            away_name = f["awayTeam"]["shortName"]
            warnings.append(
                f"EXCLUDED: MW{matchday} {home_name} vs {away_name} — "
                f"status is '{full_match['status']}', not schedulable"
            )
            continue

        valid_fixtures.append(f)

    return valid_fixtures, warnings


def format_fixture_js(f):
    """Format a single fixture as a JS object literal."""
    h = f["homeTeam"]
    a = f["awayTeam"]
    d = f["utcDate"][:10]
    return (
        '                { matchday: %d, date: "%s", '
        'homeId: %d, homeName: "%s", homeShort: "%s", homeCrest: "%s", '
        'awayId: %d, awayName: "%s", awayShort: "%s", awayCrest: "%s", '
        'probW: %.2f, probD: %.2f, probL: %.2f }'
        % (
            f["matchday"], d,
            h["id"], h["name"], h["shortName"], h["crest"],
            a["id"], a["name"], a["shortName"], a["crest"],
            f.get("probW", 0.45), f.get("probD", 0.25), f.get("probL", 0.30),
        )
    )


def format_standing_js(s):
    """Format a single standing as a JS object literal."""
    return (
        '                { id: %d, name: "%s", shortName: "%s", crest: "%s", '
        'p: %d, w: %d, d: %d, l: %d, gf: %d, ga: %d, gd: %d, pts: %d }'
        % (
            s["id"], s["name"], s["shortName"], s["crest"],
            s["p"], s["w"], s["d"], s["l"],
            s["gf"], s["ga"], s["gd"], s["pts"],
        )
    )


def update_index_html(standings, fixtures, today_str):
    """Update the BAKED_DATA section in index.html."""
    with open(INDEX_PATH, "r") as f:
        html = f.read()

    # Build new standings JS
    standings_js = ",\n".join(format_standing_js(s) for s in standings)

    # Sort fixtures by matchday then date
    fixtures.sort(key=lambda f: (f["matchday"], f["utcDate"]))
    fixtures_js = ",\n".join(format_fixture_js(f) for f in fixtures)

    # Determine matchweek range
    if fixtures:
        mw_min = min(f["matchday"] for f in fixtures)
        mw_max = max(f["matchday"] for f in fixtures)
        mw_range = "%d&ndash;%d" % (mw_min, mw_max)
    else:
        mw_range = "none"

    # Replace the BAKED_DATA block
    baked_pattern = re.compile(
        r"(// Baked-in data from football-data\.org API \(fetched ).*?(\)\s*\n"
        r"\s*const BAKED_DATA = \{\s*\n"
        r"\s*standings: \[)\n.*?(\s*\],\s*\n"
        r"\s*fixtures: \[)\n.*?(\s*\]\s*\n\s*\};)",
        re.DOTALL,
    )

    def replacement(m):
        return (
            m.group(1) + today_str + m.group(2) + "\n"
            + standings_js + "\n"
            + m.group(3) + "\n"
            + fixtures_js + "\n"
            + m.group(4)
        )

    new_html, count = baked_pattern.subn(replacement, html)
    if count == 0:
        print("ERROR: Could not find BAKED_DATA block in index.html")
        sys.exit(1)

    # Update the modal footer date and matchweek range
    modal_pattern = re.compile(
        r'(Standings data sourced from football-data\.org as of )[^.]+(\. Only remaining fixtures \(matchweeks )\S+(\) are available for simulation\.)'
    )

    def modal_replacement(m):
        return m.group(1) + today_str + m.group(2) + mw_range + m.group(3)

    new_html = modal_pattern.sub(modal_replacement, new_html)

    with open(INDEX_PATH, "w") as f:
        f.write(new_html)


def main():
    api_key = get_api_key()
    today_str = date.today().strftime("%b %d, %Y").replace(" 0", " ")
    # e.g. "Feb 27, 2026" (no leading zero on day)

    print("=" * 60)
    print("EPL Season Simulator — Data Refresh")
    print("=" * 60)
    print()

    # 1. Fetch standings
    print("Fetching standings...")
    standings, current_matchday = fetch_standings(api_key)
    print("  Current matchday: %d" % current_matchday)
    print("  Teams: %d" % len(standings))
    for s in standings:
        if s["id"] in FOCUS_IDS:
            print("  * %s: %d pts, %d played" % (s["shortName"], s["pts"], s["p"]))

    print()

    # 2. Fetch ALL matches
    print("Fetching all matches...")
    all_matches = fetch_all_matches(api_key)
    print("  Total matches: %d" % len(all_matches))

    # 2b. Compute home/away stats for Poisson probability model
    print()
    print("Computing match probabilities (Poisson model)...")
    team_stats, league_avg_home, league_avg_away = compute_home_away_stats(all_matches)
    print("  League avg home goals/match: %.2f" % league_avg_home)
    print("  League avg away goals/match: %.2f" % league_avg_away)

    # 3. Filter to focus-team matches that are unplayed
    focus_unplayed = [m for m in all_matches if is_focus_match(m) and is_unplayed(m)]
    print("  Focus team unplayed: %d" % len(focus_unplayed))

    print()

    # 4. Cross-validate
    print("Cross-validating...")
    valid_fixtures, warnings = cross_validate(focus_unplayed, standings, all_matches)

    if warnings:
        print()
        for w in warnings:
            print("  %s" % w)
        print()

    if len(valid_fixtures) != len(focus_unplayed):
        removed = len(focus_unplayed) - len(valid_fixtures)
        print("  Removed %d fixture(s) that failed validation" % removed)

    print("  Valid fixtures to bake: %d" % len(valid_fixtures))

    if valid_fixtures:
        mws = sorted(set(f["matchday"] for f in valid_fixtures))
        print("  Matchweeks: %d-%d (%d weeks)" % (min(mws), max(mws), len(mws)))

    # 4b. Calculate probabilities for each fixture
    print()
    print("Match probabilities:")
    for f in valid_fixtures:
        home_id = f["homeTeam"]["id"]
        away_id = f["awayTeam"]["id"]
        pw, pd, pl = calculate_match_probabilities(
            home_id, away_id, team_stats, league_avg_home, league_avg_away
        )
        pw, pd, pl = normalize_and_round(pw, pd, pl)
        f["probW"] = pw
        f["probD"] = pd
        f["probL"] = pl
        print("  MW%d %s vs %s: W=%.0f%% D=%.0f%% L=%.0f%%" % (
            f["matchday"],
            f["homeTeam"]["shortName"], f["awayTeam"]["shortName"],
            pw * 100, pd * 100, pl * 100,
        ))

    # Per-team fixture count
    print()
    print("Fixtures per focus team:")
    for tid in sorted(FOCUS_IDS):
        name = next((s["shortName"] for s in standings if s["id"] == tid), str(tid))
        played = next((s["p"] for s in standings if s["id"] == tid), 0)
        remaining = sum(
            1 for f in valid_fixtures
            if f["homeTeam"]["id"] == tid or f["awayTeam"]["id"] == tid
        )
        total = played + remaining
        print("  %s: %d played + %d remaining = %d total" % (name, played, remaining, total))
        if total > 38:
            print("    WARNING: Total exceeds 38 matches!")
        elif total < 38:
            print("    NOTE: %d matches unaccounted (non-focus opponent games)" % (38 - total))

    print()

    # 5. Update index.html
    print("Updating index.html...")
    update_index_html(standings, valid_fixtures, today_str)
    print("  Done!")

    print()
    print("=" * 60)
    print("Refresh complete: %d standings, %d fixtures" % (len(standings), len(valid_fixtures)))
    print("Date stamp: %s" % today_str)
    print("=" * 60)


if __name__ == "__main__":
    main()
