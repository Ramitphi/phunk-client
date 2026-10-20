#!/bin/sh
# Static half of phunk.social: copies the web client's page, CSS and JS into
# web/_site, which Vercel serves. The read API is api/index.py.
set -eu
cd "$(dirname "$0")/.."
rm -rf web/_site
mkdir -p web/_site/static
cp src/phunk/static/app.css src/phunk/static/app.js web/_site/static/
cp src/phunk/static/index.html web/_site/index.html
echo "built web/_site"
