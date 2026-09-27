# Parsure integrity — snapshot, hash gate, stable document id, bounded reruns

Added 2026-09-27 from the customer engineering handoff
(`todos/assure_final_engineering_handoff.md`: "State Drift Across Artifacts",
"document_id Is Null", "Rerun Thrash", "Backend Must Enforce"). Code:
`prompt_matrix/services/snapshot.py`, `db/parsure_repository.py`
(`save_report` / `update_report`), `routers/parsure_routes.py` (exports,
`GET …/parsure/<report_id>`, classification override, `POST …/replay`),
`services/verification_dossier.py` + `routers/export_routes.py` (dossier PDF,
`verification_state.json`, bundle), `services/v1_orchestrator.py`
(`assign_document_id`, `record_rerun`, `rerun_stop_rule`, `replay_proof`).
Tests: `tests/test_parsure_integrity.py`.

## 1. The canonical run snapshot

The stored row `parsure_reports.report_json` **is** the snapshot. Every
artifact — the JSON API, the record page, the per-report and project CSVs, the
dossier PDF and its `verification_state.json` twin — is derived from that row
and nothing else. On every write the repository stamps the row with a hash of
itself:

```json
"snapshot": {
  "algorithm": "sha256/canonical-json-v1",
  "content_hash": "3f9c…",              // sha256 of canonical_json(report)
  "stamped_at": "2026-09-27T09:12:44+00:00",
  "policy_versions": {
    "schema_version": "1.0", "policy_version": "v1", "node_id_policy": "eid-v1",
    "redhat_policy": "rh-graph-v1", "parser_version": "0.2.3", "verification_version": "…"
  }
}
```

`canonical_json` = the report minus `VOLATILE_KEYS`, serialised with
`sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str` — the
same serialisation the row uses, so the hash of the in-memory dict at save
time equals the hash of the row read back (tested).

### What is excluded, and why

| Key | Why it is not hashed |
|---|---|
| `snapshot` | cannot contain its own hash |
| `updated_at`, `created_at` | row metadata rewritten by `update_report` / read back from a `DATETIME` column at seconds precision (`_ts`) |
| `_page_texts`, `_page_quality`, `_layout` | private working keys `public_report` strips from every artifact; evidence for a re-read, not the exported contract |
| `timings_ms` | wall-clock measurements; two identical results in 812 ms and 830 ms are the same artifact |

Everything else — fields, classification, conflicts, `redhat`, `replay`
(including its history), `document_id` — is hashed.

`policy_versions` travel with the hash so a mismatch can be told apart from a
legitimate re-stamp under a newer policy.

## 2. The artifact hash gate

Before any export, the stored report is recomputed and compared with its
stamped hash. On mismatch the answer is **409** and **nothing** is written:

```json
{"ok": false, "error": "artifact integrity: report snapshot mismatch",
 "report_id": "pr-…", "expected": "<stored hash>", "actual": "<recomputed hash>"}
```

Gated paths:

* `GET /api/projects/<id>/parsure/<report_id>/export?format=json|csv`
* `GET /api/projects/<id>/parsure/export` (json, csv long, csv wide, with any
  filter) — all-or-nothing over the project's reports: the first mismatch
  refuses the whole file, never a file with one row missing.
* `GET /api/projects/<id>/export?format=dossier-pdf` and `format=bundle` —
  `verification_dossier.build_verification_state` checks every intake report
  it would print (`_require_intact`) before the HTML, PDF, JSON twin or zip is
  built. `export_routes` answers the same 409 body.

A report with **no** `snapshot` block (saved before 2026-09-27) is refused the
same way (`expected: null`): an unstamped row proves nothing. Any repository
write (accept, correct, dispute, override, replay) re-stamps it.

`GET /api/projects/<id>/parsure/<report_id>` still returns a mismatched report
— the reviewer must be able to see what the row holds — with
`"integrity": {"ok": false, "expected": …, "actual": …}`.

No `exported` audit event is written for a refused export.

### Where the hash appears in the artifacts

