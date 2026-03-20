"""
Pickleball doubles scheduler with individual rankings.
- Generate weekly schedule from a list of players (rotate partners, limit repeats).
- Show win probability per match from combined rankings.
- Update rankings after each match (Elo-style).
"""

import json
import argparse
import sys
import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from itertools import combinations
from collections import defaultdict


def round_half_up(x, decimals=0):
    """Round to decimals place; 0.5 rounds up, 0.4 rounds down."""
    d = Decimal(str(x))
    return float(d.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP))

DEFAULT_RATING = 1300
K_FACTOR = 32
_DATA_DIR = os.environ.get("PICKLEBALL_DATA_DIR")
_BASE = Path(_DATA_DIR) if _DATA_DIR else Path(__file__).resolve().parent
RANKINGS_FILE = _BASE / "rankings.json"
HISTORY_FILE = _BASE / "play_history.json"
PUBLISHED_SCHEDULE_FILE = _BASE / "published_schedule.json"
DRAFT_SCHEDULE_FILE = _BASE / "draft_schedule.json"
DROP_IN_SCHEDULE_FILE = _BASE / "drop_in_schedule.json"


def load_rankings():
    if not RANKINGS_FILE.exists():
        return {}
    with open(RANKINGS_FILE) as f:
        return json.load(f)


def save_rankings(rankings):
    with open(RANKINGS_FILE, "w") as f:
        json.dump(rankings, f, indent=2)


def load_history():
    if not HISTORY_FILE.exists():
        return {"with": defaultdict(int), "against": defaultdict(int)}
    with open(HISTORY_FILE) as f:
        data = json.load(f)
    with_final = defaultdict(int)
    for k, v in data.get("with", {}).items():
        with_final[pair_key_from_str(k)] = v
    against = defaultdict(lambda: defaultdict(int))
    for k, v in data.get("against", {}).items():
        against[k].update(v)
    return {"with": with_final, "against": against}


def save_history(history):
    data = {
        "with": {pair_key_to_str(k): v for k, v in history["with"].items()},
        "against": {k: dict(v) for k, v in history["against"].items()},
    }
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def pair_key(p1, p2):
    return tuple(sorted([p1, p2]))


def pair_key_to_str(k):
    return "|".join(k) if isinstance(k, tuple) else k


def pair_key_from_str(s):
    return tuple(s.split("|")) if isinstance(s, str) else s


def expected_score(rating_a, rating_b):
    """Expected score (0-1) for team A vs team B using combined Elo."""
    q_a = 10 ** (rating_a / 400)
    q_b = 10 ** (rating_b / 400)
    return q_a / (q_a + q_b)


def win_probability(team_a_ratings, team_b_ratings):
    """Probability that team A wins. Teams are (r1, r2)."""
    r_a = sum(team_a_ratings)
    r_b = sum(team_b_ratings)
    return expected_score(r_a, r_b)


def adjust_shares_for_friendly_rules(winner, t1, t2, s1, s2, e1, e2):
    """
    Apply the same friendly-rule clamps as rating updates (score-based matches).
    winner: 1 if team1 won, 2 if team2 won.
    t1, t2: point totals; s1, s2: raw shares; e1, e2: expected shares for team1/team2.
    """
    # Friendly rule: if you win and hold opponent to 5 or fewer points, don't lose rating
    if winner == 1 and t2 is not None and t2 <= 5 and s1 < e1:
        s1 = e1
    elif winner == 2 and t1 is not None and t1 <= 5 and s2 < e2:
        s2 = e2
    # Friendly rule: if you lose and scored 5 or fewer points, don't gain rating
    if winner == 2 and t1 is not None and t1 <= 5 and s1 > e1:
        s1 = e1
    elif winner == 1 and t2 is not None and t2 <= 5 and s2 > e2:
        s2 = e2
    return s1, s2


def apply_match_to_ratings_in_place(ratings, team1, team2, winner, score_team1=None, score_team2=None):
    """
    Mutate ratings dict with the same Elo/score-share + friendly rules as live updates.
    New players in the match get DEFAULT_RATING before the update.
    """
    for p in team1 + team2:
        if p not in ratings:
            ratings[p] = DEFAULT_RATING
    r1 = sum(ratings[p] for p in team1)
    r2 = sum(ratings[p] for p in team2)
    total = 0
    t1, t2 = None, None
    if score_team1 is not None and score_team2 is not None:
        try:
            t1, t2 = int(score_team1), int(score_team2)
            if t1 >= 0 and t2 >= 0 and (t1 + t2) > 0:
                total = t1 + t2
        except (TypeError, ValueError):
            pass
    if total > 0:
        s1 = t1 / total
        s2 = t2 / total
        e1 = expected_score(r1, r2)
        e2 = 1 - e1
        s1, s2 = adjust_shares_for_friendly_rules(winner, t1, t2, s1, s2, e1, e2)
    else:
        s1 = 1 if winner == 1 else 0
        s2 = 1 if winner == 2 else 0
    e1 = expected_score(r1, r2)
    e2 = 1 - e1
    k = K_FACTOR
    delta1 = k * (s1 - e1) / 2
    delta2 = k * (s2 - e2) / 2
    for p in team1:
        ratings[p] = round_half_up(ratings[p] + delta1, 0)
    for p in team2:
        ratings[p] = round_half_up(ratings[p] + delta2, 0)


