# Parsure — vision facts for picture pages (2026-09-27)

`prompt_matrix/services/vision.py` asks a multimodal model what a picture
page *shows* and records the answer beside the intake report as **facts**.
It implements Part 5 of `todos/fable_execution_plan.md` with one deliberate
departure from the plan's sketch: facts are **not** merged into `fields[]`
(plan 5.2/5.3). The customer's earlier complaint was counts that disagreed
across surfaces; a visual observation is not a taxonomy field, has no label
anchor, no source span and no compliance rule, so it must not move
`fields_found`, `review_summary` or the queue. It lives in `report["vision"]`
and `report["execution"]["vision"]` only.

## Where it runs

```
run_after_parse: build_report → assign_document_id → attach_conflicts
                 → vision.attach_vision(report, file_bytes=…, intake=…)   ← here
                 → redhat_graph critique → repo.save_report (stamps snapshot)
```

`attach_vision` mutates and returns the report, never raises, and is a fast
no-op (no render, no model call) when disabled or when the intake has no
picture page. It must run before `save_report`, which stamps the canonical
snapshot hash (`services/snapshot.py`). It is also callable standalone:

```python
from prompt_matrix.services import vision
vision.attach_vision(report, file_bytes=pdf_or_image_bytes, intake=intake_dict)
vision.analyze_page(png_bytes, analysis_type="document_photo", context="document type auto_claim")
vision.picture_quality(png_bytes)
```

## Which pages are pictures

| Intake | Picture pages | `kind` |
|---|---|---|
| `modality` `phone_photo` | every page | `photo` |
| `modality` `screenshot` | every page | `screenshot` |
| `material_type` `image` (PNG/JPEG/TIFF/BMP that is neither camera- nor screen-shaped) | every page | `image` |
| anything else | only pages whose visual probe entry carries the flag `photo` | `photo` |

A digital or scanned PDF with no `photo` flag has no picture pages and the
block is `not_run` with `reason: "no picture pages"`. Embedded figures inside
text PDFs are not analysed in V1. At most `PARSURE_VISION_MAX_PAGES` (default
4) pages are analysed; the rest are listed as `skipped` with the bound in
`reason`.

### The enable rule, stated once (2026-09-28)

Every live report of 2026-09-27/28 read `execution.vision.status: not_run`
and the operator could not tell a rule from a misconfiguration. All of them
were digital PDFs — the correct answer — and the model had resolved
(`openrouter/amazon/nova-lite-v1`). The block now says which test decided:

| Situation | `status` | `reason` | Also on the block |
|---|---|---|---|
| `PARSURE_VISION` off | `disabled` | `PARSURE_VISION is off` | — |
| no vision model for the backend (`cloud` without an OpenRouter key; Ollama without `ASSURE_OLLAMA_MODEL_VISION`) | `disabled` | `no vision model … ; vision models: ollama=ASSURE_OLLAMA_MODEL_VISION (default qwen2.5vl:3b, off until set), openrouter=ASSURE_OPENROUTER_MODEL_VISION (default amazon/nova-lite-v1), bedrock=ASSURE_BEDROCK_MODEL_VISION (default ASSURE_BEDROCK_MODEL_DRAFT)` | `model` |
| digital or scanned PDF, no page flagged `photo` | `not_run` | `no picture pages` | `rule` (below) |
| picture pages but no file bytes | `not_run` | `N picture page(s) but no file bytes to render` | `rule` |
| photo / screenshot / image upload, or a `photo`-flagged page | `completed` / `failed` | `null`, or the provider's error class | `rule`, `pages[]` |

`vision.rule` (`picture_rule`) is `{modality, material_type, picture_modalities,
picture_materials, every_page_is_a_picture, pages_probed, pages_flagged_photo,
picture_pages, basis}` — the inputs and the outcome of `picture_pages`, so a
`not_run` is legible without this document.

When the model answers and names **no** fact, the page entry carries a `note`
saying so — how many facts it offered and how many were dropped, whether the
first answer was not JSON and it was asked again, and the first 160
characters of its answer — instead of a bare `facts: []` that reads as "not
asked". A page with facts carries no note.

The question asked depends on the kind and the classified document type
(`analysis_type_for`): a `screenshot` or `image` is a **picture of a
document** (`document_photo`: odometer, plate, VIN sticker); a `photo` under
`auto_claim`/`auto_policy` is an `accident_scene`; under
`property_claim`/`property_policy` it is `property_damage`; otherwise
`generic` (the union of all fact names).

## Rendering and quality — one renderer

