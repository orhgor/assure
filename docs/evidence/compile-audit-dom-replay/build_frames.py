"""Build the DOM-replay frame scripts for Phase 2.

CASE A — the refused ("doomed") compile. Frames are the REAL captured bytes
(/tmp/domreplay/caseA.sse, 2911 bytes, project audit-d-table-b3e579). Non-token
frames keep their recorded arrival t; token frames are paced to the recorded
cumulative streamed-draft lengths (0@0s, 62@1s, 330@2s, end 2.493s).

CASE B — the compile that completes ok:true over a document the entailment layer
contradicted. Token frames carry the REAL 2569-char draft (sha256 9dfbfef5…,
recovered from the box's own pipeline_cache), paced to the recorded cumulative
lengths (213@0s … 2569@6s). Control frames are the recorded frame list of
run 1 of the determinism series (artifact 8444), including the `verified` payload
with unverified=True and the `complete` frame with ok=True.
"""
import json
import re

OUT = "/tmp/domreplay/replay.json"
CASE_A_SSE = "/tmp/domreplay/caseA.sse"
DRAFT_B = "/tmp/domreplay/naic_draft.txt"


def frames_of(raw: bytes):
    return [b + "\n\n" for b in raw.decode("utf-8").split("\n\n") if b.strip()]


def ev_of(block: str) -> str:
    return next((l[6:].strip() for l in block.split("\n") if l.startswith("event:")), "message")


def data_of(block: str):
    lines = [l[5:].strip() for l in block.split("\n") if l.startswith("data:")]
    j = "\n".join(lines)
    if j == "[DONE]":
        return {"_raw": "[DONE]"}
    try:
        return json.loads(j)
    except Exception:
        return None


def pace(token_blocks, marks, end_t):
    """Assign each token frame a send time so cumulative chars hit `marks`.

    marks: [(t, cumulative_chars)] ascending. Returns [(t, block)].
    """
    deltas = []
    for b in token_blocks:
        d = data_of(b)
        deltas.append(len(str((d or {}).get("delta") or "")))
    total = sum(deltas)
    out, cum, i = [], 0, 0
    for d in deltas:
        cum += d
        # first mark whose cumulative >= cum
        while i + 1 < len(marks) and marks[i][1] < cum:
            i += 1
        t = marks[i][0] if marks[i][1] >= cum else end_t
        # interpolate within the segment for sub-second smoothness
        lo = marks[i - 1] if i > 0 else (0.0, 0)
        if marks[i][1] > lo[1]:
            frac = (cum - lo[1]) / float(marks[i][1] - lo[1])
            t = lo[0] + frac * (marks[i][0] - lo[0])
        out.append((round(min(t, end_t), 3), None))
    # attach blocks
    return [(t, b) for (t, _), b in zip(out, token_blocks)], total


def pace_from_order(token_blocks, start_t, end_t):
    """Spread token frames over [start_t, end_t] in stream order.

    Per-frame arrival times are not recoverable from a buffered capture (a single
    read() can carry many frames), so the tokens are paced proportionally to their
    own character counts inside a window that sits where the real token window sat.
    This preserves the frame ORDER exactly as captured, which is what the DOM
    questions turn on.
    """
    deltas = [len(str((data_of(b) or {}).get("delta") or "")) for b in token_blocks]
    total = sum(deltas)
    out, cum = [], 0
    for b, d in zip(token_blocks, deltas):
        cum += d
        t = start_t + (end_t - start_t) * (cum / float(total or 1))
        out.append((round(t, 3), b))
    return out, total


def build_a():
    raw = open(CASE_A_SSE, "rb").read()
    blocks = frames_of(raw)
    T = {
        "preflight": 0.052, "model": 0.054, "usage1": 1.596, "locks": 1.596,
        "usage2": 2.482, "redhat": 2.482, "error": 2.493, "complete": 2.493, "done": 2.493,
    }
    tok = [b for b in blocks if ev_of(b) == "token"]
    non = [b for b in blocks if ev_of(b) != "token"]
    paced, total = pace_from_order(tok, 0.20, 1.55)
    assert total == 330, total
    seq = []
    ni = 0
    for b in non:
        e = ev_of(b)
        if e == "status":
            key = "preflight" if "preflight" in b else ("locks" if "locks" in b else "model")
            seq.append((T[key], b))
        elif e == "usage":
            ni += 1
            seq.append((T["usage1"] if ni == 1 else T["usage2"], b))
        elif e == "redhat":
            seq.append((T["redhat"], b))
        elif e == "error":
            seq.append((T["error"], b))
        elif e == "complete":
            seq.append((T["complete"], b))
        else:
            seq.append((T["done"], b))
    seq += paced
    seq.sort(key=lambda x: x[0])
    return [{"t": t, "text": b} for t, b in seq], total


