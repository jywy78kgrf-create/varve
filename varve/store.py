"""The log: append-only entries, hash-chained, founded empty.

Entries live as one JSON file per entry under <root>/log/, named by sequence
number. The file layout is the canonical store; anything else (indexes, views,
dashboards) must be derivable from it. An entry's hash covers its full content
including the previous entry's hash, so any historical edit breaks the chain
from that point on — that property, not trust in this code, is the guarantee.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from . import validate

LOG_DIR = "log"
# 6+ digits: %06d widens past 999999 on its own, but a fixed {6} regex would
# silently DROP entry one million from read_log — a truncation bug wearing a
# filename convention as a disguise (first external review, 2026-08-22).
_SEQ_RE = re.compile(r"^(\d{6,})\.json$")


def _log_dir(root):
    return os.path.join(root, LOG_DIR)


def _entry_path(root, seq):
    return os.path.join(_log_dir(root), "%06d.json" % seq)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(entry):
    """The byte string an entry's hash covers: everything except 'hash' itself."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(entry):
    return hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()


def read_log(root):
    """All entries in sequence order. Missing dir means no log here."""
    d = _log_dir(root)
    if not os.path.isdir(d):
        raise FileNotFoundError("no varve log at %s (run: varve init)" % root)
    found = []
    for name in os.listdir(d):
        m = _SEQ_RE.match(name)
        if m:
            found.append((int(m.group(1)), name))
    entries = []
    # numeric sort, not lexicographic: "1000000.json" sorts before "999999.json"
    # as a string, which would shuffle the chain right when the log gets long
    for _, name in sorted(found):
        with open(os.path.join(d, name), "r", encoding="utf-8") as f:
            entries.append(json.load(f))
    return entries


def init(root, note=""):
    """Found a log. Refuses to found over an existing one — a second founding
    is exactly the kind of history rewrite the constitution forbids."""
    d = _log_dir(root)
    if os.path.isdir(d) and any(_SEQ_RE.match(n) for n in os.listdir(d)):
        raise ValueError("a varve log already exists at %s" % root)
    os.makedirs(d, exist_ok=True)
    founding = {
        "seq": 1,
        "id": "e000001",
        "ts": now_iso(),
        "kind": "meta",
        "title": "founding",
        "body": (
            "This log was founded empty at this timestamp. Nothing predates "
            "this entry; any claim of an earlier entry is fabricated. "
            + (note or "")
        ).strip(),
        "anchors": [],
        "tags": ["founding"],
        "prev": "",
    }
    founding["hash"] = entry_hash(founding)
    _write(root, founding)
    return founding


def append(root, fields):
    """Validate and append one entry. Returns the stored entry.

    Callers supply content fields only; seq/id/prev/hash are assigned here so
    an author can't mint its own position in history. Raises ValueError with
    every gate failure listed — the worker feeds that back to the model.
    """
    entries = read_log(root)
    if not entries:
        raise ValueError("log has no founding entry; refusing to append")
    last = entries[-1]

    entry = dict(fields)
    for reserved in ("seq", "id", "prev", "hash"):
        entry.pop(reserved, None)
    entry["seq"] = last["seq"] + 1
    entry["id"] = "e%06d" % entry["seq"]
    entry.setdefault("ts", now_iso())
    entry.setdefault("anchors", [])
    entry.setdefault("tags", [])
    entry["prev"] = last["hash"]

    problems = validate.check(entry, entries)
    if problems:
        raise ValueError("entry rejected by the gate:\n- " + "\n- ".join(problems))

    entry["hash"] = entry_hash(entry)
    _write(root, entry)
    return entry


def _write(root, entry):
    path = _entry_path(root, entry["seq"])
    if os.path.exists(path):
        raise ValueError("refusing to overwrite %s" % path)
    # write-then-rename so a crash never leaves a half-written entry in the chain
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def head(root):
    """The chain head: (seq, hash) of the last entry. This is the value to
    witness EXTERNALLY (a session report, a mirror, a transparency log):
    the chain proves internal order, but only a remembered head proves the
    log wasn't truncated or wholesale re-chained by whoever holds the disk."""
    entries = read_log(root)
    if not entries:
        raise ValueError("log is empty")
    return entries[-1]["seq"], entries[-1]["hash"]


def verify(root, expect_head=None):
    """Walk the chain; return a list of problems (empty = intact).

    Honest scope: this detects any PARTIAL tamper — an edited entry, a
    re-hashed edit, a gap, a reordering. It cannot detect tail truncation
    or a full re-chain by the disk's owner; for those, pass expect_head
    (a previously witnessed chain-head hash) or compare `head()` against
    a record the writer doesn't control."""
    entries = read_log(root)
    problems = []
    if not entries:
        return ["log is empty — not even a founding entry"]
    if entries[0]["seq"] != 1 or entries[0].get("prev") != "":
        problems.append("founding entry malformed (seq!=1 or prev not empty)")
    prev_hash, prev_seq, prev_ts = "", 0, ""
    for e in entries:
        tag = e.get("id", "seq %s" % e.get("seq"))
        if e["seq"] != prev_seq + 1:
            problems.append("%s: sequence gap (expected %d)" % (tag, prev_seq + 1))
        if e.get("prev", "") != prev_hash:
            problems.append("%s: chain broken (prev hash mismatch)" % tag)
        if entry_hash(e) != e.get("hash"):
            problems.append("%s: content hash mismatch (entry was altered)" % tag)
        if prev_ts and e.get("ts", "") < prev_ts:
            problems.append("%s: timestamp earlier than predecessor" % tag)
        prev_hash, prev_seq, prev_ts = e.get("hash", ""), e["seq"], e.get("ts", "")
    if expect_head and entries and entries[-1].get("hash") != expect_head:
        problems.append(
            "chain head %s… does not match the witnessed head %s… — "
            "truncated tail or forked history" % (entries[-1].get("hash", "")[:12], expect_head[:12])
        )
    return problems
