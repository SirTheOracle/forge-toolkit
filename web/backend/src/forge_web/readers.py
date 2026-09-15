"""Bounded, exact-path source reads. No GET ever repairs, rewrites or creates a source.

PATHS ARE BUILT FROM VALIDATED LIVE TMUX NAMES, never from a `forge-context.*.yml` glob
(plan-review M6). The sentinel `__nosession__`, the bare legacy `forge-context.yml` and any
`.bak*` sibling are excluded BY CONSTRUCTION: they are not reachable from a live session
name, and `_session_file` refuses them explicitly as a second line of defence.

THE PER-PIPELINE LOG NEEDS A REGEX FALLBACK. Legacy logs contain malformed `files:-` lines
that make `yaml.safe_load` throw for the WHOLE file; `bin/forge-watch:562-582` and
`bin/forge:664-671` both carry the same fallback and this reader is the third copy of that
contract. Without it, an entire project's stage projection silently becomes empty.
"""

from __future__ import annotations

import datetime
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Plan §5: "Cap structured/Markdown files at 1 MiB and subprocess output at 4 MiB".
STRUCTURED_MAX = 1 << 20
SUBPROCESS_MAX = 4 << 20
OBJECTIVE_CHARS = 400
RESPONSE_READ_MAX = 256 * 1024

_BAD_NAME = re.compile(r"(^$|/|\\|\x00|^\.\.?$|\.bak)")
_SENTINEL = "__nosession__"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ReadResult:
    """One bounded read. `state` is the SourceState value; `data` is only set on ok."""

    path: str
    state: str
    detail: str = ""
    text: str | None = None
    data: Any = None
    size_bytes: int | None = None
    limit_hit: bool = False
    observed_at: str = field(default_factory=_now_iso)
    source_at: str | None = None


def session_file(root: str, name: str, template: str) -> str:
    """Build `<root>/.dev/<template % name>` from a VALIDATED live session name.

    Refuses the sentinel, path separators, NUL, `.`/`..` and any `.bak` spelling. A live
    tmux session cannot legitimately be called any of those, so a hit here means the caller
    reached this function from something other than the inventory — which is the bug this
    guard exists to make loud.
    """
    if name == _SENTINEL or _BAD_NAME.search(name or ""):
        raise ValueError("refusing to build a source path from session name %r" % name)
    return os.path.join(root, ".dev", template % name)


def _stat_pair(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (st.st_size, st.st_mtime_ns)


def contained(root: str, path: str) -> bool:
    """The project .dev tree is the sole state boundary, including its own symlink."""
    anchor = os.path.join(os.path.realpath(root), ".dev")
    real = os.path.realpath(path)
    return os.path.realpath(anchor) == anchor and (real == anchor or real.startswith(anchor + os.sep))


def safe_source(path: str) -> bool:
    parts = Path(os.path.abspath(path)).parts
    if ".dev" not in parts:
        return True  # generic reader tests / registry; projections always pass .dev paths
    at = parts.index(".dev")
    return contained(str(Path(*parts[:at])), path)


def valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug)) and slug not in (".", "..")


def read_prefix(path: str, cap: int = RESPONSE_READ_MAX) -> ReadResult:
    return read_text(path, cap, _prefix=True)


