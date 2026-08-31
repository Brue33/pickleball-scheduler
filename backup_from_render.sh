#!/usr/bin/env bash
# Backup data from Render (or any host) into local JSON files and images.
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

# endpoint:outfile — keep in sync with /export/* routes in app.py
JSON_EXPORTS=(
  "player_bios:player_bios.json"
  "players:players.json"
  "rankings:rankings.json"
  "match_history:match_history.json"
  "play_history:play_history.json"
  "availability:availability.json"
  "published_schedule:published_schedule.json"
  "draft_schedule:draft_schedule.json"
  "drop_in_schedule:drop_in_schedule.json"
  "drop_in_requests:drop_in_requests.json"
  "drop_in_hub:drop_in_hub.json"
  "mens_league_standings:mens_league_standings.json"
  "replay_starting_ratings:replay_starting_ratings.json"
  "pickleball_resale:pickleball_resale.json"
  "court_bookings:court_bookings.json"
)

BACKED_UP_FILES=()
for item in "${JSON_EXPORTS[@]}"; do
  endpoint="${item%%:*}"
  outfile="${item##*:}"
  backup_curl "$endpoint" "$outfile"
  BACKED_UP_FILES+=("$outfile")
done

# Profile, court, and resale images (not stored in JSON)
IMAGES_ZIP="pickleball_images_backup.zip"
if curl -fsS "$RENDER_URL/export/images?key=$EXPORT_KEY" -o "$IMAGES_ZIP"; then
  if file "$IMAGES_ZIP" | grep -qi zip; then
    unzip -o "$IMAGES_ZIP" -d .
    rm -f "$IMAGES_ZIP"
    BACKED_UP_FILES+=("resale_images/ court_images/ player_images/")
    echo "Images extracted into resale_images/, court_images/, and player_images/"
  else
    rm -f "$IMAGES_ZIP"
    echo "Note: image backup skipped (deploy latest app for /export/images, or no images on server)."
  fi
else
  echo "Note: image backup skipped (endpoint unavailable — deploy latest app to back up photos)."
fi

echo ""
echo "Backup complete. JSON files updated:"
printf '  %s\n' "${BACKED_UP_FILES[@]}"
