import json, re
from pathlib import Path

LOCALES_DIR = Path('marketing/locales')
TEMPLATES_DIR = Path('marketing/templates')

keys = set()
for template_file in TEMPLATES_DIR.rglob('*.html'):
    content = template_file.read_text(encoding='utf-8')
    for match in re.finditer(r'data-i18n(?:-html|-placeholder|-aria|-label)?="([^"]+)"', content):
        keys.add(match.group(1))
    for match in re.finditer(r"strings\.get\(\s*['\"]([a-zA-Z0-9_.]+)['\"]", content):
        keys.add(match.group(1))

en_data = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
missing = sorted(k for k in keys if k not in en_data)
print(f'Total template keys: {len(keys)}')
print(f'Total en keys: {len(en_data)}')
print(f'Missing in en: {len(missing)}')
for k in missing:
    print(f'  {k}')