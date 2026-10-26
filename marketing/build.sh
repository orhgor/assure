#!/usr/bin/env bash
# Build static marketing site from Flask templates → dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
CDN_BASE_URL="${CDN_BASE_URL:-/static}"

cd "$ROOT"

if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/static"

echo "→ Copying static assets…"
rsync -a \
  --include 'landing.css' \
  --include 'landing-pilot.js' \
  --include 'landing-i18n.js' \
  --include 'favicon.svg' \
  --include 'favicon.ico' \
  --include 'assure-logo.png' \
  --include 'aws-startups.svg' \
  --include 's3-icon.svg' \
  --include 'onprem-icon.svg' \
  --include 'sentry.bundle.js' \
  --include 'marketing_pages.css' \
  --exclude '*' \
  "$ROOT/marketing/static/" "$DIST_DIR/static/"

# Real favicon.ico rasterized from favicon.svg (own copy, no workbench read).
if [ ! -f "$ROOT/marketing/static/favicon.ico" ]; then
  "$PYTHON" "$ROOT/marketing/make_favicon.py" || echo "warn: favicon.ico generation failed"
fi

# Locales to publish. ar is built for preview only: set PUBLISH_LOCALES="en es zh fr de ja tr ar" to turn it on.
PUBLISH_LOCALES="${PUBLISH_LOCALES:-en es zh fr de ja tr ar}"
export PUBLISH_LOCALES

for LOCALE in $PUBLISH_LOCALES; do
  if [ "$LOCALE" = "en" ]; then
    OUT="$DIST_DIR"
  else
    OUT="$DIST_DIR/$LOCALE"
  fi
  echo "→ Rendering locale: $LOCALE → $OUT"
  "$PYTHON" "$ROOT/marketing/render.py" \
    --locale "$LOCALE" \
    --output "$OUT" \
    --static-base "$CDN_BASE_URL"
done

if [ -f "$ROOT/landing/_headers" ]; then
  cp "$ROOT/landing/_headers" "$DIST_DIR/_headers"
fi

if [ -f "$ROOT/landing/robots.txt" ]; then
  cp "$ROOT/landing/robots.txt" "$DIST_DIR/robots.txt"
fi
if [ -f "$ROOT/landing/sitemap.xml" ]; then
  cp "$ROOT/landing/sitemap.xml" "$DIST_DIR/sitemap.xml"
fi
if [ -f "$ROOT/landing/llms.txt" ]; then
  cp "$ROOT/landing/llms.txt" "$DIST_DIR/llms.txt"
fi

if [ -f "$ROOT/marketing/static/favicon.svg" ]; then
  cp "$ROOT/marketing/static/favicon.svg" "$DIST_DIR/favicon.svg"
fi
if [ -f "$ROOT/marketing/static/favicon.ico" ]; then
  cp "$ROOT/marketing/static/favicon.ico" "$DIST_DIR/favicon.ico"
fi

echo "✅ Static marketing site built in $DIST_DIR"
HTML_COUNT="$(find "$DIST_DIR" -name 'index.html' | wc -l | tr -d ' ')"
echo "   HTML index files: $HTML_COUNT"
