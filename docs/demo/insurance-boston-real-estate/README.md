# Assure AI — Insurance Demo Environment

**Audience:** Boston-based real estate insurance company
**Product version:** production (`f4e2d20`, UI `assure-127`); staging golden-path (`3cb82a7`, UI `assure-140`)
**Last verified:** 2026-09-10

---

## Which environment to use

| Environment | URL | `build_sha` | Recommendation |
|-------------|-----|-------------|----------------|
| **Production** | https://getassureai.com/app | `f4e2d20` | **Primary demo** — healthy, ~9.9 GB disk free |
| **Staging** | https://staging.getassureai.com/app?view=founder | `3cb82a7` | **Golden path / free stack** — Gemini 3.6 Flash + DeepSeek live |

Use **production** for stable ICP demos; use **staging** for Difference Engine (⌘K two-model compare) without burning paid API budget.

---

## Demo project setup (15 min)

### 1. Create project (Wizard)

1. Open **Write** (projects dashboard) → **+ New**.
2. **Step 1 — Template:** **Compliance Memo**.
3. **Step 2 — Sources:** Upload (drag-and-drop):
   - `assets/naic-underwriting-policy-redacted.pdf`
   - `assets/rating-engine-config.json` (rename upload to `.json` or paste as text file — vault accepts PDF/images; for JSON use **Upload File** or paste into compile prompt source)
4. **Step 3 — Prompt:** Use starter or paste:

   ```
   Summarize the Massachusetts commercial real estate underwriting obligations in the source.
   Report the wind/hail deductible percentage, the maximum liability in USD, and the inspection
   interval in months. Cite each figure.
   ```

   > The policy alone carries all three figures. The drift comparison
   > (`rating-engine-config.json` vs the policy) is answerable **only** when the engine config is
   > uploaded alongside the policy — see the `Flag any mismatch…` variant below and the
   > two-source note in `demo-script.md` step 2.

5. Name: **`Boston RE Insurance Demo`** → **Create project**.

> **Note:** Substrate Vault Textract expects single-page PDFs or images. The bundled policy PDF is one page. For JSON config, upload as `.txt` export or include key fields in the compile prompt if upload type blocks JSON.

### 2. Skip wizard (power users)

```bash
# After signing in to production/staging in browser, from dev machine with session cookie:
curl -X POST https://getassureai.com/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"Boston RE Insurance Demo","template_id":"compliance-memo","prompt":"..."}'
```

### 3. Clean project for repeat demos

Use a fresh project per audience, or reset Sources and clear canvas via new project. Avoid the polluted `default` project on shared staging.

---

## Pre-demo checklist

- [ ] Health: `curl https://getassureai.com/health` → `ok: true`, `assure-122`, `build_sha` starts with `1d6bf79`
- [ ] Logged in (Clerk) on demo machine
- [ ] Demo project created with both sources uploaded and **included** in compile
- [ ] Browser: 1440×900+, onboarding dismissed (`localStorage.assure_onboarding_complete = 1`)
- [ ] Backup: screen recording ready (QuickTime / Loom, 5–10 min)
- [ ] One-pager PDF printed or linked (`assets/assure-insurance-demo-one-pager.pdf`)
- [ ] Slide deck open (`slides.md` → export to PDF if needed)

---

## Asset index

| File | Purpose |
|------|---------|
| `assets/naic-underwriting-policy-redacted.pdf` | Regulatory-style policy (synthetic) |
| `assets/rating-engine-config.json` | Engine config with **intentional drift** |
| `assets/rating-engine-config-corrected.json` | Post-fix reference |
| `demo-script.md` | 6-step compliance loop + talking points |
| `one-pager.md` | Value prop (export to PDF) |
| `slides.md` | 3–5 slide outline |
| `scripts/build_demo_pdfs.py` | Regenerate PDFs |

---

## Known demo limitations (v1.4 — be honest)

1. **Source conflict scan** is keyword-level (years, durations, numbers) — not full semantic NLI. Say: *"Assure flags numeric drift; v2.0 adds deeper semantic contradiction."*
2. **JSON in Vault** — best uploaded as extracted text or referenced in prompt; Textract is PDF/image oriented.
3. **Audit export** — JSON Audit Report manifest (not Word PDF of full policy unless you use canvas PDF export separately).

---

## After the demo

1. Capture feedback in `feedback-template.md`.
2. Log decisions in OMP (threshold changes, objections).
3. Review `v1.5-priorities.md` for product follow-ups.
