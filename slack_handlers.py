"""
Slack slash command handlers for the pickleball scheduler.
Each handler returns a dict for Slack's JSON response (text or blocks).
"""

from datetime import timedelta


def _availability_status(entry):
    if entry is None:
        return "not_answered"
    if isinstance(entry, str):
        val = entry.strip().lower()
        if val in ("in", "out", "partial", "not_answered"):
            return val
        return "not_answered"
    if isinstance(entry, dict):
        st = str(entry.get("status", "not_answered")).strip().lower()
        if st in ("in", "out", "partial", "not_answered"):
            return st
    return "not_answered"


def _availability_partial_label(entry):
    if isinstance(entry, dict) and _availability_status(entry) == "partial":
        when = str(entry.get("when", "start")).strip().lower()
        when_word = "first" if when == "start" else "last"
        try:
            games = int(entry.get("games", 4))
        except (TypeError, ValueError):
            games = 4
        return f"Partial ({when_word} {games})"
    return None


def _availability_for_game_day(availability_all, game_day):
    """Thursday availability; includes legacy Wednesday key for the same week."""
    date_key = game_day.isoformat()
    legacy_key = (game_day - timedelta(days=1)).isoformat()
    current = dict(availability_all.get(date_key, {}))
    legacy = availability_all.get(legacy_key, {})
    if isinstance(legacy, dict):
        for player, status in legacy.items():
            if player not in current:
                current[player] = status
    return date_key, current


def handle_pb_in(text, player_list, load_availability, save_availability, get_next_wednesday):
    """Mark a player as in for next Thursday. Usage: /pb-in Alice"""
    name = (text or "").strip()
    if not name:
        return {"response_type": "ephemeral", "text": "Usage: `/pb-in YourName` (e.g. /pb-in Alice)"}
    if name not in player_list:
        return {"response_type": "ephemeral", "text": f"Unknown player: {name}. Players on list: {', '.join(player_list[:15])}{'...' if len(player_list) > 15 else ''}"}
    next_game = get_next_wednesday()
    date_key = next_game.isoformat()
    availability_all = load_availability()
    if date_key not in availability_all:
        availability_all[date_key] = {}
    availability_all[date_key][name] = "in"
    save_availability(availability_all)
    return {"response_type": "ephemeral", "text": f"Marked *{name}* as *In* for {next_game.strftime('%A, %b %d')}."}


def handle_pb_out(text, player_list, load_availability, save_availability, get_next_wednesday):
    """Mark a player as out. Usage: /pb-out Alice"""
    name = (text or "").strip()
    if not name:
        return {"response_type": "ephemeral", "text": "Usage: `/pb-out YourName`"}
    if name not in player_list:
        return {"response_type": "ephemeral", "text": f"Unknown player: {name}. Players: {', '.join(player_list[:15])}{'...' if len(player_list) > 15 else ''}"}
    next_game = get_next_wednesday()
    date_key = next_game.isoformat()
    availability_all = load_availability()
    if date_key not in availability_all:
        availability_all[date_key] = {}
    availability_all[date_key][name] = "out"
    save_availability(availability_all)
    return {"response_type": "ephemeral", "text": f"Marked *{name}* as *Out* for {next_game.strftime('%A, %b %d')}."}


def handle_pb_availability(player_list, load_availability, get_next_wednesday):
    """List who's in/out for next Thursday."""
    next_game = get_next_wednesday()
    availability_all = load_availability()
    _, availability = _availability_for_game_day(availability_all, next_game)
    lines = [f"*Games on {next_game.strftime('%A, %B %d')}*"]
    for p in player_list:
        entry = availability.get(p)
        status = _availability_status(entry)
        if status == "in":
            lines.append(f"  • {p} — In")
        elif status == "out":
            lines.append(f"  • {p} — Out")
        elif status == "partial":
            partial = _availability_partial_label(entry) or "Partial"
            lines.append(f"  • {p} — {partial}")
        else:
            lines.append(f"  • {p} — _not set_")
    return {"response_type": "ephemeral", "text": "\n".join(lines) if lines else "No players on list."}


def handle_pb_rankings(load_rankings):
    """List current rankings."""
    rankings = load_rankings()
    if not rankings:
        return {"response_type": "ephemeral", "text": "No rankings yet. Record some games from the web app or generate a schedule."}
    sorted_r = sorted(rankings.items(), key=lambda x: -x[1])
    lines = ["*Current rankings*"]
    for i, (p, r) in enumerate(sorted_r, 1):
        lines.append(f"  {i}. {p}: {r}")
    return {"response_type": "ephemeral", "text": "\n".join(lines)}


