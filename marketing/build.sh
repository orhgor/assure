#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist-test-render}"

echo "Building marketing site to $DIST_DIR"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# Export ROOT for Python
export ROOT

python3 << 'PYEOF'
import os
import sys
import json
from pathlib import Path

# Get ROOT from environment
ROOT = Path(os.environ['ROOT'])
sys.path.insert(0, str(ROOT.parent))

# Load cache versions
from marketing.cache_versions import LANDING_CSS, LANDING_JS

# Load locales
locales_dir = ROOT / "locales"
LOCALES = ['en', 'es', 'fr', 'de', 'ja', 'zh', 'tr', 'ar']

strings_cache = {}
for loc in LOCALES:
    path = locales_dir / f"{loc}.json"
    with open(path, 'r') as f:
        strings_cache[loc] = json.load(f)

# Locale paths
locale_paths = {
    'en': '/', 'es': '/es/', 'fr': '/fr/', 'de': '/de/',
    'ja': '/ja/', 'zh': '/zh/', 'tr': '/tr/', 'ar': '/ar/',
}

# Languages for selector
languages = {
    'en': 'English', 'es': 'Español', 'fr': 'Français', 'de': 'Deutsch',
    'ja': '日本語', 'zh': '中文', 'tr': 'Türkçe', 'ar': 'العربية',
}

# Setup Jinja2
from jinja2 import Environment, FileSystemLoader, select_autoescape
env = Environment(
    loader=FileSystemLoader(str(ROOT / 'templates')),
    autoescape=select_autoescape(['html', 'xml'])
)

# Add url_for function
def url_for(endpoint, **values):
    if endpoint == 'static':
        filename = values.get('filename', '')
        return f'/static/{filename}?v={LANDING_CSS}' if filename.endswith('.css') else f'/static/{filename}?v={LANDING_JS}' if filename.endswith('.js') else f'/static/{filename}'
    return '/'

env.globals['url_for'] = url_for

# Base template context
base_context = {
    'landing_css_version': LANDING_CSS,
    'landing_js_version': LANDING_JS,
    'locale_paths': locale_paths,
    'en_url': 'https://assureproof.com/',
    'og_image': 'https://assureproof.com/static/og-image.png',
    'json_ld': [],
    'languages': languages,
}

# Pages to render
pages = [
    ('index.html', 'landing.html', {
        'page_title': 'AssureProof | Catch the figure that does not match before you pay or approve',
        'page_description': 'AssureProof reads your contracts, invoices, claims and bids together and marks every figure that does not match across them, with both source pages open. A person reviews each flag and decides. Runs in your own AWS account or on your own servers, including air-gapped networks.',
        'page_suffix': '',
    }),
    ('terms/index.html', 'terms.html', {
        'page_title': 'Terms of use | AssureProof',
        'page_description': 'AssureProof runs in your own AWS account, on a private GPU, or on your own servers, including air-gapped networks. Model calls go to Amazon Bedrock in your account or to a self-hosted model.',
        'page_suffix': 'terms/',
    }),
    ('privacy/index.html', 'privacy.html', {
        'page_title': 'Privacy Policy | AssureProof',
        'page_description': 'How deployment affects what AssureProof stores, and how the marketing site handles contact details.',
        'page_suffix': 'privacy/',
    }),
    ('solutions/index.html', 'solutions.html', {
        'page_title': 'Verify figures across invoices, contracts and claims | AssureProof',
        'page_description': 'AssureProof cross-checks invoices against contracts, claims against policies, and bids against requirements.',
        'page_suffix': 'solutions/',
    }),
    ('about/index.html', 'about.html', {
        'page_title': 'About AssureProof | Cross-document figure verification',
        'page_description': 'AssureProof reads your contracts, invoices, claims and bids together and marks every figure that does not match across them, with both source pages open.',
        'page_suffix': 'about/',
    }),
    ('architecture/index.html', 'architecture.html', {
        'page_title': 'How AssureProof verifies figures across your documents | AssureProof',
        'page_description': 'The invoice is read, the contract is read, the relation "invoice total ≤ contract cap" is checked deterministically, and both source pages stay open for the reviewer.',
        'page_suffix': 'architecture/',
    }),
    ('security/index.html', 'security.html', {
        'page_title': 'Security and data | AssureProof',
        'page_description': 'Zero data leakage. Built for sovereign and air-gapped environments.',
        'page_suffix': 'security/',
    }),
    ('contact-us/index.html', 'contact.html', {
        'page_title': 'Contact | AssureProof',
        'page_description': 'Before you pay, accept, or sign, you still open the contract and the invoice side by side.',
        'page_suffix': 'contact/',
    }),
    ('pricing/index.html', 'pricing.html', {
        'page_title': 'Pricing | AssureProof',
        'page_description': 'Simple, transparent pricing for teams that verify before they approve.',
        'page_suffix': 'pricing/',
    }),
    ('use-cases/index.html', 'use_cases_index.html', {
        'page_title': 'Use cases | AssureProof',
        'page_description': 'Where a single missed number creates legal or financial exposure.',
        'page_suffix': 'use-cases/',
    }),
    ('use-cases/government/index.html', 'use_case_government.html', {
        'page_title': 'Government document checks · AssureProof',
        'page_description': 'Government procurement bids checked against statutory caps and compliance requirements.',
        'page_suffix': 'use-cases/government/',
    }),
    ('use-cases/insurance/index.html', 'use_case_insurance.html', {
        'page_title': 'Insurance claims review · AssureProof',
        'page_description': 'Insurance claim files checked against policies and supporting documents.',
        'page_suffix': 'use-cases/insurance/',
    }),
    ('use-cases/fuel-retail/index.html', 'use_case_fuel.html', {
        'page_title': 'Fuel retail bonus reconciliation · AssureProof',
        'page_description': 'Fuel Delivery Logs are checked against Bonus Rebate Statements.',
        'page_suffix': 'use-cases/fuel-retail/',
    }),
    ('poc/index.html', 'poc.html', {
        'page_title': 'POC | AssureProof',
        'page_description': 'Run a pilot on your files.',
        'page_suffix': 'poc/',
    }),
    ('404.html', '404.html', {
        'page_title': 'Page not found',
        'page_description': '',
        'page_suffix': '',
    }),
]

# Render each locale
DIST_DIR = Path(os.environ.get('DIST_DIR', str(ROOT / 'dist-test-render')))

for locale in LOCALES:
    strings = strings_cache[locale]
    
    context = {
        **base_context,
        'locale': locale,
        'strings': strings,
        'locale_path': locale_paths[locale],
        'page_url': f'https://assureproof.com{locale_paths[locale]}',
        'og_locale': locale + '_' + locale.upper(),
    }
    
    locale_dist = DIST_DIR / locale.lstrip('/')
    locale_dist.mkdir(parents=True, exist_ok=True)
    
    for out_file, template_name, page_ctx in pages:
        template = env.get_template(template_name)
        full_context = {**context, **page_ctx}
        html = template.render(full_context)
        out_path = locale_dist / out_file
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding='utf-8')
        
        # Also copy English to root for backward compatibility
        if locale == 'en':
            root_path = DIST_DIR / out_file
            root_path.parent.mkdir(parents=True, exist_ok=True)
            root_path.write_text(html, encoding='utf-8')

print("Build complete")
PYEOF
