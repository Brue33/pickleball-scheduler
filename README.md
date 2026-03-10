# Pickleball Doubles Scheduler

Weekly doubles schedule with **individual rankings**, **win probability** per match, **partner rotation**, and **post-match ranking updates**.

## What it does

- **Schedule**: You give a list of players; it generates multiple games with rotating partners and avoids repeating the same partner/opponent too often.
- **Win chance**: Each game shows "Team 1 win chance: X%" from combined Elo ratings (teammate ratings summed vs opposing team).
- **Rankings**: Stored in `rankings.json`. New players start at 1000. After each match you report the winner; ratings update (Elo, K=32).

## Quick start

### Web interface (recommended)

```bash
cd pickleball_scheduler
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser. You can:

- **Schedule** — Enter player names and generate the week’s games (with win chances).
- **Results** — Record who won each match; rankings update automatically.
- **Rankings** — View current ratings.

### Command line

```bash
cd pickleball_scheduler
python scheduler.py --help
```

## Weekly workflow

### 1. Generate the schedule

Provide the list of players (even number, 4+). Names are used as-is (spaces are fine if you quote).

```bash
python scheduler.py schedule Alice Bob Carol Dave Eve Frank Grace Henry
```

Optional: set number of games (default is about one per player, e.g. 8 games for 8 players):

```bash
python scheduler.py schedule Alice Bob Carol Dave Eve Frank Grace Henry --games 6
```

Example output:

```
Game 1: Alice & Bob vs Carol & Dave  (Team 1 win chance: 50%)
Game 2: Eve & Frank vs Grace & Henry  (Team 1 win chance: 50%)
...
Current rankings (for reference):
  Alice: 1000
  Bob: 1000
  ...
```

### 2. Play the games

Use the printed schedule. **Team 1** = first two names, **Team 2** = second two. Winner is either **1** or **2**.

### 3. Update rankings with results

**One match at a time:**

```bash
python scheduler.py results --game "Alice,Bob,Carol,Dave" --winner 1
```

(Team 1 = Alice & Bob won.)

**Multiple matches (batch):**

Put each result on its own line: `P1,P2,P3,P4,winner` (Team 1 = P1,P2; Team 2 = P3,P4; winner = 1 or 2).

```bash
python scheduler.py batch-results "Alice,Bob,Carol,Dave,1" "Eve,Frank,Grace,Henry,2"
```

Or use a file:

```bash
# results.txt:
# Alice,Bob,Carol,Dave,1
# Eve,Frank,Grace,Henry,2
python scheduler.py batch-results --file results.txt
```

Or pipe:

```bash
echo "Alice,Bob,Carol,Dave,1" | python scheduler.py batch-results
```

After each update, the script prints the new rankings.

### 4. View rankings anytime

```bash
python scheduler.py rankings
```

### 5. New “season” (optional)

To reset only partner/opponent history (so the scheduler doesn’t avoid past partners) but **keep** ratings:

```bash
python scheduler.py reset-history
```

## Files

- `app.py` – Flask web app (run with `python app.py`).
- `scheduler.py` – Core logic and CLI.
- `templates/` – HTML pages for the web interface.
- `rankings.json` – Created automatically; stores each player’s rating.
- `play_history.json` – Tracks how often each pair played together/against.

## Summary

| Step | You do | Command |
|------|--------|--------|
| Start of week | Give list of players | `python scheduler.py schedule P1 P2 P3 ...` |
| After games | Give outcomes | `python scheduler.py results --game "A,B,C,D" --winner 1` or `batch-results` |
| Anytime | Check ratings | `python scheduler.py rankings` |
