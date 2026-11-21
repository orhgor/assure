import json
from pathlib import Path

LOCALES_DIR = Path('marketing/locales')

# Missing keys for en (from the template analysis)
MISSING_EN_KEYS = {
    "about.checks.1": "You upload the files. AssureProof reads each one and keeps every figure tied to its source page. It writes a draft from those files and marks the figures and statements that don't match.",
    "about.checks.2": "A person on your team accepts, corrects or disputes each flag, and the decision is recorded.",
    "about.cta": "Try it on your own files",
    "about.proof.link": "See the example check",
    "about.title": "About AssureProof",
    "about.what.body": "AssureProof is a platform that checks a draft against the files it was written from, before you pay or sign off. If an invoice is over the contract cap, or a claim doesn't match the policy, you see the mismatch on the review screen with both source pages open. A person decides whether each flagged figure is right. AssureProof runs inside your own environment.",
    "about.who.title": "Who it is for",
    "contact.f_email": "Work email",
    "contact.f_name": "Name",
    "contact.lead": "Before you pay or sign, someone still has to open the contract and the invoice side by side. Tell us which workflow you'd like checked, and we'll reply with a short scope note.",
    "contact.note": "We start with one workflow and a few sample cases, once you've given a written \"Yes, proceed\".",
    "home.hero.eyebrow": "Document checking platform for finance, claims and procurement teams",
    "home.hero.icp": "For finance, claims and procurement teams. AssureProof checks the documents behind a payment or approval and shows you where they disagree, with both source pages open.",
    "home.hero.proof.line": "The mismatch is marked, and a person reviews and decides.",
    "home.hero.sub": "AssureProof reads your contracts, invoices, claims and bids, drafts with a source for every figure, and marks every mismatch with both pages. A person decides.",
    "home.start.title": "Bring a real packet. See what doesn't match.",
    "landing.lang": "Language",
    "landing.pilot.workflow.fuel": "Bonus reconciliation",
    "landing.skip": "Skip to content",
    "poc.assessment.title": "Request a proof of concept",
    "poc.form.cases": "Can you share 5-10 sample cases (up to 50 files)?",
    "poc.form.cases.check": "Need to check",
    "poc.form.review": "Who will join the 30-minute findings review?",
    "poc.lead": "Pick one workflow and share a few sample cases. We check them in your environment and walk you through what we find. The assessment starts once you've given a written \"Yes, proceed\".",
    "poc.offer.title": "What a proof of concept includes",
    "poc.title": "Try AssureProof on your own documents",
    "pricing.contact": "Get a price for your workflow",
    "pricing.pro": "Enterprise",
    "pricing.selective": "Start with one workflow and a small set of sample cases. Once you give a written \"Yes, proceed\", we run the assessment on your documents in your environment, with a scope agreed up front and a written review at the end.",
    "privacy.enterprise.body_html": "This policy covers the assureproof.com website only. We use your contact details only to reply and arrange a review.",
    "privacy.enterprise.s10.title": "International transfers",
    "privacy.enterprise.s11.title": "Changes to this policy",
    "privacy.enterprise.s12.title": "Contact us",
    "privacy.enterprise.s2.title": "How we use your data",
    "privacy.enterprise.s4.title": "Your rights",
    "privacy.enterprise.s7.title": "Children's privacy",
    "privacy.enterprise.summary": "This policy covers the assureproof.com website only. We use your contact details only to reply and arrange a review.",
    "privacy.enterprise.toc_title": "On this page",
    "privacy.enterprise.updated": "Last updated: September 8, 2026",
    "proof.a.title": "Master Services Agreement (MSA), Page 12, Clause 4.2",
    "proof.a.value": "120,000 EUR cap",
    "proof.b.billed": "Amount billed",
    "proof.b.title": "Vendor Invoice #884, Page 3, Line Item 8",
    "proof.b.value": "142,500 EUR billed",
    "proof.z3.body": "Mismatch marked: the invoice is 22,500 EUR over the contract cap. A person reviews and decides.",
    "proof.z3.title": "Conflict check",
    "sec.poc.body": "Every POC runs in an isolated single-tenant environment. Your documents are never retained or used to train or fine-tune AI models — zero AI model training — and are deleted after the review according to the agreed retention.",
    "security.pillar4.desc": "Every extracted figure, flagged contradiction, reviewer sign-off, and manual override is cryptographically timestamped.",
    "security.pillar4.title": "Every decision logged",
    "solutions.cards.1.finding": "The invoice total is 142,500 EUR. The contract cap is 120,000 EUR. Both source pages stay open.",
    "solutions.cards.4.name": "Internal report",
    "solutions.line": "Invoice p.3 and Contract p.12 are both open. Export is flagged.",
    "sovereign.title": "You choose where it runs",
    "terms.accuracy.h": "Accuracy",
    "terms.advice.h": "Not professional advice",
    "terms.as_is.h": "No warranty",
    "terms.files": "Only upload files you have the right to use. Do not upload privileged, confidential, or sensitive information unless you are authorised to do so. Do not send health or other sensitive text to a provider that is open to the internet unless you mean to. We are not responsible for uploads that violate your own obligations.",
    "terms.indemnity.h": "Liability",
    "terms.law": "You are responsible for how you use the output. If these terms cannot be enforced in your location, the rest still apply.",
    "terms.law.h": "If something goes wrong",
    "terms.prohibit.illegal": "Illegal content or activities",
    "terms.prohibit.rights": "Content that violates the rights of others",
    "terms.providers": "You must also follow the terms of the AI provider you connect. A Send to a connected provider is subject to that provider's rules. AssureProof does not replace them.",
    "terms.updated": "Updated 6 September 2026.",
    "terms.use": "AssureProof is designed for document compilation, verification, and compliance review. It is not a substitute for binding legal, financial, or regulatory decisions. If you rely on the output as your sole verification, that responsibility stays with you.",
    "terms.use.h": "What this is for",
    "uc.fuel.title": "Bonus and commission checks",
    "uc.gov.sub": "Catch a grant application whose figures don't match its attached expense audit before it's approved. Figures keep their source pages, and mismatches show up on the review screen. A person reviews and decides.",
    "uc.index.title": "See the kind of mismatch it flags",
    "uc.ins.sub": "Catch a claim amount that doesn't match the receipts and medical bills behind it before the payout. Figures keep their source pages, and mismatches show up on the review screen. A person reviews and decides.",
}

# New keys to add to all locales
NEW_KEYS = {
    "about.who.title": "Who it is for",
    "brand.subtitle": "Every figure, sourced.",
    "security.pillar4.desc": "Every extracted figure, flagged contradiction, reviewer sign-off, and manual override is cryptographically timestamped.",
    "security.pillar4.title": "Every decision logged",
}

def update_locale(locale_file, extra_keys=None):
    data = json.loads(locale_file.read_text(encoding='utf-8'))
    
    # Add missing EN keys if this is en.json
    if locale_file.name == 'en.json':
        for k, v in MISSING_EN_KEYS.items():
            if k not in data:
                data[k] = v
    
    # Add new keys to all locales
    for k, v in NEW_KEYS.items():
        if k not in data:
            data[k] = v
    
    if extra_keys:
        for k, v in extra_keys.items():
            if k not in data:
                data[k] = v
    
    # Write back with sorted keys for consistency
    sorted_data = {k: data[k] for k in sorted(data.keys())}
    locale_file.write_text(json.dumps(sorted_data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"Updated {locale_file.name}: {len(sorted_data)} keys")

# Update all locales
for locale_file in LOCALES_DIR.glob('*.json'):
    update_locale(locale_file)

print("Done!")