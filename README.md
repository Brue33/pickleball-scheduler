# Mitten Smash Pickleball

Weekly doubles schedule with **individual rankings**, **win probability** per match, **partner rotation**, and **post-match ranking updates**.

## What it does

- **Schedule**: You give a list of players; it generates multiple games with rotating partners and avoids repeating the same partner/opponent too often.
- **Win chance**: Each game shows "Team 1 win chance: X%" from combined Elo ratings (teammate ratings summed vs opposing team).
- **Rankings**: Stored in `rankings.json`. New players start at 1300. After each match you report the winner; ratings update (Elo, K=32).

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

### Slack (in addition to the web app)

Run the scheduler from Slack with slash commands. Same data as the web app.

**Setup:** Create an app at [api.slack.com/apps](https://api.slack.com/apps). Add Slash Commands `/pb-in`, `/pb-out`, `/pb-availability`, `/pb-rankings`, `/pb-history`, `/pb-schedule` — all with Request URL `https://YOUR_DOMAIN/slack/command`. Set env var `SLACK_SIGNING_SECRET` (Signing Secret from the app). Deploy Flask so Slack can POST to `/slack/command`.

**Commands:** `/pb-in YourName`, `/pb-out YourName`, `/pb-availability`, `/pb-schedule`, `/pb-rankings`, `/pb-history [N]`. Generate the schedule from the web app (Generate tab). Record results from the web app.

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
  Alice: 1300
  Bob: 1300
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

- `app.py` – Flask web app (run with `python app.py`) and Slack endpoint `/slack/command`.
- `slack_handlers.py` – Slack slash command handlers.
- `scheduler.py` – Core logic and CLI.
- `templates/` – HTML pages for the web interface.
- `rankings.json` – Created automatically; stores each player’s rating.
- `play_history.json` – Tracks how often each pair played together/against.
- `player_bios.json` – Player bios (shown on Rankings). Edit via Players → Edit player bios.

### Keeping data when you push / deploy

The **players list**, **past games** (match history), **current rankings**, **bios**, and other data live in the app directory by default. When you deploy new code (e.g. git pull), that directory is often overwritten and **that data can be lost**.

**Option A – Persistent data directory (recommended for servers)**  
Set the environment variable `PICKLEBALL_DATA_DIR` to a path that is **not** overwritten on deploy (e.g. a persistent volume or a directory outside the repo):

```bash
export PICKLEBALL_DATA_DIR=/var/data/pickleball   # or wherever you keep data
python app.py
```

These are then read and written under that directory, so they survive code updates:

- **Players list** (`players.json`)
- **Past games** (`match_history.json`)
- **Current rankings** (`rankings.json`)
- **Partner/opponent history** (`play_history.json`)
- **Player bios** (`player_bios.json`)
- Availability, published schedule, etc.

**Option B – Commit data to git**  
You can commit the data files to the repo so they are pushed and pulled with your code: `players.json`, `player_bios.json`, `rankings.json`, `match_history.json`, `play_history.json`, etc. Then deploy as usual; the updated repo will include the latest data.

### Backing up data from Render

If you host on Render (or any ephemeral host) without a persistent disk, data changed on the live site is lost on the next deploy unless you pull it into the repo first. Use the export endpoints to backup before pushing new code.

1. **On Render:** In your Web Service → **Environment**, add:
   - **Key:** `EXPORT_SECRET`
   - **Value:** a long random string (e.g. from a password generator). Do not commit this to git.
   Redeploy once so the app has the new routes.

2. **Before pushing when the site has new data:** From your machine, set your site URL and secret, then pull each JSON into the project directory (or run `./backup_from_render.sh` if you use the script):

   ```bash
   cd pickleball_scheduler
   export RENDER_URL="https://YOUR-SERVICE-NAME.onrender.com"
   export EXPORT_KEY="your-EXPORT_SECRET-value"

   curl -s "$RENDER_URL/export/player_bios?key=$EXPORT_KEY" -o player_bios.json
   curl -s "$RENDER_URL/export/rankings?key=$EXPORT_KEY" -o rankings.json
   curl -s "$RENDER_URL/export/players?key=$EXPORT_KEY" -o players.json
   curl -s "$RENDER_URL/export/match_history?key=$EXPORT_KEY" -o match_history.json
   curl -s "$RENDER_URL/export/play_history?key=$EXPORT_KEY" -o play_history.json
   curl -s "$RENDER_URL/export/availability?key=$EXPORT_KEY" -o availability.json
   curl -s "$RENDER_URL/export/published_schedule?key=$EXPORT_KEY" -o published_schedule.json
   ```

   Then commit and push as usual. Treat the export URL and key like a password; anyone with the key can download your data.

## Summary

| Step | You do | Command |
|------|--------|--------|
| Start of week | Give list of players | `python scheduler.py schedule P1 P2 P3 ...` |
| After games | Give outcomes | `python scheduler.py results --game "A,B,C,D" --winner 1` or `batch-results` |
| Anytime | Check ratings | `python scheduler.py rankings` |
