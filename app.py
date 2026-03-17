"""
Flask web app for the pickleball doubles scheduler.
Run with: python app.py  or  flask --app app run
"""

import json
import hmac
import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from scheduler import (
    generate_schedule,
    load_rankings,
    save_rankings,
    update_rankings_for_match,
    format_schedule,
    DEFAULT_RATING,
    win_probability,
    HISTORY_FILE as SCHEDULER_HISTORY_FILE,
    PUBLISHED_SCHEDULE_FILE,
    DRAFT_SCHEDULE_FILE,
)

app = Flask(__name__)
app.secret_key = "pickleball-scheduler-secret-change-in-production"

# Data directory: use PICKLEBALL_DATA_DIR if set (persists across code deploys), else same folder as app
_data_dir_raw = os.environ.get("PICKLEBALL_DATA_DIR")
_DATA_DIR = Path(_data_dir_raw) if _data_dir_raw else Path(__file__).resolve().parent
if _data_dir_raw:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

PLAYERS_FILE = _DATA_DIR / "players.json"
PLAYER_BIOS_FILE = _DATA_DIR / "player_bios.json"
MATCH_HISTORY_FILE = _DATA_DIR / "match_history.json"
AVAILABILITY_FILE = _DATA_DIR / "availability.json"
PLAYERS_PASSWORD = "PBPlayers26"
SCHEDULE_PASSWORD = "PBGames26"
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")


def verify_slack_signature(body_bytes, timestamp, signature_header):
    if not SLACK_SIGNING_SECRET:
        return True
    if not signature_header or not body_bytes or not timestamp:
        return False
    if not signature_header.startswith("v0="):
        return False
    sig_basestring = f"v0:{timestamp}:".encode() + body_bytes
    expected = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), sig_basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def load_players_list():
    """Load the master player list. If missing or empty, return names from rankings."""
    if not PLAYERS_FILE.exists():
        rankings = load_rankings()
        return sorted(rankings.keys()) if rankings else []
    try:
        with open(PLAYERS_FILE) as f:
            data = json.load(f)
        return data.get("players", []) or []
    except (json.JSONDecodeError, OSError):
        return []


def save_players_list(players):
    """Save the master player list (sorted, unique)."""
    unique = list(dict.fromkeys(p.strip() for p in players if p and str(p).strip()))
    with open(PLAYERS_FILE, "w") as f:
        json.dump({"players": unique}, f, indent=2)


