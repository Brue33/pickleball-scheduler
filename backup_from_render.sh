#!/usr/bin/env bash
# Backup data from Render (or any host) into local JSON files.
#
# Usage (no EXPORT_SECRET on Render — uses schedule password by default):
#   export RENDER_URL="https://YOUR-SERVICE-NAME.onrender.com"
#   ./backup_from_render.sh
#
# Optional: override the key if you set EXPORT_SECRET on Render:
#   export EXPORT_KEY="your-EXPORT_SECRET-value"

set -e
cd "$(dirname "$0")"

# Same as Generate tab login when EXPORT_SECRET is not configured on the server
DEFAULT_BACKUP_KEY="PBGames26"
EXPORT_KEY="${EXPORT_KEY:-$DEFAULT_BACKUP_KEY}"

if [ -z "$RENDER_URL" ]; then
  echo "Set RENDER_URL first, e.g.:"
  echo '  export RENDER_URL="https://YOUR-SERVICE-NAME.onrender.com"'
  echo '  ./backup_from_render.sh'
  exit 1
fi

echo "Backing up from $RENDER_URL ..."

curl -fsS "$RENDER_URL/export/player_bios?key=$EXPORT_KEY" -o player_bios.json
curl -fsS "$RENDER_URL/export/rankings?key=$EXPORT_KEY" -o rankings.json
curl -fsS "$RENDER_URL/export/players?key=$EXPORT_KEY" -o players.json
curl -fsS "$RENDER_URL/export/match_history?key=$EXPORT_KEY" -o match_history.json
curl -fsS "$RENDER_URL/export/play_history?key=$EXPORT_KEY" -o play_history.json
curl -fsS "$RENDER_URL/export/availability?key=$EXPORT_KEY" -o availability.json
curl -fsS "$RENDER_URL/export/published_schedule?key=$EXPORT_KEY" -o published_schedule.json
curl -fsS "$RENDER_URL/export/drop_in_schedule?key=$EXPORT_KEY" -o drop_in_schedule.json
curl -fsS "$RENDER_URL/export/mens_league_standings?key=$EXPORT_KEY" -o mens_league_standings.json

echo "Backup complete. Files updated: player_bios.json, rankings.json, players.json, match_history.json, play_history.json, availability.json, published_schedule.json, drop_in_schedule.json, mens_league_standings.json"
