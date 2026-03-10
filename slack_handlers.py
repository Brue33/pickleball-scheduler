"""
Slack slash command handlers for the pickleball scheduler.
Each handler returns a dict for Slack's JSON response (text or blocks).
"""


def handle_pb_in(text, player_list, load_availability, save_availability, get_next_wednesday):
    """Mark a player as in for next Wednesday. Usage: /pb-in Alice"""
    name = (text or "").strip()
    if not name:
        return {"response_type": "ephemeral", "text": "Usage: `/pb-in YourName` (e.g. /pb-in Alice)"}
    if name not in player_list:
        return {"response_type": "ephemeral", "text": f"Unknown player: {name}. Players on list: {', '.join(player_list[:15])}{'...' if len(player_list) > 15 else ''}"}
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    if date_key not in availability_all:
        availability_all[date_key] = {}
    availability_all[date_key][name] = "in"
    save_availability(availability_all)
    return {"response_type": "ephemeral", "text": f"Marked *{name}* as *In* for {next_wed.strftime('%A, %b %d')}."}


def handle_pb_out(text, player_list, load_availability, save_availability, get_next_wednesday):
    """Mark a player as out. Usage: /pb-out Alice"""
    name = (text or "").strip()
    if not name:
        return {"response_type": "ephemeral", "text": "Usage: `/pb-out YourName`"}
    if name not in player_list:
        return {"response_type": "ephemeral", "text": f"Unknown player: {name}. Players: {', '.join(player_list[:15])}{'...' if len(player_list) > 15 else ''}"}
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    if date_key not in availability_all:
        availability_all[date_key] = {}
    availability_all[date_key][name] = "out"
    save_availability(availability_all)
    return {"response_type": "ephemeral", "text": f"Marked *{name}* as *Out* for {next_wed.strftime('%A, %b %d')}."}


def handle_pb_availability(player_list, load_availability, get_next_wednesday):
    """List who's in/out for next Wednesday."""
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    availability = availability_all.get(date_key, {})
    lines = [f"*Games on {next_wed.strftime('%A, %B %d')}*"]
    for p in player_list:
        status = availability.get(p)
        if status == "in":
            lines.append(f"  • {p} — In")
        elif status == "out":
            lines.append(f"  • {p} — Out")
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


def handle_pb_schedule(player_list, load_availability, get_next_wednesday):
    """Show next Wednesday and who's in/out; hint for generating."""
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    availability = availability_all.get(date_key, {})
    in_count = sum(1 for p in player_list if availability.get(p) == "in")
    text = (
        f"*Next games: {next_wed.strftime('%A, %B %d')}*\n"
        f"Who's in: {in_count} | Use `/pb-in YourName` or `/pb-out YourName` to update.\n"
        f"Use `/pb-availability` to see the full list.\n"
        f"To *generate the schedule*, use `/pb-generate <schedule-password> [number-of-games]` (uses everyone marked in)."
    )
    return {"response_type": "ephemeral", "text": text}


def handle_pb_generate(text, schedule_password, load_players_list, load_availability, get_next_wednesday, generate_schedule, format_schedule, save_published_schedule, win_probability, default_rating):
    """Generate schedule from who's marked in this week. Usage: /pb-generate <password> [number-of-games]"""
    parts = (text or "").strip().split()
    if len(parts) < 1:
        return {"response_type": "ephemeral", "text": "Usage: `/pb-generate <schedule-password> [number-of-games]` — uses everyone marked *in* for this week."}
    password = parts[0]
    if password != schedule_password:
        return {"response_type": "ephemeral", "text": "Invalid schedule password."}
    games = None
    if len(parts) >= 2:
        try:
            games = int(parts[1])
            if games < 1:
                games = None
        except ValueError:
            pass
    player_list = load_players_list()
    next_wed = get_next_wednesday()
    date_key = next_wed.isoformat()
    availability_all = load_availability()
    availability = availability_all.get(date_key, {})
    players_in = [p for p in player_list if availability.get(p) == "in"]
    if len(players_in) < 4:
        return {"response_type": "ephemeral", "text": f"Need at least 4 players marked *in*. Right now: {len(players_in)} — {', '.join(players_in) or 'none'}. Use `/pb-in YourName` to mark in."}
    try:
        schedule_list, rankings = generate_schedule(players_in, games_per_round=games)
        lines = format_schedule(schedule_list, rankings, players=players_in)
        schedule_entries = []
        for i, (team1, team2) in enumerate(schedule_list, 1):
            r1 = [rankings.get(p, default_rating) for p in team1]
            r2 = [rankings.get(p, default_rating) for p in team2]
            prob = win_probability(r1, r2)
            playing = set(team1) | set(team2)
            bye = sorted(set(players_in) - playing)
            schedule_entries.append({
                "game": i,
                "team1": list(team1),
                "team2": list(team2),
                "line": lines[i - 1] if i <= len(lines) else "",
                "prob": prob,
                "bye": bye,
            })
        save_published_schedule(
            date_key,
            next_wed.strftime("%A, %B %d, %Y"),
            players_in,
            schedule_entries,
            rankings,
        )
        out = [f"*Schedule for {next_wed.strftime('%A, %B %d')}*"]
        for line in lines:
            out.append(f"  {line}")
        out.append("\nRecord results and update rankings from the *web app* (Schedule result page).")
        return {"response_type": "in_channel", "text": "\n".join(out)}
    except ValueError as e:
        return {"response_type": "ephemeral", "text": str(e)}


def handle_slack_command(command, text, **kwargs):
    """Dispatch by command and return Slack JSON response body."""
    cmd = (command or "").strip().lower()
    if cmd == "/pb-in":
        return handle_pb_in(text, kwargs["player_list"], kwargs["load_availability"], kwargs["save_availability"], kwargs["get_next_wednesday"])
    if cmd == "/pb-out":
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
        return handle_pb_schedule(kwargs["player_list"], kwargs["load_availability"], kwargs["get_next_wednesday"])
    if cmd == "/pb-generate":
        return handle_pb_generate(
            text,
            kwargs["schedule_password"],
            kwargs["load_players_list"],
            kwargs["load_availability"],
            kwargs["get_next_wednesday"],
            kwargs["generate_schedule"],
            kwargs["format_schedule"],
            kwargs["save_published_schedule"],
            kwargs["win_probability"],
            kwargs["default_rating"],
        )
    return {"response_type": "ephemeral", "text": f"Unknown command: {command}. Use `/pb-schedule`, `/pb-in`, `/pb-out`, `/pb-availability`, `/pb-rankings`, `/pb-history`, or `/pb-generate`."}
