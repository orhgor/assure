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
  --include 'assure-logo.png' \
  --include 'aws-startups.svg' \
  --include 's3-icon.svg' \
  --include 'og-image.png' \
  --include 'badge-built-on-aws.svg' \
  --include 'badge-aws-activate.svg' \
  --include 'favicon.ico' \
  --include 'style.css' \
  --include 'script.js' \
  --include 'sentry.bundle.js' \
  --include 'assure_csrf.js' \
  --exclude '*' \
  "$ROOT/prompt_matrix/static/" "$DIST_DIR/static/"

for LOCALE in en; do
  OUT="$DIST_DIR"
  echo "Rendering locale: $LOCALE to $OUT"
  "$PYTHON" "$ROOT/marketing/render.py" \
    --locale "$LOCALE" \
    --output "$OUT" \
    --static-base "$CDN_BASE_URL" \
    --app-host "$APP_HOST"
done

DEPLOY_DATE="$(date -u +%Y-%m-%d)"

cat > "$DIST_DIR/robots.txt" <<'ROBOTS'
User-agent: *
Disallow: /internal/
Allow: /

Sitemap: https://getassureai.com/sitemap.xml
ROBOTS

{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
  for p in "" "features/parsure" "features/mirrorsure" "use-cases" "security-and-audit" "company" "contact" "privacy" "terms"; do
    echo "  <url><loc>https://getassureai.com/$p</loc><lastmod>$DEPLOY_DATE</lastmod></url>"
  done
  echo '</urlset>'
} > "$DIST_DIR/sitemap.xml"

# Cloudflare Pages / static hosting metadata
cat > "$DIST_DIR/_redirects" <<'EOF'
/app/*  https://app.getassureai.com/app/:splat  302
/api/*  https://app.getassureai.com/api/:splat  302
EOF

if [ -f "$ROOT/landing/_headers" ]; then
  cp "$ROOT/landing/_headers" "$DIST_DIR/_headers"
fi

cp "$ROOT/prompt_matrix/static/founder_workbench.css" "$DIST_DIR/static/marketing_pages.css" 2>/dev/null || true
if [ -f "$ROOT/landing/llms.txt" ]; then
  cp "$ROOT/landing/llms.txt" "$DIST_DIR/llms.txt"
fi

if [ -f "$ROOT/prompt_matrix/static/favicon.svg" ]; then
  cp "$ROOT/prompt_matrix/static/favicon.svg" "$DIST_DIR/favicon.svg"
fi

echo "✅ Static marketing site built in $DIST_DIR"
HTML_COUNT="$(find "$DIST_DIR" -name 'index.html' | wc -l | tr -d ' ')"
echo "   HTML index files: $HTML_COUNT"
