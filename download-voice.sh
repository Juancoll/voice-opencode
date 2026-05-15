#!/usr/bin/env bash
# Download a Piper voice from huggingface into ./voices/
#
# Usage:
#   ./download-voice.sh <lang>/<region>/<name>/<quality>
#
# Examples:
#   ./download-voice.sh es/es_ES/sharvard/medium
#   ./download-voice.sh es/es_MX/claude/high
#   ./download-voice.sh en/en_GB/jenny_dioco/medium
#   ./download-voice.sh fr/fr_FR/upmc/medium
#
# Browse all voices: https://huggingface.co/rhasspy/piper-voices/tree/main
#
# After download, run `./voice voices` to see them and inspect speakers
# (some voices like sharvard contain both M and F speakers).

set -euo pipefail

if [[ $# -lt 1 ]]; then
    grep -E '^#( |$)' "$0" | sed 's/^# \?//'
    exit 1
fi

PATH_SPEC="$1"
DIR="$(cd "$(dirname "$0")" && pwd)/voices"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${PATH_SPEC}"

# Derive the file stem: e.g. es_ES-sharvard-medium
# Path layout: <lang>/<region>/<name>/<quality>  ->  <region>-<name>-<quality>
read -r LANG REGION NAME QUALITY <<<"$(echo "$PATH_SPEC" | tr '/' ' ')"
if [[ -z "${QUALITY:-}" ]]; then
    echo "ERR: spec must be lang/region/name/quality, got: $PATH_SPEC" >&2
    exit 1
fi

# Most files use dash quality (medium, low, high, x_low, x-low)
# Try both x_low and x-low conventions.
STEM="${REGION}-${NAME}-${QUALITY}"

mkdir -p "$DIR"
cd "$DIR"

echo "Downloading voice: $STEM"
for ext in onnx onnx.json; do
    url="${BASE}/${STEM}.${ext}"
    echo "  GET $url"
    if ! curl -fL --progress-bar -o "${STEM}.${ext}" "$url"; then
        # Try the alternate convention with underscore quality flipped
        ALT_STEM="${REGION}-${NAME}-${QUALITY//-/_}"
        echo "  retry with $ALT_STEM"
        ALT_URL="${BASE}/${ALT_STEM}.${ext}"
        curl -fL --progress-bar -o "${STEM}.${ext}" "$ALT_URL"
    fi
done

echo
echo "Installed:"
ls -lh "${STEM}".*
echo
echo "Run: ./voice voices    # to see all voices and speakers"
echo "Use: VOICE=${STEM} ./voice say 'prueba'"