| Artifact | Where |
|---|---|
| per-report JSON | top-level `snapshot` (the report's own block) |
| per-report CSV | column `snapshot_hash` on every row |
| project JSON | top-level `snapshot: {algorithm, reports: [{report_id, document_id, content_hash}]}` and `documents[].snapshot: {algorithm, content_hash}` |
| project CSV long | column `snapshot_hash` (last) |
| project CSV wide | column `snapshot_hash` after `needs_review`, before the field columns |
| `verification_state.json` | `intake_snapshots: [{report_id, document_id, content_hash}]`; also `intake.snapshots` and `intake.documents[].content_hash` |
| dossier PDF / HTML | "Intake report snapshots" table in §1 Summary: document, report id, document id, snapshot hash |
| `exported` audit event | `payload.snapshot_hash` |

## 3. Stable `document_id`

`v1_orchestrator.assign_document_id` runs right after `build_report`:

| `document_id_source` | When | Value |
|---|---|---|
| `ingest` | the pipeline gave one (saved revision, Sources vault row) | as given |
| `content_hash` | no id, bytes available | `doc-` + SHA-256(bytes)[:16] |
| `generated` | no id, no bytes | `doc-` + uuid4 hex[:16] |

The content-derived id makes a re-upload of the same file the same document
across reports and exports; `list_reports(current_only=True)` dedupes on it,
so two uploads of the same bytes are one row in every list and export. The
id is persisted into the `document_id` column. `routers/substrate.py` and
`services/pdf_ingest.py` both pass `file_bytes` to `run_after_parse`.

Export rows never carry an empty document id: a legacy report with none gets
`doc-` + SHA-256(report_id)[:16] in `export_documents` (the row still keys the
document; it is not content-derived and is not written back).

## 4. Bounded, auditable reruns

A *rerun* is a re-extraction over the stored page texts:
a classification override that re-extracts (`trigger: classification_override`)
or a replay (`trigger: replay`). `report["replay"]`:

```json
"replay": {
  "eligible": true, "reasons": ["…"],
  "replayed": true,
  "attempts": 2, "max_attempts": 3,
  "stop_rule": null | "max_attempts: 3 of 3 reruns used" | "no_improvement: the last 2 reruns did not raise fields_found",
  "last_proof": { … see §5 … },
  "history": [
    {"at": "…", "trigger": "classification_override", "policy_version": "v1", "node_id_policy": "eid-v1",
     "redhat_policy": "rh-graph-v1", "fields_found_before": 10, "fields_found_after": 2, "improved": false,
     "snapshot_before": "<hash of the report before the rerun>", "snapshot_after": "<hash as the rerun left it>"}
  ]
}
```

Rules (`RERUN_MAX_ATTEMPTS = 3`, `RERUN_NO_IMPROVEMENT_RUNS = 2`, versioned
with `POLICY_VERSION`):

* **max_attempts** — a report is rerun at most 3 times.
* **no_improvement** — never again after two consecutive reruns whose
  `fields_found_after` did not exceed `fields_found_before`.

`rerun_stop_rule` reads the append-only `history` (attempts =
`len(history)`), so editing the counter does not reset the bound. When a rule
fires, both the replay endpoint and a re-extracting override answer 409
`{"ok": false, "error": "rerun refused — <rule>", "stop_rule": "<rule>",
"replay": {…}}` and change nothing; an override on a report saved without
page text only renames the type, is not a rerun and is not counted.

`snapshot_after` is the hash of the report as the rerun left it, *before* the
history entry was appended — an entry cannot contain the hash of a report
that contains the entry. The row's own `snapshot` is the hash with the entry.

## 5. Replay proof

`POST /api/projects/<id>/parsure/<report_id>/replay` (body optional:
`{"actor": "…"}`) re-runs `reextract_for_type(report, current type,
verification=stored summary, by_evidence=True, llm=False)` over
`_page_texts` — label pass and policy only, no model call, no Z3 re-run (the
stored `verification.z3_status` tells the policy whether Z3 ran) — and
compares every field's `(value, element_id, field_state)` before and after:

```json
"proof": {
  "deterministic": true, "fields_identical": 12, "fields_total": 12, "changed": [],
  "compared": ["name", "value", "element_id", "field_state"],
  "snapshot_before": "…", "snapshot_after": "…", "at": "…", "document_type": "auto_policy"
}
```

Response `{ok, report, proof}`; the proof is stored as `replay.last_proof`,
`replay.replayed = true`, one `history` entry (`trigger: replay`), one
`replayed` audit event with the proof. The re-extracted fields replace the
report's fields: a reviewer's accepted or corrected state the rerun does not
reproduce appears in `changed` rather than being silently kept. Refusals: 404
unknown report; 409 no stored page text; 409 stop rule (§4).

Measured 2026-09-27 (`tests/test_parsure_integrity.py`): the one-page
declarations fixture replays deterministic (12/12 identical); three overrides
reach `max_attempts` and the fourth override and any replay are refused; two
replays without improvement stop the third.

## 6. Party-name conflicts (Red-Hat recall)

`field_extractor.cross_document_conflicts` compares `insured_name` and
`claimant_name` as one key (`PARTY_NAME_FIELDS`): a policy's insured and a
claim's claimant that differ are one `cross_document` conflict. The entry
carries `field` (the single name when all values came from one field, else
`insured_name`), `fields` (every contributing name), `report_ids`, and
`values[]` each with its `field`. Whitespace/hyphen/case differences are not
conflicts; `schema_mismatch` values are not compared.
