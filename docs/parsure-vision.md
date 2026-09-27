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
block is `not_run` with that reason. Embedded figures inside text PDFs are
not analysed in V1. At most `PARSURE_VISION_MAX_PAGES` (default 4) pages
are analysed; the rest are listed as `skipped` with the bound in `reason`.

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
  "reason": null | "PARSURE_VISION is off" | "no picture pages (modality 'digital_pdf', no page flagged photo)" | "ConnectionError: …",
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
- The 45 s per-call timeout is a bound, not a measurement: no live vision
  model has been timed yet.

## Measured on 2026-09-27 (this machine, compose stack)

| Step | Figure |
|---|---|
| `render_page_png` 1200×800 PNG → 1200×800 (14 KB) | 13 ms |
| `render_page_png` 4000×3000 JPEG → 1568×1177 (31 KB) | 102 ms |
| `picture_quality` on those PNGs | 17 ms / 26 ms |
| `analyze_page` inside the app container (`ollama/qwen2.5vl:3b`, tag not pulled) | `failed`, `NotFoundError: … model 'qwen2.5vl:3b' not found`, 79 ms; `attach_vision` end to end 96 ms, `execution.vision.status: failed`, no exception |
| `/api/show` on `qwen2.5:1.5b` | `['completion', 'tools']` → refused by the blind-model guard |

Not measured: a real multimodal answer. The compose `.env` has an empty
`OPENROUTER_API_KEY` and the local Ollama holds only `qwen2.5:1.5b` and
`llama3.2:1b`; no multi-GB vision tag was pulled for this check. The prompt,
JSON handling, grounding and count invariants are covered by
`tests/test_vision.py` with an injected completion. `docs/anti-claims.md`
"Vision (2026-09-27)" lists the sentences the code must not say.