def read_text(path: str, cap: int = STRUCTURED_MAX, *, _prefix: bool = False, _strict: bool = False) -> ReadResult:
    """Bounded read with a stat-before/after consistency retry.

    A NON-REGULAR file (FIFO, socket, device) is refused without opening it: a FIFO in
    `.dev/` would otherwise block the collector forever on the first read. This is the
    file-type check the review asks for, applied to EVERY source read in this module.
    """
    if not safe_source(path):
        return ReadResult(path, "malformed", "source escapes the project state tree")
    for attempt in (0, 1):
        before = _stat_pair(path)
        if before is None:
            if os.path.lexists(path):
                return ReadResult(path=path, state="malformed",
                                  detail="not a regular file; refusing to read")
            return ReadResult(path=path, state="absent", detail="no such file")
        if before[0] > cap and not _prefix:
            return ReadResult(path=path, state="oversized", size_bytes=before[0],
                              detail="%d bytes exceeds the %d byte cap; not parsed"
                                     % (before[0], cap))
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    return ReadResult(path=path, state="malformed",
                                      detail="not a regular file; refusing to read")
                blob = b""
                while len(blob) <= cap:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    blob += chunk
            finally:
                os.close(fd)
        except OSError as exc:
            return ReadResult(path=path, state="malformed", detail="read failed: %s" % exc)
        after = _stat_pair(path)
        if after == before:
            limit_hit = len(blob) > cap
            try:
                decoded = blob[:cap].decode("utf-8", "strict" if _strict else "replace")
            except UnicodeDecodeError:
                return ReadResult(path=path, state="malformed", detail="invalid UTF-8")
            return ReadResult(
                path=path,
                state="ok",
                text=decoded,
                size_bytes=before[0],
                limit_hit=limit_hit,
                source_at=datetime.datetime.fromtimestamp(
                    before[1] / 1e9, datetime.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        if attempt == 1:
            return ReadResult(path=path, state="stale",
                              detail="file changed while being read twice; keeping last good")
    return ReadResult(path=path, state="stale", detail="unreachable")


def read_yaml(path: str, cap: int = STRUCTURED_MAX) -> ReadResult:
    result = read_text(path, cap)
    if result.state != "ok":
        return result
    try:
        import yaml

        result.data = yaml.safe_load(result.text) or {}
    except Exception as exc:                      # noqa: BLE001 - degrade, never throw
        result.state = "malformed"
        result.detail = "YAML parse failed: %s" % str(exc)[:200]
        result.data = None
    return result


def read_json(path: str, cap: int = STRUCTURED_MAX) -> ReadResult:
    result = read_text(path, cap)
    if result.state != "ok":
        return result
    import json

    try:
        result.data = json.loads(result.text or "")
    except ValueError as exc:
        result.state = "malformed"
        result.detail = "JSON parse failed: %s" % str(exc)[:200]
        result.data = None
    return result


# ── per-session structured sources ───────────────────────────────────────────


def context_for(root: str, name: str) -> ReadResult:
    result = read_yaml(session_file(root, name, "forge-context.%s.yml"))
    if result.state == "ok" and isinstance(result.data, dict):
        result.source_at = str(result.data.get("updated_at") or result.source_at or "") or None
    return result


def status_for(root: str, name: str) -> ReadResult:
    return read_text(session_file(root, name, "forge-status.%s.md"))


def usage_for(root: str, name: str) -> ReadResult:
    return read_yaml(session_file(root, name, "forge-usage.%s.yml"))


# ── the per-slug pipeline log ────────────────────────────────────────────────

_BLOCK_RE = re.compile(r'^\s*-\s+timestamp:\s*"([^"]+)"', re.MULTILINE)
_RESP_RE = re.compile(r'^\s+response:\s*(null|"(?:[^"\\]|\\.)*")', re.MULTILINE)
_LOG_FIELDS = (
    "stage", "from", "to", "session", "incarnation", "parked_at", "parked_reason",
    "uncommitted", "prompt_sha256", "delivery_id", "physical_code_root",
)


def read_log_entries(root: str, slug: str) -> tuple[list[dict[str, Any]], bool, ReadResult]:
    """(entries, parse_clean, read_result).

    `parse_clean` is False when a regex-fallback block could not be classified open-vs-closed.
    A caller building a "stage fully resolved" predicate MUST treat parse_clean=False as
    non-authoritative, exactly as bin/forge-watch:542-582 does.
    """
    if not valid_slug(slug):
        return [], False, ReadResult("", "malformed", "invalid slug")
    path = os.path.join(root, ".dev", "proposals", slug, "forge-log.yml")
    result = read_text(path)
    if result.state != "ok":
        return [], False, result
    raw = result.text or ""
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
        entries = data.get("entries", []) or []
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)], True, result
    except Exception:                             # noqa: BLE001 - legacy `files:-` logs
        pass
    entries = []
    parse_clean = True
    starts = [(m.start(), m.group(1)) for m in _BLOCK_RE.finditer(raw)]
    if not starts and raw.strip():
        result.state = "malformed"
        result.detail = "no parseable entries (neither YAML nor the timestamp-block fallback)"
        return [], False, result
    for index, (offset, ts) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(raw)
        block = raw[offset:end]
        match = _RESP_RE.search(block)
        if not match:
            parse_clean = False
            continue
        entry: dict[str, Any] = {
            "timestamp": ts,
            "response": None if match.group(1) == "null" else match.group(1),
        }
        for fieldname in _LOG_FIELDS:
            found = re.search(r"^\s+%s:\s*(.*?)\s*$" % fieldname, block, re.MULTILINE)
            if found:
                entry[fieldname] = found.group(1).strip().strip('"')
        entries.append(entry)
    return entries, parse_clean, result


