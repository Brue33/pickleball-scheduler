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
)

app = Flask(__name__)
app.secret_key = "pickleball-scheduler-secret-change-in-production"

PLAYERS_FILE = Path(__file__).resolve().parent / "players.json"
MATCH_HISTORY_FILE = Path(__file__).resolve().parent / "match_history.json"
AVAILABILITY_FILE = Path(__file__).resolve().parent / "availability.json"
PUBLISHED_SCHEDULE_FILE = Path(__file__).resolve().parent / "published_schedule.json"
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


def append_match(team1, team2, winner, score_team1=None, score_team2=None):
    """Append one match to history."""
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


def save_published_schedule(date_key, next_wednesday_display, players, schedule_entries, rankings):
    """Save the generated schedule so the Schedule tab can show it (public view)."""
    data = {
        "date_key": date_key,
        "next_wednesday_display": next_wednesday_display,
        "players": list(players),
        "schedule_entries": schedule_entries,
        "rankings": dict(rankings),
    }
    with open(PUBLISHED_SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_round_court_and_bye(schedule_entries, players):
    """
    Add round, court (A/B), and round_bye to each entry for display.
    4-7 players: one game per round (Court A only). 8+: two games per round (Court A, Court B).
    """
    from collections import defaultdict
    n = len(players)
    round_size = 1 if n < 8 else 2
    for i, e in enumerate(schedule_entries):
        e["round"] = (i // round_size) + 1
        e["court"] = "A" if round_size == 1 else ("A" if i % 2 == 0 else "B")
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


@app.route("/")
def index():
    rankings = load_rankings()
    return render_template("index.html", rankings=rankings)


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
        schedule_password=SCHEDULE_PASSWORD,
        load_players_list=load_players_list,
        generate_schedule=generate_schedule,
        format_schedule=format_schedule,
        save_published_schedule=save_published_schedule,
        win_probability=win_probability,
        default_rating=DEFAULT_RATING,
        add_round_court_and_bye=add_round_court_and_bye,
    )
    return jsonify(response)


@app.route("/schedule", methods=["GET"])
def schedule():
    rankings = load_rankings()
    player_list = load_players_list()
    next_wed = get_next_wednesday()
    next_wed_str = next_wed.strftime("%A, %B %d, %Y")
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    availability = availability_all.get(date_key, {})
    published = load_published_schedule()
    if published:
        add_round_court_and_bye(published["schedule_entries"], published["players"])
    return render_template(
        "schedule.html",
        rankings=rankings,
        player_list=player_list,
        next_wednesday=next_wed_str,
        date_key=date_key,
        availability=availability,
        published_schedule=published,
    )


@app.route("/schedule/availability", methods=["POST"])
def schedule_availability():
    player_name = request.form.get("availability_player", "").strip()
    status = request.form.get("availability_status", "").strip().lower()
    if not player_name or status not in ("in", "out"):
        flash("Select your name and In or Out.", "error")
        return redirect(url_for("schedule"))
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    if date_key not in availability_all:
        availability_all[date_key] = {}
    availability_all[date_key][player_name] = status
    save_availability(availability_all)
    flash(f"Marked {player_name} as {status} for next Wednesday.", "success")
    return redirect(url_for("schedule"))


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
    add_round_court_and_bye(published["schedule_entries"], published["players"])
    return render_template(
        "schedule_result.html",
        schedule_entries=published["schedule_entries"],
        players=published["players"],
        rankings=published["rankings"],
    )


@app.route("/generate", methods=["GET", "POST"])
def generate():
    """Generate schedule (password protected). Saves as published schedule for Schedule tab."""
    if request.method == "GET":
        player_list = load_players_list()
        next_wed = get_next_wednesday()
        schedule_players_unlocked = session.get("schedule_players_unlocked", False)
        return render_template(
            "generate.html",
            player_list=player_list,
            next_wednesday=next_wed.strftime("%A, %B %d, %Y"),
            schedule_players_unlocked=schedule_players_unlocked,
        )

    # POST: generate schedule (requires schedule password)
    schedule_password = request.form.get("schedule_password", "").strip()
    if schedule_password != SCHEDULE_PASSWORD:
        flash("Invalid schedule password. Use the correct password to generate.", "error")
        return redirect(url_for("generate"))

    selected = request.form.getlist("selected_players")
    players_extra = request.form.get("players_extra", "").strip() if session.get("schedule_players_unlocked") else ""
    games_str = request.form.get("games", "").strip()

    players = [p.strip() for p in selected if p and p.strip()]
    if players_extra:
        extra = [p.strip() for p in players_extra.replace(",", "\n").split() if p.strip()]
        for p in extra:
            if p and p not in players:
                players.append(p)
    players = list(dict.fromkeys(players))

    if not players:
        flash("Select at least one player from the list, or add names below.", "error")
        return redirect(url_for("generate"))

    games = None
    if games_str:
        try:
            games = int(games_str)
            if games < 1:
                games = None
        except ValueError:
            pass

    try:
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
        add_round_court_and_bye(schedule_entries, players)
        next_wed = get_next_wednesday()
        save_published_schedule(
            next_wed.isoformat(),
            next_wed.strftime("%A, %B %d, %Y"),
            players,
            schedule_entries,
            rankings,
        )
        return render_template(
            "schedule_result.html",
            schedule_entries=schedule_entries,
            rankings=rankings,
            players=players,
        )
    except ValueError as e:
        flash(str(e), "error")
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
            update_rankings_for_match(None, team1, team2, int(winner), s1, s2)
            append_match(team1, team2, int(winner), s1, s2)
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
                if 0 <= val <= 3000:
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
            update_rankings_for_match(None, team1, team2, int(winner))
            append_match(team1, team2, int(winner))
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
                update_rankings_for_match(None, team1, team2, int(winner))
                append_match(team1, team2, int(winner))
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
    return render_template("rankings.html", rankings=sorted_rankings)


@app.route("/history")
def history():
    matches = load_match_history()
    return render_template("history.html", matches=matches)


@app.route("/reset-history", methods=["POST"])
def reset_history():
    from scheduler import HISTORY_FILE
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()
    flash("Partner/opponent history reset. Rankings unchanged.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