Pages are rendered with the PyMuPDF path `quality_probe` already owns
(`_open_document`; `render_page_png`): PDF pages up to a 1568 px long side,
images at their native pixel grid (never upscaled, shrunk to 1568 px when
larger — a 4000 px phone photo would otherwise be ~3 MB of base64 for no
gain, every provider downsamples above that edge).

`picture_quality(png)` runs `quality_probe.probe_visual_quality` on the
rendered PNG and reports **measured numbers only**: `width`, `height`,
`sharpness` (Laplacian variance), `contrast_range`, `contrast_std`, the
probe's flags, and a `status`:

| `status` | Rule |
|---|---|
| `poor` | long side < 480 px (`too_small`), or the probe flagged both `blurry` and `low_contrast`, or the bytes do not render (`not_renderable`) |
| `acceptable` | one of `blurry` / `low_contrast` |
| `good` | neither |

The probe's `low_res` flag is reported but does not drive the status: it is
a DPI estimate that assumes a letter-size page, which a photo is not. A
`poor` page is **not sent to the model**; its entry is `skipped` and the
`reason` quotes the measurement.

## Model and configuration

| Backend (`cost_governance.llm_backend()`) | Variable | Default | Counts as configured when unset? |
|---|---|---|---|
| `ollama` | `ASSURE_OLLAMA_MODEL_VISION` | `qwen2.5vl:3b` | **No** — the tag is not in `ollama-pull`; a stock compose stack would fail every photo with "model not found". Set the variable (after `ollama pull`) or `PARSURE_VISION=1`. |
| `openrouter` (also the legacy `cloud` policies when `OPENROUTER_API_KEY` is set) | `ASSURE_OPENROUTER_MODEL_VISION` | `amazon/nova-lite-v1` (multimodal) | Yes |
| `bedrock` | `ASSURE_BEDROCK_MODEL_VISION` | `ASSURE_BEDROCK_MODEL_DRAFT` (Sonnet), qualified with the region's `eu.`/`us.` inference-profile prefix like every other Bedrock id | Yes |
| `cloud` without an OpenRouter key | — | — | No vision model: `disabled` with that reason |

`PARSURE_VISION`: `0/false/no/off` → `disabled` ("PARSURE_VISION is off");
`1/true/yes/on` → on with the backend's model; unset → on when the table
above says "configured". `PARSURE_VISION_TIMEOUT_S` (default 45, clamped
5–180) bounds one model call; the call runs on a daemon thread and a late
answer is discarded, exactly as `llm_extraction` does.

The call is one `litellm.completion` with a text part and an `image_url`
data-URI part (PNG), through `cost_governance._litellm_api_kwargs` for the
provider key, OpenRouter headers, Ollama `api_base` or Bedrock region. Note:
litellm's Ollama image path needs Pillow — the app image has it (12.3.0,
checked 2026-09-27); a bare host venv without Pillow fails the call with
`APIConnectionError: … please run pip install Pillow`, recorded as `failed`.

**Blind-model guard (Ollama only).** Before any picture is rendered,
`model_cannot_see` asks the daemon's `/api/show` for the tag's
`capabilities` (one GET per tag per process, 3 s timeout). A tag without
`vision` — `qwen2.5:1.5b` and `llama3.2:1b` list `['completion', 'tools']` on
Ollama 0.34.3 — is refused with `status: failed` and that list in `reason`:
a text model handed an image answers from the prompt alone and every "fact"
it named would be invented. A daemon that cannot answer (unknown tag, no
connection) leaves the decision to the call itself, which then fails with
the provider's error. OpenRouter and Bedrock report no such capability
list; their defaults are multimodal and an override is the operator's
responsibility.

## The prompt and the answer

The prompt names the analysis type, lists the **allowed fact names** with a
one-line hint each, demands exactly one JSON object
`{"facts": [{name, value, confidence, evidence, bbox}]}`, requires a
non-empty `evidence` sentence per fact, and says: if nothing can be
determined, answer `{"facts": []}`. Allowed names (plan 5.4):

| Analysis type | Fact names |
|---|---|
| `accident_scene` | `scene_summary`, `vehicle_count_visual`, `vehicles_involved_visual`, `damage_description_visual` |
| `property_damage` | `scene_summary`, `property_condition_visual`, `roof_condition_visual`, `water_damage_visual`, `fire_damage_visual`, `damage_description_visual` |
| `document_photo` | `scene_summary`, `odometer_visual`, `license_plate_visual`, `vin_plate_visual` |
| `generic` | all of the above |

