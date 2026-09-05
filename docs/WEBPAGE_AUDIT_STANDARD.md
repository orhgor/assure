# Webpage Audit Standard

**Status:** standing orders for every public HTML / CSS / front-end JS change.
**Run:** before claiming UI work done, before opening or updating a PR, and again after visual polish.
**Purpose:** stop re-issuing the same craft instructions. Agents and humans run this checklist instead.

This file is **portable**. Copy it into any site repo. Fill **§0 Project profile** once per project. Keep claims / SEO / preflight in companion docs for that product.

**Companions (this repo):**

- Public site: [`landing/`](../landing/) (`index.html`, `audit.html`)
- Per-change reports: [`audits/`](./audits/) + [`audits/_TEMPLATE.md`](./audits/_TEMPLATE.md)
- Public revision table: [`landing/audit.html`](../landing/audit.html) (counts must match the tree)
- Product claims live in `prompt_matrix/i18n.py`, `prompt_matrix/PEM.md`, and in-app Pricing / Privacy

---

## 0. Project profile (fill once per site)

| Field | Value |
|---|---|
| Brand / product name | *e.g. SeroState* |
| Primary UI font | *e.g. Nunito via `--font-sans` — never Inter / Roboto / system-only as primary* |
| Mono / instrument font | *e.g. JetBrains Mono* |
| Shell max / pad | *e.g. `--shell-max: 1080px`, `--shell-pad-x: 3rem`* |
| Prose max | *e.g. `--prose-max: 760px`* |
| Visual modes | *e.g. Instrument / Editorial / Decision — do not invent a fourth* |
| Hero composition rule | Brand-first; one headline; one short support; one CTA group; one dominant visual |
| Hard claim rejects | *product-specific — link a claims/preflight doc* |
| Lead / form policy | *e.g. self-hosted only; no consumer form SaaS* |
| Cache-bust pattern | *e.g. `css/style.css?v=N`* |
| Viewports to check | **390**, **768**, **1080**, **1440** CSS px (override if needed) |