def build_b():
    draft = open(DRAFT_B, encoding="utf-8").read()
    # recorded token window: first token t=0.498s, last t=6.613s; recorded cumulative
    # streamed lengths 527@1s, 1057@2s, 1365@3s, 1823@4s, 2337@5s, 2569@6s
    marks = [(0.498, 0), (1.0, 527), (2.0, 1057), (3.0, 1365), (4.0, 1823), (5.0, 2337), (6.0, 2569)]
    n = 114
    size = len(draft) / float(n)
    pieces = [draft[int(i * size):int((i + 1) * size)] for i in range(n)]
    pieces = [p for p in pieces if p]
    tok_blocks = [
        'event: token\ndata: ' + json.dumps({"type": "token", "delta": p}, ensure_ascii=False) for p in pieces
    ]
    paced, total = pace(tok_blocks, marks, 6.613)
    assert total == len(draft), (total, len(draft))
    # body for the compiled/verified frames — the real draft, as sections
    parts = re.split(r"\n(?=## )", draft.strip())
    body = []
    for p in parts:
        lines = [ln for ln in p.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        title = lines[0][3:].strip() if lines[0].startswith("## ") else "Section"
        text = " ".join(ln.strip() for ln in lines[1:] if not ln.startswith("#")).strip()
        body.append({
            "type": "section", "id": f"sec-{len(body)}", "title": title,
            "children": [{
                "type": "paragraph", "id": f"para-{len(body)}", "content": text,
                "meta": {"provenance": {"entailment": {
                    "verdict": "no", "reasoning": "recorded: contradicted by source",
                    "model": "qwen/qwen3-next-80b-a3b-instruct"}}},
                "annotations": {},
            }],
            "meta": {}, "annotations": {},
        })
    doc = {"document_id": "doc-audit-a-naic-7f8409", "meta": {"answer_shape": "memo"},
           "body": body}
    ctrl = [
        (0.019, 'event: status\ndata: {"type": "status", "stage": "preflight", "message": "Checking budget…"}'),
        (0.021, 'event: status\ndata: {"type": "status", "stage": "model", "message": "Drafting with openrouter/qwen/qwen3-next-80b-a3b-instruct…", "model": "openrouter/qwen/qwen3-next-80b-a3b-instruct"}'),
        (6.632, 'event: usage\ndata: ' + json.dumps({"type": "usage", "input_tokens": 1302, "output_tokens": 507, "model_id": "openrouter/qwen/qwen3-next-80b-a3b-instruct", "task_type": "draft_compile", "measure": {"model": "openrouter/qwen/qwen3-next-80b-a3b-instruct", "input_tokens": 1333, "output_tokens": 513, "cache_read": 0, "duration_ms": 6608, "usd": None}}, ensure_ascii=False)),
        (6.632, 'event: status\ndata: {"type": "status", "stage": "locks", "message": "Inferring locks…"}'),
        (7.348, 'event: usage\ndata: {"type": "usage", "input_tokens": 507, "output_tokens": 86, "model_id": "deepseek/deepseek-chat", "task_type": "summarize_node"}'),
        (7.348, 'event: redhat\ndata: ' + json.dumps({"type": "redhat", "redhat": {"status": "skipped", "findings_count": 0, "error": None, "skip_reason": "no Red-Hat audit was requested for this compile"}, "status": "skipped", "findings_count": 0, "error": None, "skip_reason": "no Red-Hat audit was requested for this compile"}, ensure_ascii=False)),
        (7.391, 'event: compiled\ndata: ' + json.dumps({"type": "compiled", "document": doc, "nodes": body, "locks": [], "node_count": 3, "lock_count": 1, "draft_text": draft}, ensure_ascii=False)),
        (7.391, 'event: status\ndata: {"type": "status", "message": "Running Math Check…"}'),
        (7.393, 'event: status\ndata: {"type": "status", "stage": "entailment", "message": "Verifying anchored claims against their sources…"}'),
        (9.241, 'event: verified\ndata: ' + json.dumps({"type": "verified", "ok": False, "gate_status": "review", "z3_status": "SKIPPED", "unverified": True, "unverified_reason": "0 of 5 claims were entailed by their matched source sentence (3 contradicted by their source).", "provenance_stats": {"eligible": 5, "anchored": 3, "supported": 0, "partial": 0, "unsupported": 3, "unanchored": 2, "unverified": 0}, "document": doc}, ensure_ascii=False)),
        (9.260, 'event: complete\ndata: {"type": "complete", "ok": true, "node_count": 3, "lock_count": 1}'),
        (9.260, 'data: [DONE]'),
    ]
    seq = ctrl + paced
    seq.sort(key=lambda x: x[0])
    return [{"t": t, "text": b} for t, b in seq], total


def main():
    a, ta = build_a()
    b, tb = build_b()
    for seq in (a, b):
        for f in seq:
            if not f["text"].endswith("\n\n"):
                f["text"] = f["text"].rstrip("\n") + "\n\n"
    json.dump({"A": a, "B": b}, open(OUT, "w"))
    print(f"case A: {len(a)} frames, final draft {ta} chars, ends t={a[-1]['t']}s")
    print(f"case B: {len(b)} frames, final draft {tb} chars, ends t={b[-1]['t']}s")


if __name__ == "__main__":
    main()
