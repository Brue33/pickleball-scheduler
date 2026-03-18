#!/usr/bin/env bash
# Backup data from Render (or any host) into local JSON files.
# Set RENDER_URL and EXPORT_KEY before running, e.g.:
#   export RENDER_URL="https://YOUR-SERVICE-NAME.onrender.com"
#   export EXPORT_KEY="your-EXPORT_SECRET-value"
#   ./backup_from_render.sh

set -e
cd "$(dirname "$0")"

if [ -z "$RENDER_URL" ] || [ -z "$EXPORT_KEY" ]; then
  echo "Set RENDER_URL and EXPORT_KEY first, e.g.:"
  echo '  export RENDER_URL="https://YOUR-SERVICE-NAME.onrender.com"'
  echo '  export EXPORT_KEY="your-EXPORT_SECRET-value"'
  exit 1
fi

curl -s "$RENDER_URL/export/player_bios?key=$EXPORT_KEY" -o player_bios.json
curl -s "$RENDER_URL/export/rankings?key=$EXPORT_KEY" -o rankings.json
curl -s "$RENDER_URL/export/players?key=$EXPORT_KEY" -o players.json
curl -s "$RENDER_URL/export/match_history?key=$EXPORT_KEY" -o match_history.json
curl -s "$RENDER_URL/export/play_history?key=$EXPORT_KEY" -o play_history.json
curl -s "$RENDER_URL/export/availability?key=$EXPORT_KEY" -o availability.json
curl -s "$RENDER_URL/export/published_schedule?key=$EXPORT_KEY" -o published_schedule.json
curl -s "$RENDER_URL/export/drop_in_schedule?key=$EXPORT_KEY" -o drop_in_schedule.json

echo "Backup complete. Files updated: player_bios.json, rankings.json, players.json, match_history.json, play_history.json, availability.json, published_schedule.json, drop_in_schedule.json"
