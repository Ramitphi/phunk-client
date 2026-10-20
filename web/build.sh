#!/bin/sh
# Static half of phunk.social, into web/_site (what Vercel serves):
#   - the web client's page, CSS and JS from src/phunk/static
#   - web/head.html (link previews, the Farcaster mini app embed) put into the page
#   - web/public: images and /.well-known/farcaster.json, the mini app manifest
# The read API is api/index.py.
#
# Settings (environment variables):
#   SITE_DOMAIN            default phunk.social
#   FARCASTER_HEADER       the manifest's accountAssociation, from Farcaster's
#   FARCASTER_PAYLOAD      manifest tool (see web/README.md). Left empty if
#   FARCASTER_SIGNATURE    unset: the site works, but isn't a verified mini app.
set -eu
cd "$(dirname "$0")/.."
DOMAIN="${SITE_DOMAIN:-phunk.social}"
OUT=web/_site

fill() {
  sed -e "s|__DOMAIN__|$DOMAIN|g" \
      -e "s|__FARCASTER_HEADER__|${FARCASTER_HEADER:-}|" \
      -e "s|__FARCASTER_PAYLOAD__|${FARCASTER_PAYLOAD:-}|" \
      -e "s|__FARCASTER_SIGNATURE__|${FARCASTER_SIGNATURE:-}|"
}

rm -rf "$OUT"
mkdir -p "$OUT/static"
cp -R web/public/. "$OUT/"
cp src/phunk/static/app.css src/phunk/static/app.js "$OUT/static/"
fill < web/head.html > "$OUT/head.tmp"
awk -v head="$OUT/head.tmp" '/<\/head>/ { while ((getline line < head) > 0) print line } { print }' \
    src/phunk/static/index.html > "$OUT/index.html"
rm "$OUT/head.tmp"
fill < web/public/.well-known/farcaster.json > "$OUT/.well-known/farcaster.json"

if [ -z "${FARCASTER_HEADER:-}" ]; then
  echo "note: FARCASTER_HEADER is not set; the mini app manifest has no accountAssociation yet"
fi
echo "built $OUT for $DOMAIN"