def update_rankings_for_match(rankings, team1, team2, winner, score_team1=None, score_team2=None):
    """
    winner: 1 if team1 won, 2 if team2 won.
    Update all four players' ratings (Elo, K=32).
    When score_team1 and score_team2 are provided, uses score-share for actual result
    (so margin of victory affects rating change). Otherwise uses binary win/loss.
    """
    ratings = load_rankings()
    apply_match_to_ratings_in_place(ratings, team1, team2, winner, score_team1, score_team2)
    save_rankings(ratings)
    return ratings


def generate_schedule(players, games_per_round=None, max_with=2, max_against=2, num_courts=None):
    """
    players: list of player names (4 or more; odd allowed — byes used as needed).
    games_per_round: number of games to generate (default: enough so each player plays every round).
    max_with: max times same partner in this schedule.
    max_against: max times same opponent in this schedule.
    num_courts: if set, no player appears on more than one court per round (each round has num_courts games).
    Returns list of (team1, team2) where each team is (p1, p2).
    With 2 courts, max 8 players per round; extra players get bye for that game.
    """
    n = len(players)
    if n < 4:
        raise ValueError("Need at least 4 players.")
    rankings = load_rankings()
    for p in players:
        if p not in rankings:
            rankings[p] = DEFAULT_RATING

    # Default: enough games so each player plays multiple times (e.g. 8 players -> 8 games)
    if games_per_round is None:
        games_per_round = max(4, n)

    scheduled = []
    used_opponent = defaultdict(lambda: defaultdict(int))
    games_played = defaultdict(int)
    bye_count = defaultdict(int)
    partner_count_this = defaultdict(int)

    def score_pairing(team1, team2):
        p1, p2 = team1
        p3, p4 = team2
        k_with = pair_key(p1, p2)
        k_with2 = pair_key(p3, p4)
        penalty = 0
        # Prefer even partner usage in this schedule: use pairs that have partnered fewer times
        penalty += partner_count_this.get(k_with, 0) * 120
        penalty += partner_count_this.get(k_with2, 0) * 120
        for a in team1:
            for b in team2:
                penalty += used_opponent[a][b] * 50
        target = len(scheduled) * 4 // n + 1
        for p in team1 + team2:
            penalty += max(0, games_played[p] - target) * 80
        # Prefer closest-to-50% matches (avoid best 2 vs worst 2)
        r1 = [rankings.get(p, DEFAULT_RATING) for p in team1]
        r2 = [rankings.get(p, DEFAULT_RATING) for p in team2]
        prob = win_probability(r1, r2)
        penalty += abs(prob - 0.5) * 200
        # Prefer giving bye to players who have had fewer byes
        playing = set(team1) | set(team2)
        bye_players = [p for p in players if p not in playing]
        for p in bye_players:
            penalty += bye_count[p] * 60
        return penalty

    def add_pairing(team1, team2):
        p1, p2 = team1
        p3, p4 = team2
        scheduled.append((team1, team2))
        partner_count_this[pair_key(p1, p2)] += 1
        partner_count_this[pair_key(p3, p4)] += 1
        for a in team1:
            for b in team2:
                used_opponent[a][b] += 1
        for p in team1 + team2:
            games_played[p] += 1
        playing = set(team1) | set(team2)
        for p in players:
            if p not in playing:
                bye_count[p] += 1

    available = list(players)
    from random import shuffle
    shuffle(available)

    # Build all possible 2-player teams from available
    teams = list(combinations(available, 2))
    # Build games: two disjoint teams (no shared player). If num_courts set, no player plays twice in same round.
    for _ in range(games_per_round * 15):
        if len(scheduled) >= games_per_round:
            break
        round_size = num_courts if (num_courts and num_courts >= 1) else games_per_round
        round_start = (len(scheduled) // round_size) * round_size if round_size else 0
        players_in_this_round = set()
        for (t1, t2) in scheduled[round_start:]:
            players_in_this_round.update(t1)
            players_in_this_round.update(t2)
        best = None
        best_penalty = 1e9
        shuffle(teams)
        for t1 in teams:
            for t2 in teams:
                if t1 >= t2:
                    continue
                if set(t1) & set(t2):
                    continue
                if round_size and (set(t1) | set(t2)) & players_in_this_round:
                    continue  # player already in this round — can only play one court per round
                p = score_pairing(t1, t2)
                if p < best_penalty:
                    best_penalty = p
                    best = (t1, t2)
        if best is None:
            break
        t1, t2 = best
        add_pairing(t1, t2)

    return scheduled, rankings


def format_schedule(schedule, rankings, players=None):
    """Format schedule as lines. If players is given, append bye for each game when applicable."""
    lines = []
    for i, (team1, team2) in enumerate(schedule, 1):
        r1 = [rankings.get(p, DEFAULT_RATING) for p in team1]
        r2 = [rankings.get(p, DEFAULT_RATING) for p in team2]
        prob = win_probability(r1, r2)
        line = (
            f"Game {i}: {team1[0]} & {team1[1]} vs {team2[0]} & {team2[1]}  "
            f"(Team 1 win chance: {prob:.0%})"
        )
        if players is not None:
            playing = set(team1) | set(team2)
            bye = sorted(set(players) - playing)
            if bye:
                line += f"  — Bye: {', '.join(bye)}"
        lines.append(line)
    return lines


def main():
    parser = argparse.ArgumentParser(
        description="Pickleball doubles scheduler with rankings"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Schedule: provide list of players
    p_schedule = sub.add_parser("schedule", help="Generate weekly schedule")
    p_schedule.add_argument(
        "players",
        nargs="+",
        help="Space-separated list of players (4+; odd allowed, byes used)",
    )
    p_schedule.add_argument(
        "--games",
        type=int,
        default=None,
        help="Max number of games to generate (default: auto)",
    )

    # Results: provide match outcomes to update rankings
    p_results = sub.add_parser("results", help="Record match results and update rankings")
    p_results.add_argument(
        "--game",
        required=True,
        metavar="P1,P2,P3,P4",
        help="Game: Team1 P1 P2 vs Team2 P3 P4 (comma-separated)",
    )
    p_results.add_argument(
        "--winner",
        type=int,
        choices=[1, 2],
        required=True,
        help="Winner: 1 = first pair (P1,P2), 2 = second pair (P3,P4)",
    )

    # Show rankings
    p_rank = sub.add_parser("rankings", help="Show current rankings")

    p_batch = sub.add_parser("batch-results", help="Record multiple results (each line: P1,P2,P3,P4,1or2)")
    p_batch.add_argument("results", nargs="*", help="Lines of P1,P2,P3,P4,winner")
    p_batch.add_argument("--file", "-f", type=argparse.FileType("r"), help="Read from file")

    p_reset = sub.add_parser("reset-history", help="Reset partner/opponent history only")

    args = parser.parse_args()

    if args.command == "schedule":
        try:
            schedule, rankings = generate_schedule(args.players, games_per_round=args.games)
        except ValueError as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        for line in format_schedule(schedule, rankings, players=args.players):
            print(line)
        print()
        print("Current rankings (for reference):")
        for p in sorted(rankings.keys(), key=lambda x: -rankings[x]):
            if p in args.players:
                print(f"  {p}: {rankings[p]}")

    elif args.command == "results":
        parts = [x.strip() for x in args.game.split(",")]
        if len(parts) != 4:
            print("--game must be exactly 4 names: P1,P2,P3,P4", file=sys.stderr)
            sys.exit(1)
        team1 = (parts[0], parts[1])
        team2 = (parts[2], parts[3])
        update_rankings_for_match(None, team1, team2, args.winner)
        print("Rankings updated.")
        rankings = load_rankings()
        print("Current rankings:")
        for p in sorted(rankings.keys(), key=lambda x: -rankings[x]):
            print(f"  {p}: {rankings[p]}")

    elif args.command == "batch-results":
        lines = []
        if getattr(args, "file", None):
            lines = [line.strip() for line in args.file if line.strip()]
        if getattr(args, "results", None):
            lines.extend(args.results)
        if not lines and sys.stdin.isatty():
            print("Provide results as args or --file (P1,P2,P3,P4,1or2)", file=sys.stderr)
            sys.exit(1)
        if not lines and not sys.stdin.isatty():
            lines = [line.strip() for line in sys.stdin if line.strip()]
        for i, line in enumerate(lines, 1):
            parts = [x.strip() for x in line.split(",")]
            if len(parts) != 5:
                print(f"Line {i}: need P1,P2,P3,P4,winner", file=sys.stderr)
                continue
            try:
                winner = int(parts[4])
            except ValueError:
                continue
            if winner not in (1, 2):
                continue
            team1 = (parts[0], parts[1])
            team2 = (parts[2], parts[3])
            update_rankings_for_match(None, team1, team2, winner)
        print(f"Updated {len(lines)} match(es).")
        rankings = load_rankings()
        for p in sorted(rankings.keys(), key=lambda x: -rankings[x]):
            print(f"  {p}: {rankings[p]}")

    elif args.command == "rankings":
        rankings = load_rankings()
        if not rankings:
            print("No rankings yet. Play some games or generate a schedule first.")
            return
        for p in sorted(rankings.keys(), key=lambda x: -rankings[x]):
            print(f"  {p}: {rankings[p]}")

    elif args.command == "reset-history":
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
        print("Play history reset. Rankings unchanged.")


if __name__ == "__main__":
    main()