def handle_pb_history(load_match_history, limit=10):
    """Last N matches. Usage: /pb-history [N]"""
    matches = load_match_history()
    if not matches:
        return {"response_type": "ephemeral", "text": "No past games recorded."}
    lines = ["*Past games (newest first)*"]
    for m in matches[:limit]:
        t1 = " & ".join(m["team1"])
        t2 = " & ".join(m["team2"])
        w = "Team 1" if m.get("winner") == 1 else "Team 2"
        date_str = (m.get("date") or "")[:10]
        score = ""
        if "score_team1" in m and "score_team2" in m:
            score = f" ({m['score_team1']}-{m['score_team2']})"
        lines.append(f"  • {date_str}: {t1} vs {t2} → {w} won{score}")
    return {"response_type": "ephemeral", "text": "\n".join(lines)}


def handle_pb_schedule(player_list, load_availability, get_next_wednesday, load_published_schedule, add_round_court_and_bye):
    """Show next Thursday; if a schedule is published, show it. Otherwise who's in/out and hint for generating."""
    next_game = get_next_wednesday()
    published = load_published_schedule()
    if published:
        add_round_court_and_bye(published["schedule_entries"], published["players"])
        lines = [f"*Schedule for {published.get('next_wednesday_display', next_game.strftime('%A, %B %d'))}*"]
        if published.get("time_location"):
            lines.append(f"Time & location: {published['time_location']}")
        lines.append(f"Players: {', '.join(published['players'])}")
        current_round = None
        for e in published["schedule_entries"]:
            if e.get("round") != current_round:
                current_round = e.get("round")
                lines.append(f"\n*Round {current_round}*")
            team1 = e.get("team1", [])
            team2 = e.get("team2", [])
            t1 = " & ".join(team1) if len(team1) == 2 else "—"
            t2 = " & ".join(team2) if len(team2) == 2 else "—"
            lines.append(f"  Court {e.get('court', 'A')}: {t1} vs {t2}")
            if e.get("round_bye"):
                lines.append(f"  Bye: {', '.join(e['round_bye'])}")
        lines.append("\nRecord results from the *Schedule* tab on the web app.")
        return {"response_type": "ephemeral", "text": "\n".join(lines)}
    availability_all = load_availability()
    _, availability = _availability_for_game_day(availability_all, next_game)
    in_count = sum(1 for p in player_list if _availability_status(availability.get(p)) == "in")
    partial_count = sum(1 for p in player_list if _availability_status(availability.get(p)) == "partial")
    text = (
        f"*Next games: {next_game.strftime('%A, %B %d')}*\n"
        f"No schedule published yet for this week.\n"
        f"Who's in: {in_count}" + (f" (+ {partial_count} partial)" if partial_count else "") + " | Use `/pb-in YourName` or `/pb-out YourName` to update.\n"
        f"Use `/pb-availability` to see the full list.\n"
        f"Generate the schedule from the *Generate* tab on the web app (password required)."
    )
    return {"response_type": "ephemeral", "text": text}


def handle_slack_command(command, text, **kwargs):
    """Dispatch by command and return Slack JSON response body."""
    cmd = (command or "").strip().lower()
    if cmd == "/pb-in":
        if kwargs["load_published_schedule"]():
            return {"response_type": "ephemeral", "text": "Schedule already published. Reach out to Bryan."}
        return handle_pb_in(text, kwargs["player_list"], kwargs["load_availability"], kwargs["save_availability"], kwargs["get_next_wednesday"])
    if cmd == "/pb-out":
        if kwargs["load_published_schedule"]():
            return {"response_type": "ephemeral", "text": "Schedule already published. Reach out to Bryan."}
        return handle_pb_out(text, kwargs["player_list"], kwargs["load_availability"], kwargs["save_availability"], kwargs["get_next_wednesday"])
    if cmd == "/pb-availability":
        return handle_pb_availability(kwargs["player_list"], kwargs["load_availability"], kwargs["get_next_wednesday"])
    if cmd == "/pb-rankings":
        return handle_pb_rankings(kwargs["load_rankings"])
    if cmd == "/pb-history":
        n = None
        if text and text.strip().split():
            try:
                n = int(text.strip().split()[0])
            except ValueError:
                pass
        return handle_pb_history(kwargs["load_match_history"], limit=n or 10)
    if cmd == "/pb-schedule":
        return handle_pb_schedule(
            kwargs["player_list"],
            kwargs["load_availability"],
            kwargs["get_next_wednesday"],
            kwargs["load_published_schedule"],
            kwargs["add_round_court_and_bye"],
        )
    return {"response_type": "ephemeral", "text": f"Unknown command: {command}. Use `/pb-schedule`, `/pb-in`, `/pb-out`, `/pb-availability`, `/pb-rankings`, or `/pb-history`."}