def slugs_in(root: str) -> list[str]:
    """Slugs with a per-pipeline log under this root. Directory listing only; no recursion
    into proposal contents and no history crawl."""
    base = os.path.join(root, ".dev", "proposals")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith("."):
            continue
        if os.path.isfile(os.path.join(base, name, "forge-log.yml")):
            out.append(name)
    return out


# ── objective excerpt ────────────────────────────────────────────────────────

_MD_NOISE = re.compile(r"^\s*(#{1,6}\s|>\s|[-*+]\s|\d+[.)]\s|```)")
_MD_INLINE = re.compile(r"[`*_\[\]()>#]")


def objective_excerpt(root: str, slug: str) -> tuple[str | None, str]:
    """Up to 400 plain-text characters of substantive prose from the slug's
    problem-statement.md, plus a source label. NO generation, NO persisted summary.

    "Substantive" is defined mechanically so it is testable: the first run of consecutive
    non-empty lines that are not headings, blockquotes, list items, fences or metadata
    key-value lines, after any front matter.
    """
    if not valid_slug(slug):
        return None, "Invalid active slug; source refused"
    path = os.path.join(root, ".dev", "proposals", slug, "problem-statement.md")
    result = read_text(path)
    if result.state != "ok" or not result.text:
        return None, "Source excerpt: %s (%s)" % (path, result.state)
    paragraph: list[str] = []
    for line in result.text.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if _MD_NOISE.match(line):
            if paragraph:
                break
            continue
        if re.match(r"^[A-Za-z][A-Za-z ]{0,30}:\s", stripped) and len(stripped) < 80:
            continue                              # "Problem type: feature", "Suggested slug: …"
        paragraph.append(stripped)
    if not paragraph:
        return None, "Source excerpt: %s (no substantive prose found)" % path
    text = _MD_INLINE.sub("", " ".join(paragraph)).strip()
    if len(text) > OBJECTIVE_CHARS:
        text = text[:OBJECTIVE_CHARS].rsplit(" ", 1)[0] + "…"
    return text, "Source excerpt: .dev/proposals/%s/problem-statement.md" % slug


# ── asks ─────────────────────────────────────────────────────────────────────

_ASK_ID_RE = re.compile(r"^ask-[0-9A-Za-z-]+\.json$")


def open_asks(root: str, name: str, created_epoch: int | None) -> list[dict[str, Any]]:
    """Actual open ask RECORDS under this root, never board display strings.

    Three filters, each from the plan:
      - records whose `session` differs from this session are NOT actionable here;
      - records with no `session` at all are non-actionable project evidence;
      - records emitted BEFORE this session was created belong to a previous incarnation.
    An excluded record is still returned, flagged, so the operator sees the evidence instead
    of a silent gap.
    """
    adir = os.path.join(root, ".dev", "attention")
    try:
        names = [n for n in rotating_names(adir, 128, "asks:" + name) if _ASK_ID_RE.match(n)]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for filename in names:
        result = read_json(os.path.join(adir, filename))
        if result.state != "ok" or not isinstance(result.data, dict):
            continue
        record = dict(result.data)
        record["_path"] = os.path.join(adir, filename)
        rec_session = record.get("session") or ""
        if not rec_session:
            record["_actionable"] = False
            record["_ownership_note"] = (
                "This ask records no session; it is project evidence and cannot be answered "
                "from here."
            )
        elif rec_session != name:
            record["_actionable"] = False
            record["_ownership_note"] = (
                "This ask belongs to session '%s', not '%s'." % (rec_session, name)
            )
        elif created_epoch is not None and _emitted_epoch(record) is not None \
                and _emitted_epoch(record) < created_epoch:
            record["_actionable"] = False
            record["_ownership_note"] = (
                "Emitted before this session was created; it belongs to an earlier incarnation."
            )
        elif created_epoch is None or _emitted_epoch(record) is None:
            record["_actionable"] = False
            record["_ownership_note"] = "Creation/time ownership cannot be proved"
        else:
            record["_actionable"] = True
            record["_ownership_note"] = ""
        out.append(record)
    return out


