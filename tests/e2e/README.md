# Assure prototype shell — cross-layer honesty suite

These tests require both local servers running:

- port 8899: `prompt_matrix.web` (the Flask API)
- port 8990: `prototype/dev-server.py` (shell, proxies `/api/*` → 8899)

They assert layer agreement (UI, SSE, DB, PDF). If they fail, the app is
lying about something — one layer reported a status another layer did not.
Do not fix by loosening the assertion; fix the layer that disagrees.

Run: `.venv/bin/python -m pytest tests/e2e/ -v -m e2e`

## SSE capture is best-effort

The conftest `capture_verified_event` helper reads the `/draft/stream`
response body. Playwright can evict the body if the page navigates or if
the response is not read synchronously. When it succeeds, it provides the
strongest cross-layer assertion. When it fails, DB + PDF together still
prove the honesty claim (SSE is a duplicate source for the same persisted
value).

If SSE capture fails, do NOT skip or fail the test — drop the SSE assertion
in that test and rely on DB == PDF. The three intra-layer tests
(`test_z3_pass_is_earned`, `test_banner_matches_anchored`,
`test_export_without_gate_is_honest`) still exercise SSE.
