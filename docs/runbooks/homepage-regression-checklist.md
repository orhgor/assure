# Homepage & mobile regression checklist

Run this checklist whenever a change touches: homepage copy, trust logos,
mobile spacing, the marketing shell, the i18n / render path, or the
pricing / contact pages. Verify against the live site after CI lands.

## 1) Hero integrity
- [ ] "Documents in. Verified output out." renders as the h1
- [ ] Subtitle copy correct ("Assure checks your documents against the rules and shows the source page for every finding.", no raw keys)
- [ ] Primary CTA "See the proof for your role", secondary "Book a Demo"
- [ ] No accidental hard wraps or awkward line breaks

## 2) Proof / positioning copy
- [ ] One flow component (#steps): ParSure "Checks the file" -> CrossSure "Cross-checks the facts" -> Assure "Sends one clear response", with the "Human approval" badge between steps 2 and 3
- [ ] "Every finding links to its source page." under the flow; the steps are not repeated elsewhere on the page
- [ ] Deployment icon row (On-prem, AWS, Amazon S3) sits between the hero and the flow
- [ ] Proof / verification message stays specific, not generic

## 3) Trust architecture
- [ ] Orchestration Engine / Mathematical Logic Engine / Adversarial Audit /
      Audit-Ready Export all render — no duplication, truncation, or misalignment

## 4) AWS / S3 trust band
- [ ] Both logos render (no text fallback), centered, small and restrained
- [ ] No overlap or drift on mobile

## 5) Mobile rhythm (≤640px)
- [ ] Header compact (logo + language selector on one baseline)
- [ ] Hero pulled up (~48px below header, no dead zone)
- [ ] Subtitle wraps evenly (capped ~21rem), CTA stack centered, buttons ≤20rem
- [ ] Trust band on the same center axis; section spacing intentional

## 6) Public-page cleanliness
Zero occurrences in public page source or rendered text of:
Workbench · Prompt Library · Audit Console · Previous work · Tester ·
PEM · Closed to the internet · Describe the change
(Exception: the privacy policy's legal description of the product's
"Closed to the Internet" mode is intentional.)

## 7) Pricing / contact integrity
- [ ] Pricing + about + terms use the marketing shell (no app chrome)
- [ ] Enterprise CTA → /contact-us/ ("Contact sales")
- [ ] Contact page: heading, 4-field form, success + mailto fallback intact

## 8) Source cleanliness
- [ ] Inline catalog contains only keys used by that page (≈100 keys, ~12KB,
      not the ~4,500-key app catalog)
- [ ] Zero internal-prefix keys (workbench.*, app.*, tester.*, account.* …)
- [ ] `_used_string_keys` extraction intact (render.py) — if a page renders
      raw key names like "landing.trust.card1.title", the extraction broke

## 9) Visual balance
- [ ] No floating elements, no over-dense sections, whitespace deliberate

## Quick verification commands
```shell
# render sanity at mobile size
npx playwright screenshot --viewport-size='390,844' --full-page \
  https://www.getassureai.com/ mobile.png

# internal labels in public source (expect 0 on all pages)
for p in '' pricing/ about/ architecture/ terms/ privacy/ contact-us/; do
  printf '/%s: ' "$p"
  curl -s "https://www.getassureai.com/$p" | grep -ioc \
    'workbench\|prompt library\|audit console\|previous work\|tester\|pem\|closed to the internet\|describe the change'
done
```
