#!/usr/bin/env bash
# Fetch a source PDF into pdf/ for the pipeline to run against. PDFs are
# never committed to this repo (see .gitignore) -- check the license of
# whatever you point this at before doing anything with the output beyond
# local, personal use.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <pdf-url> [dest-dir=pdf]" >&2
  exit 1
fi

URL="$1"
DEST_DIR="${2:-pdf}"
FILENAME="$(basename "$URL")"
DEST="$DEST_DIR/$FILENAME"

mkdir -p "$DEST_DIR"

if [[ -f "$DEST" ]]; then
  echo "Already present: $DEST"
else
  echo "Fetching $URL"
  curl -fL --retry 3 --retry-delay 2 -o "$DEST.part" "$URL"
  mv "$DEST.part" "$DEST"
fi

echo "sha256: $(shasum -a 256 "$DEST" | cut -d' ' -f1)"
echo "bytes:  $(wc -c < "$DEST" | tr -d ' ')"
echo "$DEST"
