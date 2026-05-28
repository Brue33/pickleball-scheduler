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

backup_curl() {
  local endpoint="$1"
  local outfile="$2"
  local tmp="${outfile}.tmp"
  curl -fsS "$RENDER_URL/export/${endpoint}?key=$EXPORT_KEY" -o "$tmp"
  if grep -q '"error"' "$tmp" 2>/dev/null && grep -q 'Forbidden' "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "Error: $endpoint returned Forbidden. Deploy latest code or check EXPORT_KEY / schedule password."
    exit 1
  fi
  mv "$tmp" "$outfile"
}

backup_curl "player_bios" "player_bios.json"
backup_curl "rankings" "rankings.json"
backup_curl "players" "players.json"
backup_curl "match_history" "match_history.json"
backup_curl "play_history" "play_history.json"
backup_curl "availability" "availability.json"
backup_curl "published_schedule" "published_schedule.json"
backup_curl "drop_in_schedule" "drop_in_schedule.json"
backup_curl "mens_league_standings" "mens_league_standings.json"

echo "Backup complete. Files updated: player_bios.json, rankings.json, players.json, match_history.json, play_history.json, availability.json, published_schedule.json, drop_in_schedule.json, mens_league_standings.json"
