"""The authoritative-domain allowlist for fetched sources (2C).

``config/authoritative_domains.yaml`` names the domains the retrieval path is
allowed to fetch from, grouped by category, plus a ``denylist``. The rule is
DENY BY DEFAULT:

* denylist: the host is the listed domain or a subdomain of it -> rejected;
* otherwise allowlist: the host is a listed domain or a subdomain of it -> allowed;
* anything else -> rejected.

The file is re-read when its mtime or size changes, so an operator edits the
allowlist on the box and the next search or fetch uses it — no deploy, no
restart. A file that is missing, unreadable or malformed resolves to an EMPTY
allowlist, so a broken allowlist denies everything instead of failing open, and
the parse error travels with the result (``Allowlist.error``) instead of being
swallowed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

try:
    from ..lib.source_labels import fetched_url_of
except ImportError:  # pragma: no cover
    from lib.source_labels import fetched_url_of

_log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "authoritative_domains.yaml"
DENYLIST_KEY = "denylist"

ALLOW = "allow"
DENY = "deny"


@dataclass(frozen=True)
class Allowlist:
    """A parsed allowlist: domain -> category, plus the denylist."""

    allow: dict[str, str] = field(default_factory=dict)
    deny: frozenset[str] = frozenset()
    path: str = ""
    error: str = ""

    def decision(self, host: str) -> tuple[str, str]:
        """``(ALLOW|DENY, category-or-reason)`` for a host. Deny by default."""
        clean = normalize_host(host)
        if not clean:
            return DENY, "empty_host"
        if _matches(clean, self.deny):
            return DENY, "denylisted"
        category = self.allow.get(clean)
        if category:
            return ALLOW, category
        for domain, cat in self.allow.items():
            if clean.endswith("." + domain):
                return ALLOW, cat
        return DENY, "not_allowlisted"

    def allows(self, host: str) -> bool:
        return self.decision(host)[0] == ALLOW


def normalize_host(host: str) -> str:
    """Lowercase, port and trailing dot removed. ``""`` for anything unusable."""
    raw = str(host or "").strip().lower()
    if not raw:
        return ""
    if "/" in raw or "@" in raw:           # a URL where a host was expected
        raw = host_of(raw)
    if raw.startswith("["):                # [::1]:443
        raw = raw.split("]", 1)[0].lstrip("[")
    elif raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    return raw.strip().strip(".")


def host_of(url: str) -> str:
    """Host of an absolute URL, normalized — ``""`` when there is none."""
    try:
        return normalize_host(urlsplit(str(url or "").strip()).hostname or "")
    except ValueError:
        return ""


def _matches(host: str, domains: frozenset[str] | set[str] | dict[str, str]) -> bool:
    if host in domains:
        return True
    return any(host.endswith("." + domain) for domain in domains)


_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], Allowlist]] = {}


def config_path() -> Path:
    import os

    override = (os.environ.get("ASSURE_AUTHORITATIVE_DOMAINS") or "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def parse_allowlist(text: str, *, path: str = "") -> Allowlist:
    """Parse the YAML body. Raises ValueError on a shape the matcher cannot trust."""
    import yaml

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("top level must be a mapping of category: [domains]")

    allow: dict[str, str] = {}
    deny: set[str] = set()
    for key, value in raw.items():
        category = str(key or "").strip()
        if category == DENYLIST_KEY:
            deny.update(_domains_of(value, category))
            continue
        for domain in _domains_of(value, category):
            allow.setdefault(domain, category)
    if not allow:
        raise ValueError("no domains listed")
    return Allowlist(allow=allow, deny=frozenset(deny), path=path)


def _domains_of(value: object, category: str) -> list[str]:
    if isinstance(value, dict):                       # state_doi: {MA: mass.gov}
        entries = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        entries = list(value)
    else:
        raise ValueError(f"{category}: expected a list of domains or a mapping of state: domain")
    domains: list[str] = []
    for entry in entries:
        domain = normalize_host(entry if isinstance(entry, str) else "")
        if not domain:
            raise ValueError(f"{category}: {entry!r} is not a bare host")
        if any(ch in domain for ch in "*?/"):
            raise ValueError(f"{category}: {domain!r} is not a bare host")
        domains.append(domain)
    if not domains:
        raise ValueError(f"{category}: empty domain list")
    return domains


def load_allowlist(*, path: Path | None = None, force: bool = False) -> Allowlist:
    """The current allowlist, re-parsed when the file changed on disk.

    Never raises: a bad file becomes an empty allowlist carrying its error, which
    denies every host.
    """
    target = path or config_path()
    key = str(target)
    try:
        stat = target.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        result = Allowlist(path=key, error=f"cannot read allowlist: {exc}")
        with _cache_lock:
            _cache[key] = ((0, 0), result)
        return result

    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp and not force:
            return cached[1]

    try:
        parsed = parse_allowlist(target.read_text(encoding="utf-8"), path=key)
    except (OSError, ValueError) as exc:
        _log.warning("[authoritative-domains] %s: %s — denying every host", key, exc)
        parsed = Allowlist(path=key, error=str(exc))

    with _cache_lock:
        _cache[key] = (stamp, parsed)
    return parsed


def decision_for(host: str) -> tuple[str, str]:
    """``(ALLOW|DENY, category-or-reason)`` for a host, against the live allowlist."""
    return load_allowlist().decision(host)


def host_allowed(host: str) -> bool:
    return decision_for(host)[0] == ALLOW


def decision_for_url(url: str) -> tuple[str, str]:
    return decision_for(host_of(url))
