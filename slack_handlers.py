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
        f"Generate the schedule from the *Generate* tab on the web app (password required)."
    )
    return {"response_type": "ephemeral", "text": text}


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
    return {"response_type": "ephemeral", "text": f"Unknown command: {command}. Use `/pb-schedule`, `/pb-in`, `/pb-out`, `/pb-availability`, `/pb-rankings`, or `/pb-history`."}
