"""The client's counters and the server's, on the same documents.

``prototype/shell.js:_derivedCounts`` re-implements
``services/audit_summary._provenance_counts`` for the browser, because the shell
renders the tree it already has: a first paint, a cold shell and a version jump all
take the derived path. The two drifted once and the number that went missing was the
one the page promises — a paragraph citing ``[yes, no]`` counted unsupported on the
server and not in the browser, because the mirror had no contradicted branch
(SEC-COUNTER-04).

This test runs the *shipped* function: it extracts ``_derivedCounts`` and
``_entailmentFor`` from ``prototype/shell.js`` (brace-balanced, no rewriting) and
executes them in node against the same documents the Python counters see. The two
leaf helpers the function calls are supplied here because they are unrelated to the
rule under test: ``_anchorContentTokens`` as a word count over the fixture text (all
fixtures are far above the floor either way) and ``_ANCHOR_WORD_FLOOR`` as the
server's ``_MIN_CLAIM_TOKENS``.

A change to the rule in either language fails this test, which is the point: the
same document must report the same numbers whichever side derived them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prompt_matrix.services.audit_summary import _provenance_counts, _reported_stats
from prompt_matrix.services.entailment import _aggregate_verdicts, _contradicted

SHELL_JS = Path(__file__).resolve().parents[1] / "prototype" / "shell.js"

CLAIM = "The policy liability limit is five million dollars per occurrence."

#: The buckets the shell reports. ``unchecked`` is the server's detail bucket and is
#: deliberately not mirrored — the shell only counts a verdict it can read.
CLIENT_KEYS = (
    "eligible",
    "anchored",
    "supported",
    "partial",
    "unanchored",
    "unsupported",
    "unverified",
)


def _extract_function(source: str, name: str) -> str:
    """The source text of ``function name(...) { … }``, brace-balanced."""
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after function {name}")


def _paragraph(
    node_id: str,
    *,
    quotes: list[str] | None = None,
    verdict: str | None = None,
    contradicted: bool = False,
) -> dict[str, Any]:
    return {
        "type": "paragraph",
        "id": node_id,
        "content": CLAIM,
        "provenance": [
            {"extracted_quote": quote, "source_name": "policy.pdf", "page": 1}
            for quote in (quotes or [])
        ],
        "meta": {
            "provenance": {
                "entailment": {
                    "verdict": verdict,
                    "reasoning": "stubbed",
                    "contradicted": contradicted,
                }
            }
        }
        if verdict is not None
        else {},
    }


def _document(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": "doc-parity",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {"type": "section", "id": "sec-1", "title": "Coverage", "children": list(nodes), "meta": {}}
        ],
    }


def _client_counts(documents: list[dict[str, Any]]) -> list[dict[str, int]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed, so the browser's counters cannot be executed")
    source = SHELL_JS.read_text(encoding="utf-8")
    harness = "\n".join(
        [
            "var _ANCHOR_WORD_FLOOR = 4;",
            "function _anchorContentTokens(content) {",
            "  return String(content == null ? '' : content).split(/\\s+/).filter(Boolean).length;",
            "}",
            _extract_function(source, "_entailmentFor"),
            _extract_function(source, "_derivedCounts"),
            f"var docs = {json.dumps(documents)};",
            "process.stdout.write(JSON.stringify(docs.map(function (d) { return _derivedCounts(d); })));",
        ]
    )
    result = subprocess.run(
        [node, "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("verdicts", "quotes"),
    [
        (["yes", "yes"], 2),
        (["yes", "partial"], 2),
        (["yes", "no"], 2),
        (["partial", "no"], 2),
        (["no", "no"], 2),
        (["unverified"], 1),
        (["yes", "unverified"], 2),
        (["yes"], 1),
        (None, 1),
        (None, 0),
    ],
    ids=lambda value: (
        "+".join(value) if isinstance(value, list) else f"{value}q" if isinstance(value, int) else str(value)
    ),
)
def test_the_browser_counter_agrees_with_the_server(verdicts, quotes) -> None:
    """Same document, same numbers — the contradicted branch included.

    ``[yes, no]`` is the row that matters: the paragraph is carried by one citation
    and contradicted by another, so the server counts it supported AND unsupported,
    and the browser has to count the same two.
    """
    # The record the server writes for these per-citation verdicts, built by the
    # server's own aggregate: the browser reads the verdict and the flag, not the
    # per-citation list, so that is what it is given.
    record = None
    if verdicts is not None:
        record = {
            "verdict": _aggregate_verdicts(verdicts),
            "contradicted": _contradicted(verdicts),
        }
    doc = _document(
        _paragraph(
            "p1",
            quotes=[f"The limit is five million dollars ({n})." for n in range(quotes)],
            verdict=(record or {}).get("verdict"),
            contradicted=bool((record or {}).get("contradicted")),
        )
    )

    server = _reported_stats(_provenance_counts(doc))
    client = _client_counts([doc])[0]
    assert {key: client[key] for key in CLIENT_KEYS} == {key: server[key] for key in CLIENT_KEYS}


def test_the_browser_counts_a_contradicted_partial_paragraph_as_unsupported() -> None:
    """The exact case SEC-COUNTER-04 lost, stated on its own.

    ``verdict: "partial"`` with ``contradicted: true`` — a summary carried by some of
    what it cites and denied by the rest. Reading only the verdict gives supported 1
    and unsupported 0 in the browser while the server reports unsupported 1, so the
    tile understates the document on a cold shell.
    """
    doc = _document(
        _paragraph("p1", quotes=["a", "b"], verdict="partial", contradicted=True)
    )
    client = _client_counts([doc])[0]
    assert client["partial"] == 1
    assert client["supported"] == 1
    assert client["unsupported"] == 1, "the browser dropped the contradicted citation"


def _client_states(documents: list[dict[str, Any]]) -> list[list[Any]]:
    """``[[node_id, state], …]`` per document, from the shipped `_anchorStateOf`."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed, so the rendered state cannot be executed")
    source = SHELL_JS.read_text(encoding="utf-8")
    harness = "\n".join(
        [
            "var _ANCHOR_WORD_FLOOR = 4;",
            "function _anchorContentTokens(content) {",
            "  return String(content == null ? '' : content).split(/\\s+/).filter(Boolean).length;",
            "}",
            _extract_function(source, "_entailmentFor"),
            _extract_function(source, "_anchorStateOf"),
            f"var docs = {json.dumps(documents)};",
            "var out = docs.map(function (d) {",
            "  return (d.body[0].children || []).map(function (n) { return [n.id, _anchorStateOf(n)]; });",
            "});",
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


#: What each mark means, as the server's own counters report the same document. A
#: reader who sees the mark and the tile reads one document state twice, so the two
#: have to agree; a state that a bucket contradicts is a mark that lies.
_STATE_BUCKET = {
    "supported": lambda s: s["supported"] == 1 and s["unsupported"] == 0,
    "partial": lambda s: s["partial"] == 1 and s["supported"] == 1,
    "unsupported": lambda s: s["unsupported"] == 1,
    "anchored": lambda s: s["anchored"] == 1 and s["supported"] == 0 and s["unsupported"] == 0,
    "unanchored": lambda s: s["unanchored"] == 1,
}


@pytest.mark.parametrize(
    ("verdicts", "quotes"),
    [
        (["yes"], 1),
        (["partial"], 1),
        (["no"], 1),
        (["yes", "no"], 2),
        (["partial", "no"], 2),
        (["unverified"], 1),
        (["yes", "unverified"], 2),
        (None, 1),
        (None, 0),
    ],
)
def test_the_mark_a_paragraph_wears_agrees_with_the_bucket_it_is_counted_in(
    verdicts, quotes
) -> None:
    """One verdict, one state, one bucket — the contradiction included.

    `_anchorStateOf` decides the mark a paragraph wears in the document. It used to
    map `yes` to supported, `partial` to partial, and *every other verdict* to the
    grey `anchored` — so a paragraph the source check denied rendered exactly like one
    nobody had checked, while the counters beside it counted the denial in
    `unsupported`. That is the defect: the reader could not tell "not checked" from
    "checked and denied" on the paragraph itself. `(["no"], 1)` is the row that fails
    pre-fix; `(["partial", "no"], 2)` is the same loss on a contradiction flag.
    """
    record = None
    if verdicts is not None:
        record = {
            "verdict": _aggregate_verdicts(verdicts),
            "contradicted": _contradicted(verdicts),
        }
    doc = _document(
        _paragraph(
            "p1",
            quotes=[f"The limit is five million dollars ({n})." for n in range(quotes)],
            verdict=(record or {}).get("verdict"),
            contradicted=bool((record or {}).get("contradicted")),
        )
    )
    state = _client_states([doc])[0][0][1]
    server = _reported_stats(_provenance_counts(doc))
    assert state in _STATE_BUCKET, f"unknown mark {state!r}"
    assert _STATE_BUCKET[state](server), (
        f"the paragraph wears {state!r} while the server counts it "
        f"anchored={server['anchored']} supported={server['supported']} "
        f"partial={server['partial']} unsupported={server['unsupported']} "
        f"unanchored={server['unanchored']}"
    )
