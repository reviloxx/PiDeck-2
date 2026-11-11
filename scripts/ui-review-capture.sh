#!/usr/bin/env bash
# Capture PiDeck (or the full display) for agent UI/UX review without a VM.
# Requires a live X display and ImageMagick (`import`).
#
# Usage:
#   pideck --config config/pideck.yaml &   # host desktop, PIDECK_IMAGE unset
#   ./scripts/ui-review-capture.sh
#   # PNGs land in /tmp/pideck-ui-review/ for image-capable review tools
#
# On an appliance over SSH (after export DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000):
#   ./scripts/ui-review-capture.sh --root
set -euo pipefail

OUT_DIR="${PIDECK_UI_REVIEW_DIR:-/tmp/pideck-ui-review}"
STAMP="$(date +%Y%m%d-%H%M%S)"
MODE="window"

usage() {
  cat <<'EOF'
Usage: ui-review-capture.sh [--root] [--out DIR]

  --root   Capture the full root window (appliance / TV).
  --out    Output directory (default: /tmp/pideck-ui-review or $PIDECK_UI_REVIEW_DIR).

Needs DISPLAY and ImageMagick `import`. Prefer a running host `pideck` window
for visual review; escalate to TV SSH for Openbox/picom/Flatpak fidelity.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) MODE="root"; shift ;;
    --out)
      OUT_DIR="${2:?--out requires a directory}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${DISPLAY:-}" ]]; then
  echo "DISPLAY is unset. Start a desktop session or export DISPLAY=:0." >&2
  exit 1
fi

if ! command -v import >/dev/null 2>&1; then
  echo "ImageMagick \`import\` not found. Install imagemagick, or grab the window manually." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

if [[ "$MODE" == "root" ]]; then
  OUT="$OUT_DIR/root-$STAMP.png"
  import -window root "$OUT"
  echo "$OUT"
  exit 0
fi

if ! command -v wmctrl >/dev/null 2>&1; then
  echo "wmctrl not found; capturing the root window instead." >&2
  OUT="$OUT_DIR/root-$STAMP.png"
  import -window root "$OUT"
  echo "$OUT"
  exit 0
fi

# Prefer an exact PiDeck title match; fall back to any window whose title contains PiDeck.
WINDOW_ID="$(wmctrl -lx 2>/dev/null | awk '
  tolower($0) ~ /pideck\.pideck/ { print $1; exit }
')"
if [[ -z "$WINDOW_ID" ]]; then
  WINDOW_ID="$(wmctrl -l 2>/dev/null | awk '
    tolower($0) ~ /pideck/ { print $1; exit }
  ')"
fi

if [[ -z "$WINDOW_ID" ]]; then
  echo "No PiDeck window found. Start \`pideck --config config/pideck.yaml\` first," >&2
  echo "or pass --root to capture the whole display." >&2
  exit 1
fi

OUT="$OUT_DIR/pideck-$STAMP.png"
import -window "$WINDOW_ID" "$OUT"
echo "$OUT"
