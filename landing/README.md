# landing/ — hosting metadata + design assets only
#
# These files are NOT the live webpage and are NOT deployed as HTML.
# The live site is built from prompt_matrix/templates/landing.html + i18n.py,
# then uploaded to Cloudflare R2 by CI (.github/workflows/webpage-deploy.yml).
#
# Kept here:
#   _headers, _redirects  — copied verbatim into dist/ by the build
#   robots.txt, sitemap.xml, CNAME — hosting metadata
#   assets/, favicon.svg — brand assets referenced by docs and the app
#   README.md, ICP.md, objections.md, PRODUCT_HUNT.md, launch/ — marketing docs
#
# Do NOT add page HTML here. Edit prompt_matrix/templates + i18n.py instead.
