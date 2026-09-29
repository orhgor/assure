"""The vision leg's model binding and its ledger stage (customer finding V-A /
V-C, 2026-09-29).

A reviewer read ``vision.py``'s ``lambda p, b: default_completion(p, b,
model=resolved)`` as a call into ``llm_extraction.default_completion`` (one
positional argument, no ``model``) and concluded every image page fails with a
TypeError. The lambda binds ``vision.default_completion(prompt, png, *,
model)`` — the module's own multimodal call. This test drives ``analyze_page``
through that exact binding with litellm replaced at the boundary, so the
binding is exercised, not assumed; and it pins the ledger stage the call is
booked under.
"""

from __future__ import annotations

import json

import fitz

from prompt_matrix.services import model_calls as mc
from prompt_matrix.services import vision


def _png() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 48), False)
    pix.clear_with(180)
    return pix.tobytes("png")


def test_analyze_page_reaches_the_multimodal_call_through_the_module_binding(monkeypatch):
    seen: dict = {}

    name = sorted(vision.FACT_NAMES["generic"])[0]

    class _Msg:
        content = json.dumps([{"name": name, "value": "yes", "evidence": "a silver sedan with a crumpled bonnet"}])

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    def fake_completion(**kwargs):
        seen["model"] = kwargs.get("model")
        seen["stage"] = (kwargs.get("metadata") or {}).get("assure", {}).get("stage")
        parts = kwargs["messages"][0]["content"]
        seen["has_image"] = any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,") for p in parts)
        return _Resp()

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(vision, "model_cannot_see", lambda model: None)
    out = vision.analyze_page(_png(), analysis_type="generic", model="openrouter/amazon/nova-lite-v1", timeout_s=10)
    assert out["status"] == "completed", out
    assert out["calls"] == 1 and out["facts"] and out["facts"][0]["evidence"].startswith("a silver sedan")
    assert seen["model"] == "openrouter/amazon/nova-lite-v1" and seen["has_image"] is True
    assert seen["stage"] == "vision"  # V-C: the call is booked under its stage
    assert "TypeError" not in json.dumps(out)


def test_vision_default_completion_signature_is_the_multimodal_one():
    import inspect

    sig = inspect.signature(vision.default_completion)
    assert list(sig.parameters) == ["prompt", "image_png_bytes", "model"]
    assert sig.parameters["model"].kind is inspect.Parameter.KEYWORD_ONLY


def test_redhat_graph_default_completion_is_booked_under_its_stage(monkeypatch):
    from prompt_matrix.services import redhat_graph as rg

    seen: dict = {}

    class _Gov:
        executor = None

        def policy_for(self, _t):
            class P:
                litellm_model = "openrouter/meta-llama/llama-3.3-70b-instruct"
                model_id = "llama"
                max_output_tokens = 64
                caching = False

            return P()

        def _default_executor(self, model, messages, max_out, caching):
            seen["stage"] = mc.current_context().get("stage")
            seen["project"] = mc.current_context().get("project_id")
            return "[]", 10, 2

        def record_usage(self, *a, **k):
            pass

    class _CG:
        CostGovernor = _Gov

        class TaskType:
            REDHAT = "redhat"

    monkeypatch.setattr(rg, "_governor", lambda: _CG)
    assert rg.default_completion("critique this", project_id="p-9") == "[]"
    assert seen == {"stage": "redhat_graph", "project": "p-9"}