def _emitted_epoch(record: dict[str, Any]) -> int | None:
    value = str(record.get("emitted_at") or "")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def ask_text_projection(record: dict[str, Any]) -> tuple[str | None, str, str, int | None, int | None]:
    """(question_text, text_state, label, saved_bytes, full_redacted_bytes).

    `question_sha256` is NEVER used to validate `question_text`: the hash is over the RAW
    question and the text is redacted, so the check would fail on every record containing a
    redactable token. The hash stays a property of the original question and nothing here
    touches it.
    """
    text = record.get("question_text")
    if not isinstance(text, str):
        return (
            None,
            "legacy_excerpt",
            "Saved excerpt; full question was not retained",
            None,
            None,
        )
    saved = len(text.encode("utf-8"))
    full = record.get("question_text_bytes")
    full_int = full if isinstance(full, int) else None
    if record.get("question_text_truncated"):
        return (
            text,
            "truncated",
            "Saved question truncated (%d of %s redacted bytes saved)"
            % (saved, full_int if full_int is not None else "?"),
            saved,
            full_int,
        )
    return text, "full", "Saved question", saved, full_int


# ── callbacks ────────────────────────────────────────────────────────────────


def scalar_text(value: Any) -> str:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value) if value is not None else ""


def callbacks_for(root: str, slug: str | None) -> list[dict[str, Any]]:
    """Read exact callback bytes within a finite directory/file/aggregate budget.

    Missing directory proves absence; unreadable, malformed, escaped or excessive input
    cannot prove absence and produces an explicit ambiguous binding.
    """
    if slug is not None and not valid_slug(slug):
        return [{"_overflow": True}]
    cdir = os.path.join(root, ".dev", "forge-tmp", "callbacks")
    if not safe_source(os.path.join(cdir, "probe")):
        return [{"_overflow": True}]
    try:
        import itertools
        with os.scandir(cdir) as iterator:
            batch = list(itertools.islice(iterator, 65))
        if len(batch) > 64:
            return [{"_overflow": True}]
    except FileNotFoundError:
        return []
    except OSError:
        return [{"_overflow": True}]
    out = []
    total = 0
    for item in batch:
        if not item.name.endswith(".callback"):
            continue
        result = read_text(item.path, cap=65536, _strict=True)
        total += result.size_bytes or 0
        if total > (1 << 20):
            return [{"_overflow": True}]
        if result.state != "ok":
            out.append({"_file": item.name, "_invalid": True})
            continue
        record: dict[str, Any] = {"_file": item.name,
            "_sha256": __import__("hashlib").sha256((result.text or "").encode()).hexdigest()}
        import yaml
        class UniqueLoader(yaml.BaseLoader):
            pass
        def mapping(loader, node, deep=False):
            value = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in value:
                    raise yaml.YAMLError("duplicate/non-scalar header")
                value[key] = loader.construct_object(value_node, deep=deep)
            return value
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        try:
            parsed = yaml.load(result.text or "", Loader=UniqueLoader)
        except (yaml.YAMLError, ValueError):
            out.append({"_file": item.name, "_invalid": True}); continue
        known = {'slug','stage','status','worker','session','incarnation','origin','callback_id',
                 'timestamp','selected_pending_timestamp','message','parked_at','parked_reason',
                 'uncommitted','delivery_id','physical_code_root','root_identity','prompt_sha256'}
        required = {'slug','stage','status','worker','callback_id','timestamp','message'}
        if (not isinstance(parsed, dict) or set(parsed) - known or not required.issubset(parsed)
                or any(isinstance(value, (dict, list)) for value in parsed.values())
                or any(parsed[key] is None for key in required)
                or any(not scalar_text(parsed[key]) for key in required - {'message'})):
            out.append({"_file": item.name, "_invalid": True}); continue
        for key, value in parsed.items():
            record[key] = scalar_text(value)
        try:
            datetime.datetime.strptime(record['timestamp'], "%Y-%m-%dT%H:%M:%SZ")
            if record.get('selected_pending_timestamp'):
                datetime.datetime.strptime(record['selected_pending_timestamp'], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            out.append({"_file": item.name, "_invalid": True}); continue
        if record['status'] not in {'DONE','BLOCKED','ERROR','PARKED'}:
            out.append({"_file": item.name, "_invalid": True}); continue
        stem = record['slug'] + '-' + record['stage']
        if record.get('session'):
            stem += '.' + record['session']
            if record.get('incarnation'):
                stem += '.' + record['incarnation']
        elif record.get('incarnation'):
            out.append({"_file": item.name, "_invalid": True}); continue
        if item.name != stem + '.callback':
            out.append({"_file": item.name, "_invalid": True}); continue
        if slug is None or record.get("slug") == slug:
            out.append(record)
    return out


_DIRECTORY_CURSORS: dict[str, Any] = {}


def rotating_names(path: str, limit: int = 64, consumer: str = "default") -> list[str]:
    if not safe_source(os.path.join(path, "probe")):
        return []
    out = []
    try:
        key = (path, consumer)
        cursor = _DIRECTORY_CURSORS.get(key)
        if cursor is None:
            cursor = os.scandir(path)
            _DIRECTORY_CURSORS[key] = cursor
        for _ in range(limit):
            try:
                out.append(next(cursor).name)
            except StopIteration:
                cursor.close()
                _DIRECTORY_CURSORS.pop((path, consumer), None)
                break
    except OSError:
        _DIRECTORY_CURSORS.pop((path, consumer), None)
    if len(_DIRECTORY_CURSORS) > 256:
        oldest = next(iter(_DIRECTORY_CURSORS))
        _DIRECTORY_CURSORS.pop(oldest).close()
    return out


def callback_binding(root: str, slug: str, entry: dict[str, Any], name: str, created: str,
                     records=None, pending_entries=None) -> tuple[str, str]:
    records = callbacks_for(root, slug) if records is None else records
    if any(c.get("_overflow") for c in records):
        return "ambiguous", "ambiguous"
    stem = slug + '-' + str(entry.get('stage') or '')
    eligible = {stem + '.callback', stem + '.' + name + '.callback',
                stem + '.' + name + '.' + created + '.callback'}
    matching = [c for c in records if c.get('_file') in eligible]
    candidates = [c for c in matching if not c.get('_invalid')]
    if len(candidates) > 1:
        return "ambiguous", "ambiguous"
    if not candidates:
        return ("ambiguous", "ambiguous") if matching else ("none", "none")
    c = candidates[0]
    aliases = {'inline':'claude-opus','general':'claude-opus','claude-general':'claude-opus',
               'opus':'claude-opus','codex':'codex-a','orchestrator':'claude',
               'claude-orchestrator':'claude','sonnet':'claude-sonnet'}
    worker = lambda value: aliases.get(str(value or ''), str(value or ''))
    if (c.get('session') != name or c.get('incarnation') != created
            or c.get('status') not in ('BLOCKED', 'PARKED')
            or not c.get('selected_pending_timestamp')
            or scalar_text(c['selected_pending_timestamp']) != scalar_text(entry.get('timestamp'))
            or worker(c.get('worker')) != worker(entry.get('to'))
            or str(c.get('delivery_id') or '') != str(entry.get('delivery_id') or '')):
        return 'ambiguous', 'ambiguous'
    matching_pending = [e for e in (pending_entries if pending_entries is not None else [entry])
        if e.get('response') is None and e.get('stage') == entry.get('stage')
        and worker(e.get('to')) == worker(c.get('worker'))
        and str(e.get('session') or name) == name and str(e.get('incarnation') or created) == created
        and scalar_text(e.get('timestamp')) == scalar_text(c['selected_pending_timestamp'])]
    if len(matching_pending) != 1:
        return 'ambiguous', 'ambiguous'
    return str(c['callback_id']), str(c['_sha256'])