**Assure binding values** for this repo are in [§A](#a-assure-binding-profile).

---

## 1. When to run

Run the full checklist when a change touches:

- Homepage or landing sections
- Subpage layout / section chrome
- New components (viewers, forms, tables, tabs, modals)
- CSS tokens, modes, or shell geometry
- Hero, CTA, or conversion surfaces
- Visible copy that can wrap (headings, leads, CTAs, comparison lines)

Skip only for pure backend / Worker / ops with **zero** public HTML/CSS/JS UI impact. Still run **§8 Content honesty** if copy, metadata, or public assets changed.

After the checklist, file a short report under `docs/audits/` (or paste the [§10 card](#10-quick-pass--fail-card-paste-into-pr) into the PR).

---

## 2. Composition gates (landing / promo)

1. **One composition** in the first viewport — not a dashboard of widgets.
2. **Brand first** — logo or product name is hero-level; no headline that overpowers the brand.
3. **Hero budget** — brand, one headline, one short support, one CTA group, one dominant visual. No stats strips, schedules, address blocks, promo chips, or secondary marketing in the first viewport.
4. **Dominant visual** — edge-to-edge or full-bleed plane when the design system allows; not an inset media card, side panel, collage, or floating image block unless the existing system requires it.
5. **No hero overlays** — no floating badges, stickers, detached labels, or callout chips on media.
6. **Cards** — default **no cards** in heroes. Cards only when they hold a real user interaction. If removing border / shadow / radius / fill does not hurt understanding, it should not be a card.
7. **One job per section** — one purpose, one headline, usually one short support.
8. **Real visual anchor** — product, place, atmosphere, or evidence — not abstract decorative gradients as the main idea.

**Avoid default AI looks:** purple-on-white / purple-indigo themes; cream + terracotta serif; broadsheet hairline newspaper density; emoji clusters; rounded-full pill strips; multi-layer glow shadows; dark-mode-for-its-own-sake.

---

## 3. Typography & orphan checklist

| Check | Pass criteria |
|---|---|
| Expressive type | Primary UI font matches **§0** — not Inter / Roboto / Arial / bare system as the brand face |
| Intentional breaks | Multi-clause headings and leads use explicit line structure (e.g. `.text-lines` > `.text-line`, or equivalent) — do not rely on accidental wraps |
| Orphan last words | No lonely 1–2 word final line on headings, hero leads, section titles, or CTAs. Glue with `&nbsp;` / `\u00a0`, or rebreak with intentional lines |
| CTA phrases | Multi-word CTAs glue the last word when wrap risk is real |
| Pretty / balance | Body / desc / footnotes: `text-wrap: pretty` (or shared rule). Display / H2: `text-wrap: balance` where it helps |
| Hierarchy | One clear display size; supporting copy stays secondary; labels are labels (tracked/uppercase only where the system uses them) |
| No ops leakage | Implementer notes, ticket IDs, vendor bans stay out of section leads |

**Spot-check orphans at every viewport in §0** (default 390 / 768 / 1080 / 1440).

---

## 4. Empty space & vertical rhythm

Empty lines and dead air are craft failures, same as orphan words.

| Check | Pass criteria |
|---|---|
| No accidental blank bands | No large empty vertical gaps from unused wrappers, empty elements, double `<br>`, or padding stacked on padding |
| Section rhythm | New bands match adjacent section padding — not a random denser or sparer block |
| Collapsed empties | Empty optional slots (missing image, empty list, unused aside) do not leave a visible hole; hide or remove the slot |
| Hero air | Hero whitespace is intentional atmosphere, not leftover grid rows or unused columns |
| Copy density | Extra blank lines inside prose or lists are removed; one idea per short block |
| Stack vs gap | Prefer consistent gap tokens / shared section padding over one-off margins |

---

## 5. Alignment & spacing checklist

| Check | Pass criteria |
|---|---|
| Shell | Content sits in the project shell / max-width; no double horizontal padding (section + inner cap both padding-x) |
| Headers | Section headers align per mode / page pattern — match neighbors on the same page |
| Columns | Grids do not leave a 2+1 orphan tile; equal tracks or an intentional stack |
| Forms | Fields share one intake pattern; label + control alignment consistent; primary tap targets ≥ 44px |
| Stacks | Mobile is single column; no horizontal overflow; sticky header clearance respected |
| Measure | Essay / long prose stays on prose max; interactive tooling may use full shell |

---

## 6. Color & contrast checklist

| Check | Pass criteria |
|---|---|
| Tokens | New UI uses semantic CSS variables from the project palette — no one-off theme |
| Links | Body links match established link color / underline hover pattern |
| Active states | Active borders / focus match the mode system — not random accent blues |
| Contrast | Body-size text and labels meet WCAG AA on their backgrounds |
| Signal language | Warn / gap / error colors are reserved for real signals — not decorative alarm |

---

## 7. Responsive & interaction checklist

| Check | Pass criteria |
|---|---|
| Viewports | Layout holds at every size in §0; no clipped CTAs; tabs/filters stack when needed |
| Touch | Primary controls ≥ **44px** min height |
| Focus | Visible `:focus-visible` rings; no `outline: none` without a replacement |
| Keyboard | Tabs, disclosures, forms operable without a pointer |
| Reduced motion | Honor `prefers-reduced-motion`; no essential info only in motion |
| Motion budget | 2–3 intentional presence motions max for visually led work — not bounce / glow stacks |

---

## 8. Content honesty & assets

| Check | Pass criteria |
|---|---|
| Real assets only | No stub / empty PDFs, fake “evidence” downloads, or placeholder media labeled as proof |
| No toy product surfaces | No fake payloads, copy-JSON theater, or demo widgets that pretend to be the product |
| Claims | Match the project’s claims / preflight doc — never invent accuracy %, time-savings, or category self-labels the product rejects |
| Metadata | Title / meta / OG match the live URL and real page job; no hallucinated paths |
| Forms | Match **§0** lead policy; success analytics only after real success responses; no PII in analytics payloads |

---

## 9. Ship gates (engineering)

| Check | Pass criteria |
|---|---|
| Cache bust | Bump the project’s CSS/JS version query when visuals change |
| Assets | New public files land in the real public tree and any build allowlist |
| Inject / shared chrome | New hubs get shared meta / consent / analytics injectors when the project uses them |
| Live verify | After deploy: confirm asset versions, critical IDs, and primary paths on production |

---

## 10. Quick pass / fail card (paste into PR)

```md
### Webpage audit
Report: docs/audits/YYYY-MM-DD-<slug>.md
Standard: docs/WEBPAGE_AUDIT_STANDARD.md

| Gate | Result |
|------|--------|
| Composition | Pass / Fail / N/A |
| Type & orphans | Pass / Fail |
| Empty space / rhythm | Pass / Fail |
| Alignment / shell | Pass / Fail |
| Color / contrast | Pass / Fail |
| Responsive (profile viewports) | Pass / Fail |
| Content honesty / assets | Pass / Fail |
| Forms / analytics | Pass / Fail / N/A |
| Cache bust | Pass / Fail / N/A |

Fixes landed in: <commits>
```

**Definition of done:** every applicable gate is **Pass** (or N/A with reason). Fix Failures in the same PR — do not leave look-and-feel as “polish later.”

---

## A. Assure binding profile

| Field | Value |
|---|---|
| Brand / product name | Assure |
| Primary UI font | **IBM Plex Sans** via `--font-family` |
| Mono / instrument font | **IBM Plex Mono** via `--font-mono` |
| Shell max / pad | `--shell-max: 1080px`; `--shell-pad-x: 3rem` (1.25rem below 768px) |
| Prose max | `--prose-max: 760px` |
| Visual modes | Editorial (narrative bands) · Decision (pricing pair) — do not invent a third |
| Hero composition rule | Mark + **Assure** are hero-level; one H1; one support; one CTA group; Trust Blue full-bleed plane as the visual |
| Hard claim rejects | No “data never leaves your machine” (a Send goes to the chosen provider). No invented accuracy %, time-savings, or traffic stats. Pricing numbers only from in-app copy (Free 10 Sends/day; Pro $5 / 100 Sends). Closed to the internet / open to the internet wording only. |
| Lead / form policy | No marketing forms on `landing/` until a self-hosted intake exists. Do not add Formspree / Netlify Forms / Typeform. |
| Cache-bust pattern | `landing/assets/site.css?v=N` and `landing/assets/site.js?v=N`; product UI `static/style.css?v=assure-N` |
| Viewports | 390 · 768 · 1080 · 1440 |

### Mode feeling (Assure)

| Mode | Feeling | Use when |
|---|---|---|
| **Editorial** | Warm gray field, IBM Plex, long measure, few boxes | How it works, privacy, about |
| **Decision** | Two equal tracks, one highlighted action | Free / Pro |

### Intentional line breaks (Assure)

Prefer `.text-lines` > `.text-line` for multi-clause headings and leads. Glue orphan-prone last words with `&nbsp;`. Re-check after any copy edit — orphans regress easily.
