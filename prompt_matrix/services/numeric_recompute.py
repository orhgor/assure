"""Deterministic figure and arithmetic check for one claim against its verbatim evidence.

No model is consulted. The customer policy (2026-09-27) requires every number a
draft states to be found in the supplied source and every calculation to be
recomputed independently — from the source's figures, never from the draft's own.
This module does exactly that, for a fixed list of shapes, and says so in its
result; a shape it does not recognise is ``not_applicable``, never a silent pass.

Figures recognised (``extract_figures``): money (``$1,250.00``, ``USD 1,250``,
``$5 million``), percentages (``12%``, ``12 %``, ``12 percent``), plain numbers
with thousands separators, and dates in ``MM/DD/YYYY`` (read as US month-first),
``YYYY-MM-DD`` and ``Month DD, YYYY`` / ``DD Month YYYY`` forms. Cross-references
(``Section 4``, ``Page 12``, ``§ 3``), hyphenated identifiers (``POL-2025-00123``)
and citation markers are not figures. Word numbers ("twelve percent") are not
recognised — a claim written only in words has no figure to check here.

Arithmetic recomputed (``recompute``), when the operands appear in the evidence:

* sums   — "X and Y total Z", "Z in total (X + Y)", listed amounts against a
           stated total (any figure equal to the sum of the others, with a
           total keyword present);
* shares — "X of Y (Z%)" and "Z% of Y is X";
* deltas — "increased/decreased by Z from X to Y" (Z absolute or percent, and
           the stated direction must hold);
* spans  — two dates and "N months/years/days";
* labels — a figure the claim states under a label word ("deductible $25,000")
           that the evidence states under the same label with another value.

Tolerance: percentages ±0.5 point, money and counts exact to the cent, day spans
exact, month/year spans rounded to the nearest whole unit. Nothing beyond these
shapes is claimed: a calculation this module does not recognise is reported as
``not_applicable`` with its figures still required to appear in the evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_SCALES = {
    "thousand": Decimal(1_000),
    "k": Decimal(1_000),
    "million": Decimal(1_000_000),
    "m": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "b": Decimal(1_000_000_000),
}

_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_DATE_RE = re.compile(
    rf"\b(?P<us>\d{{1,2}}/\d{{1,2}}/\d{{4}})\b"
    rf"|\b(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})\b"
    rf"|\b(?P<mdy>(?:{_MONTH_ALT})\.?\s+\d{{1,2}},?\s+\d{{4}})\b"
    rf"|\b(?P<dmy>\d{{1,2}}\s+(?:{_MONTH_ALT})\.?,?\s+\d{{4}})\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    rf"(?:\$|USD\s?|EUR\s?|GBP\s?|€|£)\s?(?P<num>{_NUMBER})(?:\s?(?P<scale>thousand|million|billion|bn|mm|k|m|b)\b)?"
    rf"|(?P<num2>{_NUMBER})\s?(?P<scale2>thousand|million|billion)?\s?(?:dollars|USD|EUR|GBP)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(rf"(?P<num>{_NUMBER})\s?(?:%|percent\b|per\s+cent\b|pct\b)", re.IGNORECASE)
_PLAIN_RE = re.compile(rf"(?<![\w.])(?P<num>{_NUMBER})(?:\s?(?P<scale>thousand|million|billion)\b)?(?![\w])")
_REF_RE = re.compile(
    r"(?:\b(?:Sections?|Articles?|Clauses?|Exhibits?|Appendi(?:x|ces)|Schedules?|"
    r"Paragraphs?|Pages?|Items?|Nos?|Forms?|Endorsements?)\.?|§|#)\s*$",
    re.IGNORECASE,
)
_ID_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}|\b[A-Z]{2,}-?\d{3,}\b|\[S\d+\]")
_LIST_MARKER_RE = re.compile(r"^[ \t]*\d+[.)][ \t]", re.MULTILINE)

_TOTAL_WORDS_RE = re.compile(
    r"\b(?:total(?:s|ling|ing|led)?|in total|sum|altogether|combined|aggregate|amounts? to|comes? to)\b",
    re.IGNORECASE,
)
_UNIT_WORDS_RE = re.compile(r"\b(months?|years?|days?)\b", re.IGNORECASE)
_UP_WORDS = ("increase", "increased", "rose", "grew", "grown", "up", "rise", "growth", "gain", "gained")
_DOWN_WORDS = (
    "decrease",
    "decreased",
    "fell",
    "declined",
    "decline",
    "reduced",
    "reduction",
    "dropped",
    "drop",
    "down",
    "cut",
)

PERCENT_TOLERANCE = Decimal("0.5")


@dataclass(frozen=True)
class Figure:
    """One figure in a text. ``value`` is a ``Decimal`` for quantities, an ISO
    string for dates. ``kind`` is ``money`` | ``percent`` | ``number`` | ``date``."""

    kind: str
    value: Any
    text: str
    start: int
    end: int

    def same_value(self, other: "Figure") -> bool:
        if self.kind == "date" or other.kind == "date":
            return self.kind == other.kind and self.value == other.value
        return _dec_eq(self.value, other.value)


def _dec(raw: str, scale: str | None = None) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if scale:
        value *= _SCALES.get(scale.lower(), Decimal(1))
    return value


def _dec_eq(a: Any, b: Any) -> bool:
    try:
        return Decimal(a) == Decimal(b)
    except (InvalidOperation, TypeError, ValueError):
        return False


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_date(match: re.Match) -> str | None:
    if match.group("us"):
        m, d, y = match.group("us").split("/")
        return _iso(int(y), int(m), int(d))
    if match.group("iso"):
        y, m, d = match.group("iso").split("-")
        return _iso(int(y), int(m), int(d))
    text = match.group("mdy") or match.group("dmy")
    parts = re.findall(r"[A-Za-z]+|\d+", text)
    if len(parts) != 3:
        return None
    if parts[0].isalpha():
        month_name, day, year = parts
    else:
        day, month_name, year = parts
    month = _MONTHS.get(month_name.lower().rstrip("."))
    if month is None:
        return None
    return _iso(int(year), month, int(day))


def _overlaps(start: int, end: int, taken: list[tuple[int, int]]) -> bool:
    return any(a < end and start < b for a, b in taken)


def extract_figures(text: str) -> list[Figure]:
    """Every figure in ``text``, in order, each span claimed once.

    Dates are taken first so ``01/15/2025`` is one date and not three numbers, then
    money, then percentages, then plain numbers. A plain number preceded by a
    reference keyword (``Section 4``), inside an identifier, or standing as a list
    marker at the start of a line is not a figure.
    """
    raw = str(text or "")
    if not raw:
        return []
    taken: list[tuple[int, int]] = [(m.start(), m.end()) for m in _LIST_MARKER_RE.finditer(raw)]
    out: list[Figure] = []

    # Dates before identifiers: ``2025-01-15`` is a date, and the hyphenated-id
    # filter would otherwise read it as an identifier and drop it.
    for match in _DATE_RE.finditer(raw):
        if _overlaps(match.start(), match.end(), taken):
            continue
        iso = _parse_date(match)
        if iso is None:
            continue
        out.append(Figure("date", iso, match.group(0), match.start(), match.end()))
        taken.append((match.start(), match.end()))
    taken += [
        (m.start(), m.end())
        for m in _ID_RE.finditer(raw)
        if not _overlaps(m.start(), m.end(), taken)
    ]

    for match in _MONEY_RE.finditer(raw):
        if _overlaps(match.start(), match.end(), taken):
            continue
        num = match.group("num") or match.group("num2")
        scale = match.group("scale") or match.group("scale2")
        value = _dec(num, scale)
        if value is None:
            continue
        out.append(Figure("money", value, match.group(0), match.start(), match.end()))
        taken.append((match.start(), match.end()))

    for match in _PERCENT_RE.finditer(raw):
        if _overlaps(match.start(), match.end(), taken):
            continue
        value = _dec(match.group("num"))
        if value is None:
            continue
        out.append(Figure("percent", value, match.group(0), match.start(), match.end()))
        taken.append((match.start(), match.end()))

    for match in _PLAIN_RE.finditer(raw):
        if _overlaps(match.start(), match.end(), taken):
            continue
        if _REF_RE.search(raw[max(0, match.start() - 16) : match.start()]):
            continue
        value = _dec(match.group("num"), match.group("scale"))
        if value is None:
            continue
        out.append(Figure("number", value, match.group(0), match.start(), match.end()))
        taken.append((match.start(), match.end()))

    out.sort(key=lambda fig: fig.start)
    return out


def _present(fig: Figure, evidence: list[Figure]) -> bool:
    return any(fig.same_value(other) for other in evidence)


def _fmt(value: Any) -> str:
    if isinstance(value, Decimal):
        value = value.normalize()
        return f"{value:f}"
    return str(value)


def _template(text: str, figures: list[Figure]) -> str:
    """``text`` with each figure replaced by ``<n>`` (its index), for shape regexes."""
    out: list[str] = []
    cursor = 0
    for index, fig in enumerate(figures):
        out.append(text[cursor : fig.start])
        out.append(f"<{index}>")
        cursor = fig.end
    out.append(text[cursor:])
    return "".join(out)


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def _check_shares(template: str, figures: list[Figure]) -> list[dict[str, Any]]:
    """``X of Y (Z%)`` and ``Z% of Y is X``."""
    checks: list[dict[str, Any]] = []
    for match in re.finditer(r"<(\d+)> of <(\d+)>\s*\(\s*<(\d+)>\s*\)", template):
        x, y, z = (figures[int(match.group(i))] for i in (1, 2, 3))
        if z.kind != "percent" or "date" in (x.kind, y.kind) or y.value == 0:
            continue
        expected = (x.value / y.value * 100).quantize(Decimal("0.01"))
        checks.append(_share_check(x, y, z, expected))
    for match in re.finditer(
        r"<(\d+)> of <(\d+)>\s*(?:is|are|equals|=|,|amounts? to|comes? to|totals?|which is)\s*<(\d+)>",
        template,
    ):
        z, y, x = (figures[int(match.group(i))] for i in (1, 2, 3))
        if z.kind != "percent" or "date" in (x.kind, y.kind) or y.value == 0:
            continue
        expected = (x.value / y.value * 100).quantize(Decimal("0.01"))
        checks.append(_share_check(x, y, z, expected))
    return checks


def _share_check(x: Figure, y: Figure, z: Figure, expected: Decimal) -> dict[str, Any]:
    ok = _within(expected, z.value, PERCENT_TOLERANCE)
    return {
        "shape": "share",
        "ok": ok,
        "operands": [x.text, y.text],
        "stated": _fmt(z.value) + "%",
        "expected": _fmt(expected) + "%",
        "detail": f"{x.text} of {y.text} is {_fmt(expected)}%, stated {z.text}",
    }


def _check_deltas(text: str, template: str, figures: list[Figure]) -> list[dict[str, Any]]:
    """``increased by Z from X to Y`` / ``from X to Y, an increase of Z``."""
    checks: list[dict[str, Any]] = []
    patterns = (
        r"(?P<verb>\w+)\s+(?:by|of)\s+<(?P<z>\d+)>\s+from\s+<(?P<x>\d+)>\s+to\s+<(?P<y>\d+)>",
        r"from\s+<(?P<x>\d+)>\s+to\s+<(?P<y>\d+)>\s*[,(]?\s*(?:an?\s+)?(?P<verb>\w+)\s+(?:of|by)\s+<(?P<z>\d+)>",
        r"from\s+<(?P<x>\d+)>\s+to\s+<(?P<y>\d+)>\s*\(\s*(?P<verb>[+-]?)\s*<(?P<z>\d+)>\s*\)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, template, re.IGNORECASE):
            x, y, z = (figures[int(match.group(name))] for name in ("x", "y", "z"))
            if "date" in (x.kind, y.kind, z.kind):
                continue
            verb = str(match.group("verb") or "").lower()
            direction = (
                "up"
                if verb in _UP_WORDS or verb == "+"
                else "down"
                if verb in _DOWN_WORDS or verb == "-"
                else ""
            )
            diff = y.value - x.value
            if z.kind == "percent":
                if x.value == 0:
                    continue
                expected = (abs(diff) / abs(x.value) * 100).quantize(Decimal("0.01"))
                ok = _within(expected, z.value, PERCENT_TOLERANCE)
                expected_text = _fmt(expected) + "%"
                stated_text = _fmt(z.value) + "%"
            else:
                expected = abs(diff)
                ok = expected == z.value
                expected_text = _fmt(expected)
                stated_text = _fmt(z.value)
            if direction == "up" and diff < 0:
                ok = False
                expected_text += " (a decrease)"
            elif direction == "down" and diff > 0:
                ok = False
                expected_text += " (an increase)"
            checks.append(
                {
                    "shape": "difference",
                    "ok": ok,
                    "operands": [x.text, y.text],
                    "stated": stated_text,
                    "expected": expected_text,
                    "detail": f"from {x.text} to {y.text} is a change of {expected_text}, stated {z.text}",
                }
            )
    return checks


_SUM_OPERATOR_RE = re.compile(
    r"(?:\bin total\b|\bfor a total of\b|\ba total of\b|\btotal of\b|\btotall?(?:s|ing)?\b|"
    r"\bcombined\b|\bsum of\b|\bsum\b|\baltogether\b|\bequals?\b|=|\bamounts? to\b|\bcomes? to\b)",
    re.IGNORECASE,
)
#: Operators that follow the stated total ("$1,500 in total"); every other
#: operator precedes it ("total $1,500", "= $1,500", "a total of $1,500").
_TRAILING_OPERATORS = ("in total", "combined", "altogether")


def _sentences(text: str, figures: list[Figure]) -> list[tuple[int, str]]:
    """``(offset, sentence)`` slices of ``text`` split at ``.``/``;``/newline that
    lie outside a figure — the ``.`` in ``$1,250.00`` is not a boundary."""
    spans = [(fig.start, fig.end) for fig in figures]
    out: list[tuple[int, str]] = []
    start = 0
    for index, char in enumerate(text):
        if char in ".;\n" and not any(a <= index < b for a, b in spans):
            out.append((start, text[start : index + 1]))
            start = index + 1
    if start < len(text):
        out.append((start, text[start:]))
    return out


def _check_sums(text: str, figures: list[Figure]) -> list[dict[str, Any]]:
    """Listed amounts against a stated total — only where the sentence states the
    relation.

    Within ONE sentence, an operator ("total", "in total", "combined", "sum of",
    "equals", "=", "amounts to") must bind the operands to the stated total: the
    total is the first figure after the operator ("total $1,500", "= $1,500") or,
    for a trailing operator, the figure just before it ("$1,500 in total"); the
    operands are the other figures of the same kind in that sentence, at least
    two. "total premium of $1,250" is a label, not an operator — the word "total"
    followed by a label noun binds nothing — and figures listed across sentences
    are never summed (live run 2026-09-27: "liability limit $100,000, collision
    deductible $500, comprehensive deductible $250" was summed against a "total
    premium" in the next sentence and reported as a contradiction).
    """
    checks: list[dict[str, Any]] = []
    for offset, sentence in _sentences(text, figures):
        local = [fig for fig in figures if offset <= fig.start < offset + len(sentence) and fig.kind in ("money", "number")]
        if len(local) < 3:
            continue
        for op in _SUM_OPERATOR_RE.finditer(sentence):
            op_start, op_end = offset + op.start(), offset + op.end()
            following = text[op_end : op_end + 24]
            # "total premium of $1,250": the operator is followed by a label noun,
            # which makes "total" part of a name, not a relation.
            if re.match(r"\s+(?:[A-Za-z]+\s+){0,1}(?:" + "|".join(_LABEL_WORDS) + r")s?\b", following, re.IGNORECASE):
                continue
            after = [fig for fig in local if fig.start >= op_end]
            before = [fig for fig in local if fig.end <= op_start]
            stated: Figure | None = None
            if op.group(0).lower() in _TRAILING_OPERATORS:
                if before and len(text[before[-1].end : op_start].split()) <= 1:
                    stated = before[-1]
            elif after and len(text[op_end : after[0].start].split()) <= 2:
                stated = after[0]
            if stated is None:
                continue
            operands = [fig for fig in local if fig is not stated and fig.kind == stated.kind]
            if len(operands) < 2:
                continue
            expected = sum((fig.value for fig in operands), Decimal(0))
            checks.append(
                {
                    "shape": "sum",
                    "ok": expected == stated.value,
                    "operands": [fig.text for fig in operands],
                    "stated": _fmt(stated.value),
                    "expected": _fmt(expected),
                    "detail": f"{' + '.join(fig.text for fig in operands)} = {_fmt(expected)}, stated {stated.text}",
                }
            )
            break
    return checks


def _check_spans(text: str, figures: list[Figure]) -> list[dict[str, Any]]:
    """Two dates and ``N months|years|days``."""
    dates = [fig for fig in figures if fig.kind == "date"]
    if len(dates) < 2:
        return []
    checks: list[dict[str, Any]] = []
    first, second = dates[0], dates[1]
    d1, d2 = date.fromisoformat(first.value), date.fromisoformat(second.value)
    days = abs((d2 - d1).days)
    for fig in figures:
        if fig.kind != "number":
            continue
        unit_match = _UNIT_WORDS_RE.match(text, fig.end + (1 if text[fig.end : fig.end + 1] == " " else 0))
        if unit_match is None:
            continue
        unit = unit_match.group(1).lower().rstrip("s")
        if unit == "day":
            expected = Decimal(days)
        elif unit == "month":
            expected = Decimal(round(days / 30.4375))
        else:
            expected = Decimal(round(days / 365.25))
        checks.append(
            {
                "shape": "date_span",
                "ok": expected == fig.value,
                "operands": [first.text, second.text],
                "stated": f"{_fmt(fig.value)} {unit}s",
                "expected": f"{_fmt(expected)} {unit}s",
                "detail": f"{first.text} to {second.text} is {_fmt(expected)} {unit}s, stated {fig.text} {unit}s",
            }
        )
    return checks


_LABEL_WORDS = (
    "premium",
    "deductible",
    "limit",
    "sublimit",
    "retention",
    "aggregate",
    "coinsurance",
    "commission",
    "fee",
    "rate",
    "total",
    "revenue",
    "income",
    "loss",
    "cost",
    "price",
)
_LABEL_WORD_RE = re.compile(r"\b(" + "|".join(_LABEL_WORDS) + r")s?\b", re.IGNORECASE)
_LABEL_LOOKBACK = 5
_QUALIFIER_STOP = {
    "the", "a", "an", "of", "is", "are", "was", "were", "for", "with", "per", "and", "or",
    "at", "to", "in", "on", "by", "its", "their", "this", "that", "has", "have", "be",
    "as", "from", "under", "which", "whose", "each", "any", "no", "not", "our", "your",
    "policy", "policys",
}
_MAX_QUALIFIER_WORDS = 2


def labelled(text: str, figures: list[Figure] | None = None) -> list[tuple[str, Figure]]:
    """``(qualified label, figure)`` for each non-date figure with a label head
    noun within ``_LABEL_LOOKBACK`` words before it in the same sentence.

    The label carries its qualifier — up to ``_MAX_QUALIFIER_WORDS`` words directly
    before the head noun, stopping at an article, preposition or verb — so
    "collision deductible $500" and "comprehensive deductible $250" are two items,
    not one deductible stated twice (live run 2026-09-27: they were flagged as an
    inconsistency). "the deductible is $25,000" is plain "deductible"; only
    identical qualified labels are ever compared.
    """
    raw = str(text or "")
    figures = extract_figures(raw) if figures is None else figures
    out: list[tuple[str, Figure]] = []
    for fig in figures:
        if fig.kind == "date":
            continue
        before = raw[: fig.start]
        sentence_start = max(before.rfind("."), before.rfind(";"), before.rfind("\n")) + 1
        words = re.findall(r"[A-Za-z]+", before[sentence_start:])
        window = words[-_LABEL_LOOKBACK:]
        for offset, word in enumerate(reversed(window)):
            match = _LABEL_WORD_RE.fullmatch(word)
            if not match:
                continue
            head_index = len(words) - 1 - offset
            qualifiers: list[str] = []
            for prior in reversed(words[max(0, head_index - _MAX_QUALIFIER_WORDS) : head_index]):
                if prior.lower() in _QUALIFIER_STOP or not prior.isalpha():
                    break
                qualifiers.insert(0, prior.lower())
            out.append((" ".join([*qualifiers, match.group(1).lower()]), fig))
            break
    return out


def _check_labels(
    claim_text: str,
    claim_figures: list[Figure],
    missing: list[Figure],
    evidence_text: str,
    evidence_figures: list[Figure],
) -> list[dict[str, Any]]:
    """A labelled figure the claim states that the evidence states differently.

    "The deductible is $25,000" against "Deductible: $5,000,000" is not a missing
    figure — the source names the same quantity with another value. Only a figure
    absent from the evidence is checked, and only when the evidence carries the
    same label with one or more values, none equal to the claim's.
    """
    if not missing:
        return []
    evidence_labels = labelled(evidence_text, evidence_figures)
    checks: list[dict[str, Any]] = []
    for label, fig in labelled(claim_text, claim_figures):
        if fig not in missing:
            continue
        others = [other for other_label, other in evidence_labels if other_label == label and other.kind == fig.kind]
        if not others or any(fig.same_value(other) for other in others):
            continue
        expected = ", ".join(dict.fromkeys(other.text for other in others))
        checks.append(
            {
                "shape": "labelled_figure",
                "ok": False,
                "operands": [],
                "stated": _fmt(fig.value),
                "expected": expected,
                "detail": f"the source states the {label} as {expected}, the claim states {fig.text}",
            }
        )
    return checks


def format_decimal(value: Any) -> str:
    """``Decimal`` → plain digits (``1500``, ``12.5``), never scientific notation."""
    return _fmt(value)


def recompute(claim: str, evidence: str, source_text: str | None = None) -> dict[str, Any]:
    """Check the claim's figures and stated arithmetic against verbatim ``evidence``,
    with ``source_text`` (the whole cited source) as the second place a figure may
    be found.

    A figure is "in the source" when it appears in the evidence window OR anywhere
    in the cited source's text (normalised). The window an anchor covers is at most
    three sentences and a list paragraph states eight facts (live run 2026-09-27:
    every figure of such a paragraph was reported missing); the customer's rule is
    that the figure be in the supplied document, so the whole document is searched.

    Returns ``{"status", "kind", "detail", "expected", "stated", "missing", "checks"}``.
    ``kind`` qualifies a ``mismatch``: ``arithmetic`` when a recomputed calculation
    or a same-label figure disagrees with the source (a contradiction);
    ``missing_figure`` when a figure is simply not in the evidence (not found).
    ``services/claim_policy`` maps the first to CONTRADICTED and the second to
    UNSUPPORTED.

    * ``not_applicable`` — the claim states no figure, or states figures that all
      appear in the evidence and no recognised calculation;
    * ``recomputed_ok``  — at least one recognised calculation was recomputed from
      operands found in the evidence and agrees with what the claim states, and
      every figure is accounted for;
    * ``mismatch``       — a figure the claim states is absent from the evidence
      (``missing`` lists them), or a recomputed calculation disagrees
      (``expected`` / ``stated`` carry the first disagreement);
    * ``insufficient``   — a calculation is recognised but its operands are not in
      the evidence, so it cannot be recomputed.

    A figure that is the correct result of a calculation whose operands are in
    the evidence is accounted for even when the evidence does not state it — the
    recomputation is the check. A figure that appears only as an operand in the
    evidence is not enough on its own to carry a stated result that disagrees.
    """
    claim_text = str(claim or "")
    evidence_text = str(evidence or "")
    claim_figures = extract_figures(claim_text)
    if not claim_figures:
        return {
            "status": "not_applicable",
            "kind": None,
            "detail": "the claim states no figure",
            "expected": None,
            "stated": None,
            "missing": [],
            "checks": [],
        }
    evidence_figures = extract_figures(evidence_text)
    if source_text:
        evidence_figures = evidence_figures + extract_figures(str(source_text))
        evidence_text = evidence_text + "\n" + str(source_text)
    missing = [fig for fig in claim_figures if not _present(fig, evidence_figures)]

    template = _template(claim_text, claim_figures)
    checks = (
        _check_shares(template, claim_figures)
        + _check_deltas(claim_text, template, claim_figures)
        + _check_sums(claim_text, claim_figures)
        + _check_spans(claim_text, claim_figures)
    )
    insufficient: list[dict[str, Any]] = []
    performed: list[dict[str, Any]] = []
    by_text = {fig.text: fig for fig in claim_figures}
    for check in checks:
        operands = [by_text[t] for t in check["operands"] if t in by_text]
        if any(not _present(op, evidence_figures) for op in operands):
            insufficient.append(check)
            continue
        performed.append(check)
        if check["ok"]:
            # The recomputed result vouches for the stated figure: it is derived
            # from source operands, so it need not appear verbatim in the evidence.
            missing = [
                fig
                for fig in missing
                if fig.text not in (check["stated"], check.get("stated_text", ""))
                and not _stated_matches(fig, check)
            ]

    failed = [check for check in performed if not check["ok"]]
    failed += _check_labels(claim_text, claim_figures, missing, evidence_text, evidence_figures)
    if failed:
        first = failed[0]
        return {
            "status": "mismatch",
            "kind": "arithmetic",
            "detail": first["detail"],
            "expected": first["expected"],
            "stated": first["stated"],
            "missing": [fig.text for fig in missing],
            "checks": performed,
        }
    if missing:
        return {
            "status": "mismatch",
            "kind": "missing_figure",
            "detail": "figure(s) not in the source: " + ", ".join(fig.text for fig in missing),
            "expected": None,
            "stated": ", ".join(fig.text for fig in missing),
            "missing": [fig.text for fig in missing],
            "checks": performed,
        }
    if performed:
        return {
            "status": "recomputed_ok",
            "kind": None,
            "detail": "; ".join(check["detail"] for check in performed),
            "expected": performed[0]["expected"],
            "stated": performed[0]["stated"],
            "missing": [],
            "checks": performed,
        }
    if insufficient:
        first = insufficient[0]
        return {
            "status": "insufficient",
            "kind": None,
            "detail": "operands not in the source: " + ", ".join(first["operands"]),
            "expected": None,
            "stated": first["stated"],
            "missing": [],
            "checks": [],
        }
    return {
        "status": "not_applicable",
        "kind": None,
        "detail": f"no calculation stated; all {len(claim_figures)} figure(s) appear in the source",
        "expected": None,
        "stated": None,
        "missing": [],
        "checks": [],
    }


def _stated_matches(fig: Figure, check: dict[str, Any]) -> bool:
    """Whether ``fig`` is the stated result of ``check`` (by value, not text)."""
    stated = str(check.get("stated") or "").rstrip("%").split(" ")[0]
    if fig.kind == "date":
        return False
    try:
        return Decimal(stated) == fig.value
    except (InvalidOperation, ValueError):
        return False


__all__ = ["Figure", "PERCENT_TOLERANCE", "extract_figures", "format_decimal", "labelled", "recompute"]