def load_player_bios():
    """Load player name -> bio text. Returns dict."""
    if not PLAYER_BIOS_FILE.exists():
        return {}
    try:
        with open(PLAYER_BIOS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_player_bios(bios):
    """Save player bios dict."""
    with open(PLAYER_BIOS_FILE, "w") as f:
        json.dump(bios, f, indent=2)


def load_match_history():
    """Load past match results (newest first)."""
    if not MATCH_HISTORY_FILE.exists():
        return []
    try:
        with open(MATCH_HISTORY_FILE) as f:
            data = json.load(f)
        matches = data.get("matches", [])
        return list(reversed(matches))
    except (json.JSONDecodeError, OSError):
        return []


def save_match_history(matches_newest_first):
    """Save match history. matches_newest_first is list in display order (newest first)."""
    stored = list(reversed(matches_newest_first))
    with open(MATCH_HISTORY_FILE, "w") as f:
        json.dump({"matches": stored}, f, indent=2)


def get_wins_losses_by_player():
    """Return dict of player -> {'wins': int, 'losses': int} from match history."""
    matches = load_match_history()
    wins = {}
    losses = {}
    for m in matches:
        team1 = m.get("team1") or []
        team2 = m.get("team2") or []
        winner = m.get("winner")
        if winner == 1:
            winning_team, losing_team = team1, team2
        elif winner == 2:
            winning_team, losing_team = team2, team1
        else:
            continue
        for p in winning_team:
            if p:
                wins[p] = wins.get(p, 0) + 1
        for p in losing_team:
            if p:
                losses[p] = losses.get(p, 0) + 1
    return {p: {"wins": wins.get(p, 0), "losses": losses.get(p, 0)} for p in set(wins) | set(losses)}


def append_match(team1, team2, winner, score_team1=None, score_team2=None, prob_team1=None):
    """Append one match to history. prob_team1 is Team 1 win probability before the match (for upset detection)."""
    matches = []
    if MATCH_HISTORY_FILE.exists():
        try:
            with open(MATCH_HISTORY_FILE) as f:
                data = json.load(f)
            matches = data.get("matches", [])
        except (json.JSONDecodeError, OSError):
            pass
    record = {
        "team1": list(team1),
        "team2": list(team2),
        "winner": winner,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    if score_team1 is not None and score_team2 is not None:
        record["score_team1"] = score_team1
        record["score_team2"] = score_team2
    if prob_team1 is not None:
        record["prob_team1"] = round(prob_team1, 4)
    matches.append(record)
    with open(MATCH_HISTORY_FILE, "w") as f:
        json.dump({"matches": matches}, f, indent=2)


def get_next_wednesday():
    """Return the next upcoming Wednesday (or today if today is Wednesday)."""
    today = date.today()
    days_ahead = (2 - today.weekday() + 7) % 7
    if days_ahead == 0:
        return today
    return today + timedelta(days=days_ahead)


def _ordinal_day(d):
    """Return day with ordinal suffix, e.g. 18 -> '18th', 1 -> '1st'."""
    n = d.day
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return str(n) + suf


def load_availability():
    """Load availability by date: { 'YYYY-MM-DD': { 'PlayerName': 'in'|'out' } }."""
    if not AVAILABILITY_FILE.exists():
        return {}
    try:
        with open(AVAILABILITY_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_availability(by_date):
    with open(AVAILABILITY_FILE, "w") as f:
        json.dump(by_date, f, indent=2)


def load_published_schedule():
    """Load the published schedule for this week; return None if missing or not this week."""
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    if not PUBLISHED_SCHEDULE_FILE.exists():
        return None
    try:
        with open(PUBLISHED_SCHEDULE_FILE) as f:
            data = json.load(f)
        if data.get("date_key") != date_key:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_published_schedule(date_key, next_wednesday_display, players, schedule_entries, rankings, time_location="", num_courts=2):
    """Save the generated schedule so the Schedule tab can show it (public view)."""
    data = {
        "date_key": date_key,
        "next_wednesday_display": next_wednesday_display,
        "players": list(players),
        "schedule_entries": schedule_entries,
        "rankings": dict(rankings),
        "time_location": (time_location or "").strip(),
        "num_courts": num_courts,
    }
    with open(PUBLISHED_SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_draft_schedule():
    """Load draft schedule for this week; return None if missing or not this week."""
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    if not DRAFT_SCHEDULE_FILE.exists():
        return None
    try:
        with open(DRAFT_SCHEDULE_FILE) as f:
            data = json.load(f)
        if data.get("date_key") != date_key:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_draft_schedule(date_key, next_wednesday_display, players, schedule_entries, rankings, time_location="", num_courts=2, rotate_partners=True, pairs=None):
    """Save draft schedule (preview on Generate tab until Publish)."""
    data = {
        "date_key": date_key,
        "next_wednesday_display": next_wednesday_display,
        "players": list(players),
        "schedule_entries": schedule_entries,
        "rankings": dict(rankings),
        "time_location": (time_location or "").strip(),
        "num_courts": num_courts,
        "rotate_partners": rotate_partners,
        "pairs": list(pairs) if pairs else None,
    }
    with open(DRAFT_SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def clear_draft_schedule():
    """Remove draft schedule file."""
    if DRAFT_SCHEDULE_FILE.exists():
        DRAFT_SCHEDULE_FILE.unlink()


def _parse_draft_entries_from_form(draft):
    """
    Build schedule_entries from request form (team1_0_i, team1_1_i, team2_0_i, team2_1_i).
    Recompute prob and bye from draft players and rankings.
    Returns (schedule_entries, None) or (None, error_message).
    """
    players = draft["players"]
    rankings = draft["rankings"]
    entries = draft["schedule_entries"]
    result = []
    for i, e in enumerate(entries):
        t1_0 = (request.form.get(f"team1_0_{i}") or "").strip()
        t1_1 = (request.form.get(f"team1_1_{i}") or "").strip()
        t2_0 = (request.form.get(f"team2_0_{i}") or "").strip()
        t2_1 = (request.form.get(f"team2_1_{i}") or "").strip()
        if not all([t1_0, t1_1, t2_0, t2_1]):
            return None, f"Match {i + 1}: all four players must be selected."
        four = {t1_0, t1_1, t2_0, t2_1}
        if len(four) != 4:
            return None, f"Match {i + 1}: four different players required (no duplicates)."
        for p in four:
            if p not in players:
                return None, f"Match {i + 1}: '{p}' is not in this week's player list."
        team1 = [t1_0, t1_1]
        team2 = [t2_0, t2_1]
        r1 = [rankings.get(p, DEFAULT_RATING) for p in team1]
        r2 = [rankings.get(p, DEFAULT_RATING) for p in team2]
        prob = win_probability(r1, r2)
        playing = set(team1) | set(team2)
        bye = sorted(set(players) - playing)
        result.append({
            "game": i + 1,
            "team1": team1,
            "team2": team2,
            "line": e.get("line", ""),
            "prob": prob,
            "bye": bye,
        })
    return result, None


def add_round_court_and_bye(schedule_entries, players, num_courts=None):
    """
    Add round, court (A/B/C/D), and round_bye to each entry for display.
    num_courts: if set, that many games per round (one per court). Else: 1 court if <8 players, 2 courts if 8+.
    """
    from collections import defaultdict
    n = len(players)
    if num_courts is not None and num_courts >= 1:
        round_size = int(num_courts)
    else:
        round_size = 1 if n < 8 else 2
    for i, e in enumerate(schedule_entries):
        e["round"] = (i // round_size) + 1
        e["court"] = chr(65 + (i % round_size))  # A, B, C, D, ...
    by_round = defaultdict(list)
    for e in schedule_entries:
        by_round[e["round"]].append(e)
    for entries in by_round.values():
        all_bye = set()
        for e in entries:
            all_bye.update(e.get("bye", []))
        if entries:
            entries[-1]["round_bye"] = sorted(all_bye)
    return schedule_entries


def schedule_review_stats(schedule_entries, players):
    """
    Return stats for the Review modal: bye count per player, partner (with) counts, opponent (against) counts.
    """
    from collections import defaultdict
    bye_count = {p: 0 for p in players}
    with_count = defaultdict(lambda: defaultdict(int))
    against_count = defaultdict(lambda: defaultdict(int))
    for e in schedule_entries:
        team1 = e.get("team1") or []
        team2 = e.get("team2") or []
        for p in e.get("bye") or []:
            if p in bye_count:
                bye_count[p] += 1
        for i, p in enumerate(team1):
            for q in team1[i + 1:]:
                with_count[p][q] += 1
                with_count[q][p] += 1
        for i, p in enumerate(team2):
            for q in team2[i + 1:]:
                with_count[p][q] += 1
                with_count[q][p] += 1
        for p in team1:
            for q in team2:
                against_count[p][q] += 1
                against_count[q][p] += 1
    with_pairs = []
    seen_with = set()
    for p in players:
        for q, n in with_count.get(p, {}).items():
            if n and (p, q) not in seen_with and (q, p) not in seen_with:
                seen_with.add((p, q))
                with_pairs.append((p, q, n))
    against_pairs = []
    seen_against = set()
    for p in players:
        for q, n in against_count.get(p, {}).items():
            if n and (p, q) not in seen_against and (q, p) not in seen_against:
                seen_against.add((p, q))
                against_pairs.append((p, q, n))
    return {"bye_count": bye_count, "with_pairs": with_pairs, "against_pairs": against_pairs}


def generate_schedule_fixed_pairs(pairs, num_courts, num_rounds):
    """
    Build a schedule with fixed partner pairs. Each round fills num_courts games (2*num_courts pairs).
    pairs: list of (p1, p2) or [p1, p2]; at least 2*num_courts pairs required.
    Returns list of (team1, team2) where each team is a tuple of 2 players, in round order.
    """
    if len(pairs) < 2 * num_courts:
        raise ValueError(f"Need at least {2 * num_courts} pairs for {num_courts} court(s). You have {len(pairs)}.")
    # Normalize to list of tuples
    pair_tuples = [tuple(p) if isinstance(p, (list, tuple)) else (p[0], p[1]) for p in pairs]
    scheduled = []
    pairs_per_round = 2 * num_courts
    for r in range(num_rounds):
        start = (r * pairs_per_round) % len(pair_tuples)
        playing_indices = [(start + k) % len(pair_tuples) for k in range(pairs_per_round)]
        playing_pairs = [pair_tuples[i] for i in playing_indices]
        for g in range(num_courts):
            t1 = playing_pairs[2 * g]
            t2 = playing_pairs[2 * g + 1]
            scheduled.append((t1, t2))
    return scheduled


@app.route("/")
def index():
    rankings = load_rankings()
    next_wed = get_next_wednesday()
    next_game_date_display = next_wed.strftime("%A, %B ") + _ordinal_day(next_wed)
    published = load_published_schedule()
    next_game_location_time = ((published or {}).get("time_location") or "").strip() or "Green Lake, 6:30pm"
    return render_template(
        "index.html",
        rankings=rankings,
        next_game_date_display=next_game_date_display,
        next_game_location_time=next_game_location_time,
    )


@app.route("/slack/command", methods=["POST"])
def slack_command():
    body = request.get_data(cache=True)
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(body, timestamp, signature):
        return jsonify({"text": "Invalid request signature."}), 401
    command = request.form.get("command", "")
    text = request.form.get("text", "")
    from slack_handlers import handle_slack_command
    response = handle_slack_command(
        command=command,
        text=text,
        player_list=load_players_list(),
        load_rankings=load_rankings,
        load_match_history=load_match_history,
        load_availability=load_availability,
        save_availability=save_availability,
        get_next_wednesday=get_next_wednesday,
        load_published_schedule=load_published_schedule,
        add_round_court_and_bye=add_round_court_and_bye,
    )
    return jsonify(response)


@app.route("/schedule", methods=["GET"])
def schedule():
    rankings = load_rankings()
    player_list = load_players_list()
    next_wed = get_next_wednesday()
    next_wed_str = next_wed.strftime("%A, %B %d, %Y")
    published = load_published_schedule()
    schedule_rating_data = []
    schedule_difficulty = []
    if published:
        add_round_court_and_bye(published["schedule_entries"], published["players"], published.get("num_courts"))
        for e in published["schedule_entries"]:
            prob = e.get("prob", 0.5)
            if prob is None:
                prob = 0.5
            prob = float(prob)
            schedule_rating_data.append({
                "round": e.get("round", 1),
                "court": e.get("court", "A"),
                "team1": list(e.get("team1", [])),
                "team2": list(e.get("team2", [])),
                "team1_gain": min_score_rating_gain(prob),
                "team2_gain": min_score_rating_gain(1 - prob),
            })
        # Per-player average win chance (lower = harder schedule). Sort hardest to easiest.
        player_win_probs = {p: [] for p in published["players"]}
        for e in published["schedule_entries"]:
            prob = e.get("prob")
            if prob is None:
                prob = 0.5
            prob = float(prob)
            for p in e.get("team1", []):
                if p in player_win_probs:
                    player_win_probs[p].append(prob)
            for p in e.get("team2", []):
                if p in player_win_probs:
                    player_win_probs[p].append(1 - prob)
        for p in published["players"]:
            probs = player_win_probs.get(p, [])
            avg = sum(probs) / len(probs) * 100 if probs else 50.0
            schedule_difficulty.append((p, round(avg)))
        schedule_difficulty.sort(key=lambda x: x[1])  # ascending: hardest (lowest %) first
    return render_template(
        "schedule.html",
        rankings=rankings,
        player_list=player_list,
        next_wednesday=next_wed_str,
        published_schedule=published,
        schedule_rating_data=schedule_rating_data,
        schedule_difficulty=schedule_difficulty,
    )


@app.route("/availability", methods=["GET", "POST"])
def availability():
    """Availability for next Wednesday. List all players with In/Out/Not Answered. Locked after schedule is generated unless password entered."""
    player_list = load_players_list()
    next_wed = get_next_wednesday()
    next_wed_str = next_wed.strftime("%A, %B %d, %Y")
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    availability = availability_all.get(date_key, {})
    published = load_published_schedule()
    unlocked_week = session.get("availability_edit_unlocked_week")
    read_only = bool(published) and (unlocked_week != date_key)

    if request.method == "POST" and not read_only:
        # Bulk save: one dropdown per player
        for p in player_list:
            val = request.form.get(f"avail_{p}", "not_answered").strip().lower()
            if val not in ("in", "out", "not_answered"):
                val = "not_answered"
            if date_key not in availability_all:
                availability_all[date_key] = {}
            availability_all[date_key][p] = val
        save_availability(availability_all)
        flash("Availability saved.", "success")
        return redirect(url_for("availability"))

    # Normalize for display: missing => not_answered
    availability_display = {p: availability.get(p) or "not_answered" for p in player_list} if player_list else {}
    return render_template(
        "availability.html",
        player_list=player_list,
        next_wednesday=next_wed_str,
        date_key=date_key,
        availability=availability_display,
        read_only=read_only,
    )


@app.route("/availability/unlock", methods=["POST"])
def availability_unlock():
    """Unlock availability editing after schedule is generated (password PBGames26)."""
    date_key = get_next_wednesday().isoformat()
    if request.form.get("availability_unlock_password", "").strip() != SCHEDULE_PASSWORD:
        flash("Incorrect password. Use the schedule password to edit availability after the schedule is generated.", "error")
        return redirect(url_for("availability"))
    session["availability_edit_unlocked_week"] = date_key
    flash("You can now edit availability.", "success")
    return redirect(url_for("availability"))


@app.route("/schedule/unlock-players", methods=["POST"])
def schedule_unlock_players():
    if request.form.get("players_extra_password", "").strip() == PLAYERS_PASSWORD:
        session["schedule_players_unlocked"] = True
        flash("Additional players section unlocked.", "success")
    else:
        flash("Incorrect password.", "error")
    return redirect(url_for("generate"))


@app.route("/schedule/record-results")
def schedule_record_results():
    """Show the result-entry form for the published schedule (e.g. after generating from Slack)."""
    published = load_published_schedule()
    if not published:
        flash("No schedule published for this week. Generate a schedule first.", "error")
        return redirect(url_for("schedule"))
    add_round_court_and_bye(published["schedule_entries"], published["players"], published.get("num_courts"))
    return render_template(
        "schedule_result.html",
        schedule_entries=published["schedule_entries"],
        players=published["players"],
        rankings=published["rankings"],
    )


@app.route("/generate/login", methods=["GET", "POST"])
def generate_login():
    """Log in to access the Generate tab (schedule password)."""
    if request.method == "POST":
        if request.form.get("password") == SCHEDULE_PASSWORD:
            session["schedule_authenticated"] = True
            return redirect(url_for("generate"))
        flash("Incorrect password.", "error")
    return render_template("generate_login.html")


@app.route("/generate/logout")
def generate_logout():
    session.pop("schedule_authenticated", None)
    return redirect(url_for("index"))


@app.route("/generate", methods=["GET", "POST"])
def generate():
    """Generate schedule. Requires schedule login; saves as draft; Publish makes it live on Schedule tab."""
    if not session.get("schedule_authenticated"):
        if request.method == "GET":
            return redirect(url_for("generate_login"))
        flash("Please log in to the Generate tab first.", "error")
        return redirect(url_for("generate_login"))

    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()

    if request.method == "GET":
        player_list = load_players_list()
        schedule_players_unlocked = session.get("schedule_players_unlocked", False)
        availability_all = load_availability()
        availability = availability_all.get(date_key, {})
        players_in = [p for p in player_list if availability.get(p) == "in"]
        draft = load_draft_schedule()
        schedule_difficulty = []
        if draft:
            add_round_court_and_bye(draft["schedule_entries"], draft["players"], draft.get("num_courts"))
            player_win_probs = {p: [] for p in draft["players"]}
            for e in draft["schedule_entries"]:
                prob = e.get("prob")
                if prob is None:
                    prob = 0.5
                prob = float(prob)
                for p in e.get("team1", []):
                    if p in player_win_probs:
                        player_win_probs[p].append(prob)
                for p in e.get("team2", []):
                    if p in player_win_probs:
                        player_win_probs[p].append(1 - prob)
            for p in draft["players"]:
                probs = player_win_probs.get(p, [])
                avg = sum(probs) / len(probs) * 100 if probs else 50.0
                schedule_difficulty.append((p, round(avg)))
            schedule_difficulty.sort(key=lambda x: x[1])
            review_stats = schedule_review_stats(draft["schedule_entries"], draft["players"])
        else:
            review_stats = None
        return render_template(
            "generate.html",
            player_list=player_list,
            next_wednesday=next_wed.strftime("%A, %B %d, %Y"),
            schedule_players_unlocked=schedule_players_unlocked,
            players_in=players_in,
            draft_schedule=draft,
            schedule_difficulty=schedule_difficulty,
            review_stats=review_stats,
        )

    # POST: generate schedule (already authenticated)
    selected = request.form.getlist("selected_players")
    players_extra = request.form.get("players_extra", "").strip() if session.get("schedule_players_unlocked") else ""
    games_str = request.form.get("games", "").strip()
    time_location = request.form.get("time_location", "").strip()
    num_courts = 2
    try:
        nc = request.form.get("num_courts", "2").strip()
        if nc:
            num_courts = max(1, min(8, int(nc)))
    except ValueError:
        pass
    rotate_partners = request.form.get("rotate_partners") == "on"

    players = [p.strip() for p in selected if p and p.strip()]
    if players_extra:
        extra = [p.strip() for p in players_extra.replace(",", "\n").split() if p.strip()]
        for p in extra:
            if p and p not in players:
                players.append(p)
    players = list(dict.fromkeys(players))

    games = None
    if games_str:
        try:
            games = int(games_str)
            if games < 1:
                games = None
        except ValueError:
            pass

    try:
        if rotate_partners:
            if not players:
                flash("Select at least one player from the list, or add names below.", "error")
                return redirect(url_for("generate"))
            schedule_list, rankings = generate_schedule(players, games_per_round=games)
            lines = format_schedule(schedule_list, rankings, players=players)
            schedule_entries = []
            for i, (team1, team2) in enumerate(schedule_list, 1):
                r1 = [rankings.get(p, DEFAULT_RATING) for p in team1]
                r2 = [rankings.get(p, DEFAULT_RATING) for p in team2]
                prob = win_probability(r1, r2)
                playing = set(team1) | set(team2)
                bye = sorted(set(players) - playing)
                schedule_entries.append(
                    {
                        "game": i,
                        "team1": list(team1),
                        "team2": list(team2),
                        "line": lines[i - 1] if i <= len(lines) else "",
                        "prob": prob,
                        "bye": bye,
                    }
                )
            add_round_court_and_bye(schedule_entries, players, num_courts=num_courts)
            save_draft_schedule(
                date_key,
                next_wed.strftime("%A, %B %d, %Y"),
                players,
                schedule_entries,
                rankings,
                time_location=time_location,
                num_courts=num_courts,
                rotate_partners=True,
                pairs=None,
            )
        else:
            # Keep partners: parse pairs from form (pair_left_0, pair_right_0, ...)
            pairs = []
            for i in range(16):
                left = (request.form.get(f"pair_left_{i}") or "").strip()
                right = (request.form.get(f"pair_right_{i}") or "").strip()
                if left and right and left != right:
                    pairs.append((left, right))
            min_pairs = 2 * num_courts
            if len(pairs) < min_pairs:
                flash(f"Need at least {min_pairs} pairs for {num_courts} court(s). Define pairs below (no duplicate players within a pair).", "error")
                return redirect(url_for("generate"))
            all_in_pairs = []
            for a, b in pairs:
                all_in_pairs.append(a)
                all_in_pairs.append(b)
            if len(set(all_in_pairs)) != len(all_in_pairs):
                flash("Each player can only appear in one pair. Fix duplicate players.", "error")
                return redirect(url_for("generate"))
            num_rounds_str = request.form.get("num_rounds", "").strip()
            num_rounds = None
            if num_rounds_str:
                try:
                    num_rounds = int(num_rounds_str)
                    if num_rounds < 1:
                        num_rounds = None
                except ValueError:
                    pass
            if num_rounds is None:
                num_rounds = max(2, (len(pairs) // (2 * num_courts)) * 2)
            schedule_list = generate_schedule_fixed_pairs(pairs, num_courts, num_rounds)
            rankings = load_rankings()
            lines = format_schedule(schedule_list, rankings, players=all_in_pairs)
            schedule_entries = []
            for i, (team1, team2) in enumerate(schedule_list, 1):
                r1 = [rankings.get(p, DEFAULT_RATING) for p in team1]
                r2 = [rankings.get(p, DEFAULT_RATING) for p in team2]
                prob = win_probability(r1, r2)
                playing = set(team1) | set(team2)
                bye = sorted(set(all_in_pairs) - playing)
                schedule_entries.append(
                    {
                        "game": i,
                        "team1": list(team1),
                        "team2": list(team2),
                        "line": lines[i - 1] if i <= len(lines) else "",
                        "prob": prob,
                        "bye": bye,
                    }
                )
            add_round_court_and_bye(schedule_entries, all_in_pairs, num_courts=num_courts)
            save_draft_schedule(
                date_key,
                next_wed.strftime("%A, %B %d, %Y"),
                all_in_pairs,
                schedule_entries,
                rankings,
                time_location=time_location,
                num_courts=num_courts,
                rotate_partners=False,
                pairs=[list(p) for p in pairs],
            )
        flash("Schedule generated. Review it below and click Publish when ready to go live.", "success")
        return redirect(url_for("generate"))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("generate"))


@app.route("/generate/publish", methods=["POST"])
def generate_publish():
    """Publish the draft schedule (optionally with edits from form) so it appears on the Schedule tab."""
    if not session.get("schedule_authenticated"):
        flash("Please log in to the Generate tab first.", "error")
        return redirect(url_for("generate_login"))
    draft = load_draft_schedule()
    if not draft:
        flash("No draft schedule to publish. Generate a schedule first.", "error")
        return redirect(url_for("generate"))
    # If form has draft edits, use them; otherwise use current draft
    if request.form.get("team1_0_0") is not None:
        entries, err = _parse_draft_entries_from_form(draft)
        if err:
            flash(err, "error")
            return redirect(url_for("generate"))
        add_round_court_and_bye(entries, draft["players"], draft.get("num_courts"))
        schedule_entries = entries
    else:
        schedule_entries = draft["schedule_entries"]
    time_location = request.form.get("time_location", "").strip() or draft.get("time_location", "")
    save_published_schedule(
        draft["date_key"],
        draft["next_wednesday_display"],
        draft["players"],
        schedule_entries,
        draft["rankings"],
        time_location=time_location,
        num_courts=draft.get("num_courts", 2),
    )
    clear_draft_schedule()
    flash("Schedule is now live on the Schedule tab.", "success")
    return redirect(url_for("schedule"))


@app.route("/generate/save-draft", methods=["POST"])
def generate_save_draft():
    """Save edited draft schedule from form."""
    if not session.get("schedule_authenticated"):
        flash("Please log in to the Generate tab first.", "error")
        return redirect(url_for("generate_login"))
    draft = load_draft_schedule()
    if not draft:
        flash("No draft schedule to save. Generate a schedule first.", "error")
        return redirect(url_for("generate"))
    entries, err = _parse_draft_entries_from_form(draft)
    if err:
        flash(err, "error")
        return redirect(url_for("generate"))
    add_round_court_and_bye(entries, draft["players"], draft.get("num_courts"))
    time_location = request.form.get("time_location", "").strip() or draft.get("time_location", "")
    save_draft_schedule(
        draft["date_key"],
        draft["next_wednesday_display"],
        draft["players"],
        entries,
        draft["rankings"],
        time_location=time_location,
        num_courts=draft.get("num_courts", 2),
        rotate_partners=draft.get("rotate_partners", True),
        pairs=draft.get("pairs"),
    )
    flash("Draft saved. You can keep editing or Publish when ready.", "success")
    return redirect(url_for("generate"))


@app.route("/generate/regenerate", methods=["GET", "POST"])
def generate_regenerate():
    """Clear the draft and show the generate form again."""
    if not session.get("schedule_authenticated"):
        flash("Please log in to the Generate tab first.", "error")
        return redirect(url_for("generate_login"))
    clear_draft_schedule()
    flash("Draft cleared. Change players or options and generate again.", "success")
    return redirect(url_for("generate"))


@app.route("/schedule-results", methods=["POST"])
def schedule_results():
    """Record winners and optional scores from the schedule result page."""
    count = 0
    i = 0
    while i < 100:
        team1_0 = request.form.get(f"team1_0_{i}")
        team1_1 = request.form.get(f"team1_1_{i}")
        team2_0 = request.form.get(f"team2_0_{i}")
        team2_1 = request.form.get(f"team2_1_{i}")
        winner = request.form.get(f"winner_{i}")
        score_t1 = request.form.get(f"score_team1_{i}")
        score_t2 = request.form.get(f"score_team2_{i}")
        if not team1_0 and not team1_1:
            i += 1
            continue
        if team1_0 and team1_1 and team2_0 and team2_1 and winner and winner in ("1", "2"):
            team1 = (team1_0.strip(), team1_1.strip())
            team2 = (team2_0.strip(), team2_1.strip())
            s1, s2 = None, None
            if score_t1 not in (None, "") and score_t2 not in (None, ""):
                try:
                    s1 = int(score_t1)
                    s2 = int(score_t2)
                except ValueError:
                    pass
            rankings = load_rankings()
            r1 = tuple(rankings.get(p, DEFAULT_RATING) for p in team1)
            r2 = tuple(rankings.get(p, DEFAULT_RATING) for p in team2)
            prob_team1 = win_probability(r1, r2)
            update_rankings_for_match(None, team1, team2, int(winner), s1, s2)
            append_match(team1, team2, int(winner), s1, s2, prob_team1=prob_team1)
            count += 1
        i += 1
    if count > 0:
        flash(f"Recorded {count} match(es). Rankings and history updated.", "success")
    else:
        flash("No results to save. Select a winner for each game you played.", "error")
    return redirect(url_for("rankings"))


@app.route("/players", methods=["GET", "POST"])
def players():
    if not session.get("players_authenticated"):
        if request.method == "GET":
            return redirect(url_for("players_login"))
        flash("Please log in to manage players.", "error")
        return redirect(url_for("players_login"))

    if request.method == "GET":
        player_list = load_players_list()
        rankings = load_rankings()
        return render_template("players.html", player_list=player_list, rankings=rankings)

    # POST: add or remove player
    add_name = request.form.get("add_name", "").strip()
    remove_name = request.form.get("remove_name", "").strip()
    if add_name:
        current = load_players_list()
        if add_name not in current:
            current.append(add_name)
            save_players_list(current)
            flash(f"Added {add_name}.", "success")
        else:
            flash(f"{add_name} is already on the list.", "error")
    elif remove_name:
        current = load_players_list()
        if remove_name in current:
            current.remove(remove_name)
            save_players_list(current)
            flash(f"Removed {remove_name}.", "success")
        else:
            flash(f"{remove_name} was not on the list.", "error")
    return redirect(url_for("players"))


@app.route("/players/bios", methods=["GET", "POST"])
def players_bios():
    if not session.get("players_authenticated"):
        if request.method == "GET":
            return redirect(url_for("players_login"))
        flash("Please log in to manage players.", "error")
        return redirect(url_for("players_login"))
    player_list = load_players_list()
    bios = load_player_bios()
    if request.method == "POST":
        for p in player_list:
            key = f"bio_{p}"
            val = request.form.get(key, "").strip()
            if val:
                bios[p] = val
            elif p in bios:
                del bios[p]
        save_player_bios(bios)
        flash("Bios saved. They appear on hover on the Rankings tab.", "success")
        return redirect(url_for("players_bios"))
    return render_template(
        "players_bios.html",
        player_list=player_list,
        bios=bios,
    )


@app.route("/players/reset", methods=["POST"])
def players_reset():
    if not session.get("players_authenticated"):
        flash("Please log in to manage players.", "error")
        return redirect(url_for("players_login"))
    rankings = load_rankings()
    reset_rankings = {p: DEFAULT_RATING for p in rankings}
    save_rankings(reset_rankings)
    with open(MATCH_HISTORY_FILE, "w") as f:
        json.dump({"matches": []}, f, indent=2)
    flash("All rankings reset to 1300 and past games cleared.", "success")
    return redirect(url_for("players"))


@app.route("/players/ratings", methods=["POST"])
def players_ratings():
    if not session.get("players_authenticated"):
        flash("Please log in to manage players.", "error")
        return redirect(url_for("players_login"))
    player_list = load_players_list()
    rankings = load_rankings()
    for i in range(len(player_list)):
        p = request.form.get(f"player_{i}")
        r = request.form.get(f"rating_{i}")
        if p and p in player_list and r is not None and str(r).strip() != "":
            try:
                val = int(float(str(r).strip()))
                if 0 <= val <= 5000:
                    rankings[p] = val
            except (ValueError, TypeError):
                pass
    save_rankings(rankings)
    flash("Rankings updated.", "success")
    return redirect(url_for("players"))


@app.route("/players/login", methods=["GET", "POST"])
def players_login():
    if request.method == "POST":
        if request.form.get("password") == PLAYERS_PASSWORD:
            session["players_authenticated"] = True
            return redirect(url_for("players"))
        flash("Incorrect password.", "error")
    return render_template("players_login.html")


@app.route("/players/logout")
def players_logout():
    session.pop("players_authenticated", None)
    return redirect(url_for("index"))


@app.route("/results", methods=["GET", "POST"])
def results():
    if request.method == "GET":
        rankings = load_rankings()
        return render_template("results.html", rankings=rankings)

    # POST: record one or more results
    # Form can send: game_team1_0, game_team1_1, game_team2_0, game_team2_1, winner per row
    count = 0
    i = 0
    while True:
        t1_0 = request.form.get(f"team1_0_{i}")
        t1_1 = request.form.get(f"team1_1_{i}")
        t2_0 = request.form.get(f"team2_0_{i}")
        t2_1 = request.form.get(f"team2_1_{i}")
        winner = request.form.get(f"winner_{i}")
        if not t1_0 and not t1_1 and not t2_0 and not t2_1:
            break
        if t1_0 and t1_1 and t2_0 and t2_1 and winner and winner in ("1", "2"):
            team1 = (t1_0.strip(), t1_1.strip())
            team2 = (t2_0.strip(), t2_1.strip())
            rankings = load_rankings()
            r1 = tuple(rankings.get(p, DEFAULT_RATING) for p in team1)
            r2 = tuple(rankings.get(p, DEFAULT_RATING) for p in team2)
            prob_team1 = win_probability(r1, r2)
            update_rankings_for_match(None, team1, team2, int(winner))
            append_match(team1, team2, int(winner), prob_team1=prob_team1)
            count += 1
        i += 1
        if i > 50:
            break

    if count == 0:
        # Try single-game format: p1, p2, p3, p4, winner
        game = request.form.get("game_single", "").strip()
        winner = request.form.get("winner_single")
        if game and winner and winner in ("1", "2"):
            parts = [p.strip() for p in game.replace(",", " ").split() if p.strip()]
            if len(parts) >= 4:
                team1 = (parts[0], parts[1])
                team2 = (parts[2], parts[3])
                rankings = load_rankings()
                r1 = tuple(rankings.get(p, DEFAULT_RATING) for p in team1)
                r2 = tuple(rankings.get(p, DEFAULT_RATING) for p in team2)
                prob_team1 = win_probability(r1, r2)
                update_rankings_for_match(None, team1, team2, int(winner))
                append_match(team1, team2, int(winner), prob_team1=prob_team1)
                count = 1

    if count > 0:
        flash(f"Updated rankings for {count} match(es).", "success")
    else:
        flash("No valid result to record. Enter Team 1, Team 2, and winner.", "error")
    return redirect(url_for("rankings"))


@app.route("/rankings")
def rankings():
    rankings = load_rankings()
    sorted_rankings = sorted(
        rankings.items(), key=lambda x: -x[1]
    ) if rankings else []
    bios = load_player_bios()
    wins_losses = get_wins_losses_by_player()
    return render_template("rankings.html", rankings=sorted_rankings, bios=bios, wins_losses=wins_losses)


def _export_key_valid():
    return request.args.get("key") == os.environ.get("EXPORT_SECRET")


@app.route("/export/player_bios")
def export_player_bios():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(load_player_bios())


@app.route("/export/rankings")
def export_rankings():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(load_rankings())


@app.route("/export/players")
def export_players():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    return jsonify({"players": load_players_list()})


@app.route("/export/match_history")
def export_match_history():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    if not MATCH_HISTORY_FILE.exists():
        return jsonify({"matches": []})
    try:
        with open(MATCH_HISTORY_FILE) as f:
            data = json.load(f)
        return jsonify(data)
    except (json.JSONDecodeError, OSError):
        return jsonify({"matches": []})


@app.route("/export/play_history")
def export_play_history():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    if not SCHEDULER_HISTORY_FILE.exists():
        return jsonify({"with": {}, "against": {}})
    try:
        with open(SCHEDULER_HISTORY_FILE) as f:
            data = json.load(f)
        return jsonify(data)
    except (json.JSONDecodeError, OSError):
        return jsonify({"with": {}, "against": {}})


@app.route("/export/availability")
def export_availability():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(load_availability())


@app.route("/export/published_schedule")
def export_published_schedule():
    if not _export_key_valid():
        return jsonify({"error": "Forbidden"}), 403
    if not PUBLISHED_SCHEDULE_FILE.exists():
        return jsonify({})
    try:
        with open(PUBLISHED_SCHEDULE_FILE) as f:
            data = json.load(f)
        return jsonify(data)
    except (json.JSONDecodeError, OSError):
        return jsonify({})


def min_score_rating_gain(prob):
    """
    Given expected score share (0-1), return min winning score for that team in 'to 11, win by 2' format.
    E.g. '11-4 or better'. Team gains rating when actual share > prob.
    """
    if prob is None or prob >= 1.0:
        return "11-0 or better"
    if prob <= 0:
        return "11-9 or better"
    e1 = float(prob)
    for t2 in range(9, -1, -1):
        if 11 / (11 + t2) > e1:
            return f"11-{t2} or better"
    if 12 / 22 > e1:
        return "12-10 or better"
    return "13-11 or better"


@app.template_filter("date_long_month_short_year")
def date_long_month_short_year(value):
    """Format an ISO date string as 'Long Month Day Short Year', e.g. March 12 26."""
    if not value:
        return "—"
    s = (value[:10] if isinstance(value, str) else str(value)[:10]).strip()
    if len(s) != 10:
        return value if isinstance(value, str) else "—"
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return d.strftime("%B ") + str(d.day) + d.strftime(" %y")
    except (ValueError, TypeError):
        return s


@app.route("/history")
def history():
    matches = load_match_history()
    for m in matches:
        prob = m.get("prob_team1")
        if prob is not None:
            m["upset"] = (m.get("winner") == 1 and prob < 0.5) or (m.get("winner") == 2 and prob > 0.5)
        else:
            m["upset"] = False
    history_edit_authenticated = session.get("history_edit_authenticated", False)
    return render_template("history.html", matches=matches, history_edit_authenticated=history_edit_authenticated)


@app.route("/history/unlock", methods=["POST"])
def history_unlock():
    """Unlock score editing on Past games (schedule password)."""
    if request.form.get("password", "").strip() == SCHEDULE_PASSWORD:
        session["history_edit_authenticated"] = True
        flash("You can now edit scores.", "success")
    else:
        flash("Incorrect password.", "error")
    return redirect(url_for("history"))


@app.route("/history/lock")
def history_lock():
    session.pop("history_edit_authenticated", None)
    return redirect(url_for("history"))


@app.route("/history/save", methods=["POST"])
def history_save():
    """Save edited scores for past games. Requires history unlock."""
    if not session.get("history_edit_authenticated"):
        flash("Unlock score editing first (password required).", "error")
        return redirect(url_for("history"))
    matches = load_match_history()
    for i, m in enumerate(matches):
        s1 = request.form.get(f"score_team1_{i}")
        s2 = request.form.get(f"score_team2_{i}")
        if s1 not in (None, "") and s2 not in (None, ""):
            try:
                m["score_team1"] = int(s1)
                m["score_team2"] = int(s2)
            except ValueError:
                pass
        else:
            m.pop("score_team1", None)
            m.pop("score_team2", None)
    save_match_history(matches)
    flash("Scores updated.", "success")
    return redirect(url_for("history"))


@app.route("/reset-history", methods=["POST"])
def reset_history():
    if request.form.get("reset_history_password", "").strip() != PLAYERS_PASSWORD:
        flash("Incorrect password. Use the players password to reset partner history.", "error")
        return redirect(url_for("index"))
    if SCHEDULER_HISTORY_FILE.exists():
        SCHEDULER_HISTORY_FILE.unlink()
    flash("Partner/opponent history reset. Rankings unchanged.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