The answer is parsed defensively (code fences, prose around the JSON, a bare
list, `{"facts": null}` = empty). A non-JSON answer is asked **once more**;
a second non-JSON answer is `failed` ("ValueError: model answer was not JSON
after 2 call(s)") with no facts. `normalise_facts` then keeps a fact only
when it has an allowed `name`, a non-empty `value` and a non-empty
`evidence`; everything else is dropped and **counted** in
`pages[].dropped` (`unknown_name`, `no_evidence`, `no_value`, `not_object`,
`duplicate`). `confidence` is the model's own 0–1 number or `null` — it is
never computed by Assure. `bbox` is the model's own claim, accepted as
fractions 0–1 (a 0–1000 grid, Qwen-VL's convention, is scaled down),
otherwise `null`.

## Output shape

```jsonc
report["vision"] = {
  "status": "completed" | "not_run" | "failed" | "disabled",
  "model": "openrouter/amazon/nova-lite-v1" | null,
  "reason": null | "PARSURE_VISION is off" | "no picture pages" | "ConnectionError: …",
  "rule": {"modality": "phone_photo", "material_type": "photo", "picture_modalities": ["phone_photo", "screenshot"],
           "picture_materials": ["image", "photo", "screenshot"], "every_page_is_a_picture": true,
           "pages_probed": 1, "pages_flagged_photo": 0, "picture_pages": 1, "basis": "modality 'phone_photo' / material 'photo' make every page a picture"},
  "ms": 4210,
  "pages": [
    {
      "page": 1, "kind": "photo", "analysis_type": "accident_scene",
      "status": "completed" | "skipped" | "failed",
      "quality": {"status": "acceptable", "flags": ["blurry"], "width": 1568, "height": 1176,
                  "sharpness": 412.3, "contrast_range": 201.0, "contrast_std": 55.2, "basis": "acceptable: one measured flag: blurry. probe: …"},
      "facts": [
        {"name": "damage_description_visual", "value": "front-left bumper cracked and detached",
         "confidence": 0.82, "evidence": "cracked plastic hanging below the left headlight",
         "bbox": [0.05, 0.4, 0.45, 0.9],
         "grounding": {"kind": "image", "page": 1, "bbox": [0.05, 0.4, 0.45, 0.9]},
         "model": "openrouter/amazon/nova-lite-v1"}
      ],
      "dropped": {"unknown_name": 1}, "calls": 1, "reason": null, "ms": 4100
      // "note": "the model answered and named no fact (0 offered, 0 dropped); first answer was not JSON, asked 2 times; answer starts: '{\"facts\": []}'"  — only when facts is empty
    }
  ]
}

report["execution"]["vision"] = {
  "status": "completed", "model": "openrouter/amazon/nova-lite-v1",
  "pages_analyzed": 1,      // pages with status completed
  "facts": 1,               // facts across all pages
  "ms": 4210, "reason": null
}
```

`execution` is created with `setdefault` — the orchestrator owns the block.
Block status: `completed` when every selected picture was processed (a page
may still be `skipped` for quality or `failed`; `reason` then says how
many); `failed` only when no page reached a usable answer and at least one
failed; `not_run` when nothing was a picture or there were no bytes;
`disabled` per the flag/backend.

## What is and is not claimed

- A fact is the model's observation with its own evidence sentence; Assure
  verifies neither. Nothing here is a Z3 verdict, an OCR read or a field.
- Assure does not compute confidence for a fact. `confidence` is the model's
  number or `null`.
- Nothing is analysed when the picture is measured `poor`; the report says
  which measurement.
- Embedded pictures inside text PDFs, handwriting, and skew/glare/noise are
  not detected (the visual probe does not flag them in V1).
- On the local Ollama backend nothing runs until a vision tag is pulled and
  named; the stack's `ollama-pull` does not download one.
- The 45 s per-call timeout is a bound; one live call has been timed (below:
  3.4 s for two Nova Lite calls on one page).
- An empty `facts` list is the model's answer, not Assure's: the `note` quotes
  what came back. Assure never fills a fact the model did not name.

## Measured on 2026-09-27 (this machine, compose stack)

| Step | Figure |
|---|---|
| `render_page_png` 1200×800 PNG → 1200×800 (14 KB) | 13 ms |
| `render_page_png` 4000×3000 JPEG → 1568×1177 (31 KB) | 102 ms |
| `picture_quality` on those PNGs | 17 ms / 26 ms |
| `analyze_page` inside the app container (`ollama/qwen2.5vl:3b`, tag not pulled) | `failed`, `NotFoundError: … model 'qwen2.5vl:3b' not found`, 79 ms; `attach_vision` end to end 96 ms, `execution.vision.status: failed`, no exception |
| `/api/show` on `qwen2.5:1.5b` | `['completion', 'tools']` → refused by the blind-model guard |

On 2026-09-27 no real multimodal answer had been measured (empty
`OPENROUTER_API_KEY`, no vision tag on the local Ollama). The prompt, JSON
handling, grounding and count invariants are covered by `tests/test_vision.py`
with an injected completion. `docs/anti-claims.md` "Vision (2026-09-27)" lists
the sentences the code must not say.

## Measured on 2026-09-28 (compose stack, `ASSURE_LLM_BACKEND=openrouter`)

Why every live report said `not_run`: the eight newest `parsure_reports` rows
were all `modality: digital_pdf`, `parser_name: jdf-cli`, with
`execution.vision = {status: not_run, model: "openrouter/amazon/nova-lite-v1",
reason: "no picture pages (…)"}` — the model had resolved and the rule had
answered correctly; only the wording hid it. No report was `disabled`.

Then a picture was uploaded: page 1 of `/tmp/final_run_debris.pdf` rendered
with PyMuPDF to a 1130×1600 PNG (100 KB) and posted to
`POST /api/projects/vision-live-089ccb/import-pdf` (202, task `545fee4c…`,
worker job `job-289c2b2b82404c1f`):

| Step | Figure |
|---|---|
| ingest end to end (`jdf-cli+tesseract`, OCR confidence 0.944, 1 page) | 22.2 s worker time, 25.5 s to task `success` |
| router | `material_type: image`, `modality: scanned_pdf` (a PNG of a letter page is not screen-shaped, so not `screenshot`; not a JPEG, so not `phone_photo`) → every page a picture, `kind: image`, `analysis_type: document_photo` |
| `picture_quality` | `good` — 1109×1568 px, sharpness 1780.6, contrast range 192, flag `low_res` (the DPI estimate, not used for the status) |
| `analyze_page` with `openrouter/amazon/nova-lite-v1` | `status: completed`, **2 calls** (the first answer was not JSON; the retry was), **3395 ms** for both, `facts: []`, `dropped: {}` |
| `execution.vision` | `{status: completed, model: openrouter/amazon/nova-lite-v1, pages_analyzed: 1, facts: 0, ms: 3395, reason: null}` |

So the model answered and named no fact. Before this change the page entry
showed only `facts: []` and `reason: null`; the `note` added on 2026-09-28
records the empty answer and the retry.

**Second run, same PNG, rebuilt image** (project `photo-cb3791`, report
`pr-edb8a1692fea44b5`): ingest 56.8 s worker time (the worker was also
rastering and the stack was busy), `execution.vision = {status: failed,
pages_analyzed: 0, facts: 0, ms: 4194, reason: "ValueError: model answer was
not JSON after 2 call(s) (starts: '{"facts": [{"name": "vin_plate_visual",
"value": "1HGCM82633A004352", "confidenc')"}` — both answers unparsable this
time. `vision.rule` on the block: `every_page_is_a_picture: true`, basis
"modality 'scanned_pdf' / material 'image' make every page a picture".

**Why not JSON** (probe inside the app container, `vz.default_completion` on
the same PNG, two calls): call 1, 2492 ms, 201 characters —

```
{"facts": [{"name": "vin_plate_visual", "value": "1HGCM82633A004352", "confidence": 1,
  "evidence": "The VIN is clearly printed on the document as '1HGCM82633A004352'.",
  "bbox": [165, 181, 355, 195]]}]}
```

— a well-formed answer except for one stray `]` closing `bbox`, which makes
the whole object invalid JSON; call 2, 1410 ms, `{"facts": []}`. So Nova Lite
does read the page (that VIN is the one printed on the debris PDF and the one
Parsure's field pass extracts) but roughly one answer in two carries a
bracket error, and the run's outcome depends on which of the two attempts the
retry lands on: `completed` with no facts (run 1) or `failed` (run 2). Assure
does not repair the JSON — a bracket "fix" would be Assure's guess at the
model's answer — and reports the failure with the answer's head **and tail**
(the tail was added after this probe so the bracket is visible in `reason`).
A model that returns the object correctly will have its VIN fact kept, with
the model's own `evidence` sentence and `bbox` (Nova Lite answered in pixel
units, which `_clean_bbox` rejects unless the values fit a 0–1000 grid — they
did here, so it would have been scaled).

No claim is made about what Nova Lite would name on a real damage photo:
none was uploaded.
