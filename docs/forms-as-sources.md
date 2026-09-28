# Forms as compile sources (2026-09-28)

`prompt_matrix/services/source_carry.py` (`attach_form_fields`,
`form_field_sentence`, the numbering walk), `routers/draft.py`
(`build_sentence_map`, `attach_citations_to_tree`), `services/claim_policy.py`
(`_field_quote`). Tests: `tests/test_forms_as_sources.py`.

## The gap

A filled form — a CMS-1500, a claim form — is text the compile guard rightly
refuses to ground on: its lines are captions ("1. INSURED'S NAME (Last Name,
First Name…)") and values, not sentences a draft can cite, so every compile
over one ended in `compile_guard.form_aware`'s refusal with a link to the field
report (demo measurement 2026-09-27: two forms refused). Yet Parsure had read
the form's fields, each with a verbatim `grounding_quote`, a `raw` value as
written, an `element_id`, a page and a bbox. The compile could not use what the
intake had already verified.

## What happens now

1. **Lookup.** After the compile ranks its Sources rows and before it numbers
   them, `attach_form_fields(project_id, rows)` fetches the project's intake
   reports once (`parsure_repository.list_reports`, all revisions) and matches
   each row to a report by `document_id` — the vault row id the Sources upload
   stores under — else by filename (newest first). A row is a **form** when its
   report's `quality_flags` carries `form_template`
   (`v1_orchestrator.form_template_flag`) or when `compile_guard.looks_like_form`
   says so of its text. A form row gets `parsure_fields` (the report's fields),
   `parsure_report_id` and `parsure_form: true`; a prose row is untouched.
2. **Sentences.** `numbered_source_blocks` — the one numbering walk the prompt,
   the sentence map, the citation resolver and the carry plan all read — appends,
   after the source's own sentences and inside the same untrusted fence, the
   header `Fields read from the form (Parsure):` and one line per **found**
   field:

   ```
   [S13] Patient birth date: 01 15 1980
   ```

   The sentence is `"<Label>: <value as written>"`. The value is the field's
   `raw` (the text read under the label), else its `grounding_quote` (the page
   line the value was found on) — never `field.value`, the parsed reading
   (`1980-01-15` is Parsure's; only `01 15 1980` is on the page). Found means
   `evidence_state` in `found_verified` / `found_unverified`; a `found_suspect`
   read (debris such as `~~` under a label), an absent field, or a field with
   neither text yields no sentence. Ids continue the source's numbering; the
   per-file cap applies to the field lines as to any other.
3. **Provenance.** Each field entry carries
   `{"kind": "parsure_field", "field", "element_id", "page", "bbox", "quote",
   "report_id"}`. `build_sentence_map` keeps it on the id; when the model cites
   the id, `attach_citations_to_tree` writes a provenance row with
   `grounding_source: "parsure_field"`, `field`, `element_id`, `bbox`,
   `field_quote` (the verbatim page quote) and `parsure_report_id` beside the
   usual `extracted_quote` / `source_name` / `page` / `cited_id`.
4. **Verdict.** `claim_policy` tests a field citation's `field_quote` for
   verbatim presence in the source's `extracted_text` — the "Label: value"
   sentence is what the model saw, not a line of the page. The claim block's
   `quote` is the field's page quote, `page` the field's page, and the block
   carries `grounding_source: "parsure_field"`, `field`, `element_id`;
   `checks.source_quality.basis` ends with "from the field report (Parsure field
   '<name>', verbatim on page <n>)". Every other rule (entailment, numeric
   recompute, wording, carry status) is unchanged; a field whose quote is not on
   the page anchors nothing and the sentence is UNSUPPORTED.
5. **Guard.** `validate_compiled_draft` is unchanged. It now sees anchorable
   sentences for a form with a report, so a draft that cites them passes the
   grounding rules; a form **without** a report gets no sentences and is refused
   exactly as before, with `form_source` and the report link when there is one.
6. **Frames.** The carry plan entry per source (`sources[]` on the `compiled`
   and `verified` frames, the gate block and the audit row) carries
   `parsure_fields: n` — the field sentences the prompt actually carried for
   that source; `sentences`, `last_id`, `chars` and `text_sha256` include them,
   since the plan and the prompt are one walk.

## Example

Source `cms1500.pdf` (text: captions + values), report `pr-form-1` with
`insured_name` (`raw: "SAMPLE, JOHN Q"`, element `p1e1:…`, page 1) and
`patient_birth_date` (`raw: "01 15 1980"`, `value: "1980-01-15"`). Prompt block
tail:

```
[S11] 10. DATE 02 03 2026 …

Fields read from the form (Parsure):
[S12] Insured name: SAMPLE, JOHN Q
[S13] Patient birth date: 01 15 1980
```

Draft: `The insured named on the claim form is SAMPLE, JOHN Q. [S12]` →
claim block `{verdict: VERIFIED, quote: "SAMPLE, JOHN Q", page: 1,
grounding_source: "parsure_field", field: "insured_name", element_id:
"p1e1:…", checks.source_quality.basis: "…; from the field report (Parsure
field 'insured_name', verbatim on page 1)"}`; `verified.sources.sources[0].
parsure_fields == 2`.

## What is not claimed

- The parsed value is never the source (`docs/anti-claims.md`, "Forms as
  compile sources"). Only `raw` / `grounding_quote` — page text — reach the
  prompt or a block.
- A form with no report, or whose fields were all suspect or absent, compiles
  exactly as before: refused as a form. Nothing is added to make it pass.
- The field sentence is a restatement the model was shown; the verbatim test,
  the quote and the page are the field's own. `attach_substrate_provenance_to_tree`
  (the lexical matcher) is unchanged and still matches lock figures against the
  source text, where the field values already are.
- Fields are not re-verified here: `evidence_state` is Parsure's; a
  `found_unverified` field yields a sentence like a `found_verified` one, and
  the claim policy's own verbatim test decides.
