#!/usr/bin/env bash
# Fetch the validation-fixture PDF. The PDF is never committed to the repo.
#
# The book is posted freely by its authors but remains copyright Cambridge
# University Press. See README.md, "Licensing".
set -euo pipefail

URL="${1:-https://www.cs.cornell.edu/home/kleinber/networks-book/networks-book.pdf}"
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
