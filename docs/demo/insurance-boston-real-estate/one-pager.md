# Assure AI — Real Estate Insurance Compliance

**One sentence:** Assure turns regulatory PDFs and rating engine configs into **verified, auditable documents** — with math-checked claims and an exportable audit trail regulators can inspect.

---

## The problem (before)

- Underwriters and compliance teams manually compare **policy language**, **rating JSON/YAML**, and **filings**.
- Numeric drift (deductibles, limits, inspection intervals) slips through until bind or audit.
- ChatGPT-style tools produce prose with **no proof**, **no structure**, **no export**.

---

## The solution (after — Assure v1.4)

| Step | What Assure does |
|------|------------------|
| **Sources** | Ingest PDFs and configs into a grounded vault |
| **Assemble** | Structure intent into a JDF document (not a chat blob) |
| **Verify** | Z3 symbolic checks + confidence overlay on every claim |
| **Audit** | Red-Hat adversarial stress test |
| **Polish** | Surgical node-level fixes without document drift |
| **Download** | JSON Audit Report manifest for compliance files |

---

## Why Boston real estate insurers care

- **Concentrated coastal wind exposure** — small numeric errors in deductibles have large loss implications.
- **DORA-style operational resilience** — need traceability from config → published policy.
- **Speed to bind** — underwriters shouldn't re-type; they should **verify and ship**.

---

## Proof in today's demo

Synthetic MA commercial RE policy vs. rating engine JSON with **intentional drift**:

- Wind/hail deductible: **2%** (policy) vs **5%** (config)
- Max liability: **$2M** vs **$2.5M**
- Inspection: **24 months** vs **12 months**

Assure surfaces the drift, critiques it, lets you fix inline, and exports the audit manifest.

---

## Contact / next steps

- **Live product:** https://getassureai.com/app (v1.4.0)
- **Follow-up:** Pilot on one product line with your redacted filings + actuarial configs

*Synthetic demo materials — not legal or actuarial advice.*
