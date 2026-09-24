#!/usr/bin/env bash
# Build static marketing site from Flask templates → dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
APP_HOST="${APP_HOST:-https://app.getassureai.com}"
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
  --include 'style.css' \
  --include 'script.js' \
  --include 'sentry.bundle.js' \
  --exclude '*' \
  "$ROOT/prompt_matrix/static/" "$DIST_DIR/static/"

for LOCALE in en es zh fr de ja tr; do
  if [ "$LOCALE" = "en" ]; then
    OUT="$DIST_DIR"
  else
    OUT="$DIST_DIR/$LOCALE"
  fi
  echo "→ Rendering locale: $LOCALE → $OUT"
  "$PYTHON" "$ROOT/scripts/render_static.py" \
    --locale "$LOCALE" \
    --output "$OUT" \
    --static-base "$CDN_BASE_URL" \
    --app-host "$APP_HOST"
done

# Cloudflare Pages / static hosting metadata
cat > "$DIST_DIR/_redirects" <<'EOF'
/app/*  https://app.getassureai.com/app/:splat  302
/api/*  https://app.getassureai.com/api/:splat  302
EOF

if [ -f "$ROOT/landing/_headers" ]; then
  cp "$ROOT/landing/_headers" "$DIST_DIR/_headers"
fi

if [ -f "$ROOT/landing/robots.txt" ]; then
  cp "$ROOT/landing/robots.txt" "$DIST_DIR/robots.txt"
fi

if [ -f "$ROOT/prompt_matrix/static/favicon.svg" ]; then
  cp "$ROOT/prompt_matrix/static/favicon.svg" "$DIST_DIR/favicon.svg"
fi

echo "✅ Static marketing site built in $DIST_DIR"
HTML_COUNT="$(find "$DIST_DIR" -name 'index.html' | wc -l | tr -d ' ')"
echo "   HTML index files: $HTML_COUNT"
