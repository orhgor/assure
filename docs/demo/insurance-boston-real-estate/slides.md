# Slide Deck — Assure AI × Boston Real Estate Insurance

Export to PDF via Keynote/Google Slides or print from Markdown.

---

## Slide 1 — Title

**Assure AI**
*AI guesses. Assure proves.*

Real estate insurance compliance demo
Boston · v1.4 · September 2026

---

## Slide 2 — Before: Manual compliance loop

**Today (manual)**

```
PDF filing ──► Word memo ──► Spreadsheet check ──► Email sign-off
                     ↓
              Drift discovered at bind or audit
```

- Policy text ≠ rating engine config
- No single source of truth
- Audit trail scattered across email

**Cost:** Rework, E&O exposure, slow bind

---

## Slide 3 — After: Assure compliance loop

**With Assure (automated loop)**

```
Sources → Assemble → Verify (Z3) → Audit (Red-Hat) → Polish → Audit Report
```

- Structured document AST (JDF)
- Every claim scored and explainable
- Exportable JSON manifest for regulators

**Outcome:** Faster bind with **proof**

---

## Slide 4 — Live demo arc (6 steps)

1. **Ingest** — Policy PDF + rating JSON
2. **Assemble** — Structured underwriting memo
3. **Verify** — Z3 catches 2% vs 5% deductible drift
4. **Audit** — Red-Hat adversarial review
5. **Polish** — Fix one paragraph surgically
6. **Export** — Download Audit Report JSON

*20–30 minutes · production · getassureai.com*

---

## Slide 5 — Next steps

**Pilot proposal**

- Week 1: Your redacted filing + one rating config
- Week 2: Underwriter workshop on Assure
- Week 3: Export audit manifest into your compliance workflow

**Questions?**

https://getassureai.com

---

## Speaker notes (all slides)

- Lead with **drift story** — audience feels this in underwriting ops.
- Never claim NAIC/DORA certification — claim **workflow + audit export**.
- Show status bar and confidence overlay — visual proof lands better than architecture slides.
- End with pilot, not feature roadmap.
