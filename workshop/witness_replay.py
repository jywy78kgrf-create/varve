#!/usr/bin/env python3
"""witness_replay — check a log against heads that were published elsewhere.

The README's threat model says the mitigation that exists today is: publish
the chain head, and anyone who kept one can later prove a rewrite by diffing
against it. This is that diff, as a command instead of a sentence.

Feed it the head observations that accumulated somewhere the writer does not
control — session reports in an inbox, a mirror, a comment thread — and it
answers one question: does the history on this disk still agree with
everything anyone was ever told about it?

    python3 witness_replay.py ../notebook observations.txt

Three failures it is looking for, all of which `varve verify` passes clean
because the chain alone cannot see them:

    REWRITTEN   an observed entry is still here but carries a different hash
    ABSENT      an observed entry id is gone — truncated tail, or a rewrite
                that did not bother to keep the ids

Deliberately standalone: stdlib only, no varve import, canonicalization
reimplemented in twenty lines. That is not tidiness, it is the point. A
writer who rewrites the log also holds varve/store.py, so a verifier that
imports the suspect's own hash function proves nothing. Read these twenty
lines against CONSTITUTION.md rule 3 and decide for yourself whether they
are right; then this tool needs no trust at all.

Observation file format — one per line, whitespace-separated, '#' comments:

    2026-08-22T22:51:04Z  e000003  27c8bac7...  where it was published

The first three fields are when it was observed, the entry id, and the head
hash. Everything after is a free-text note on provenance, echoed back in the
report so a reader can go check the receipt. Short hashes are fine; they are
compared as prefixes, since a report may have wrapped or been trimmed.
"""

import hashlib
import json
import os
import re
import sys

SEQ_RE = re.compile(r"^(\d{6,})\.json$")


def entry_hash(entry):
    """SHA-256 over the entry's canonical bytes: sorted keys, no whitespace,
    UTF-8, every field except 'hash' itself. Mirrors store.canonical() by
    hand, on purpose — see the module docstring."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_log(root):
    """Every entry under <root>/log, in numeric seq order."""
    d = os.path.join(root, "log")
    if not os.path.isdir(d):
        sys.exit("no varve log at %s" % root)
    found = [(int(m.group(1)), n) for n in os.listdir(d) if (m := SEQ_RE.match(n))]
    entries = []
    for _, name in sorted(found):
        with open(os.path.join(d, name), "r", encoding="utf-8") as f:
            entries.append(json.load(f))
    return entries


def read_observations(path):
    """Parse the published-head file. Returns (when, entry_id, hash, note)."""
    obs = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 3)
            if len(parts) < 3:
                sys.exit("%s:%d: need at least 'when id hash'" % (path, lineno))
            when, eid, h = parts[0], parts[1], parts[2].rstrip(".…")
            obs.append((when, eid, h, parts[3] if len(parts) > 3 else ""))
    return obs


def chain_problems(entries):
    """The partial-tamper walk, so a mismatch below is never blamed on the
    observations when the chain itself is already broken."""
    problems = []
    prev_hash, prev_seq = "", 0
    for e in entries:
        tag = e.get("id", "seq %s" % e.get("seq"))
        if e.get("seq") != prev_seq + 1:
            problems.append("%s: sequence gap" % tag)
        if e.get("prev", "") != prev_hash:
            problems.append("%s: chain broken" % tag)
        if entry_hash(e) != e.get("hash"):
            problems.append("%s: content hash mismatch" % tag)
        prev_hash, prev_seq = e.get("hash", ""), e.get("seq", 0)
    return problems


def replay(entries, obs):
    """Check each observation against the log. Returns (verdict, detail) rows.

    An observation names an entry id, so it is matched by id rather than by
    position: an entry that moved seq is as much a rewrite as one that
    changed bytes, and matching by id is what catches it.
    """
    by_id = {e.get("id"): e for e in entries}
    top = entries[-1]["seq"] if entries else 0
    rows = []
    for when, eid, h, note in obs:
        e = by_id.get(eid)
        if e is None:
            rows.append(("ABSENT", when, eid, h, note,
                         "not in the log at all; it now ends at seq %d" % top))
        elif not e.get("hash", "").startswith(h):
            rows.append(("REWRITTEN", when, eid, h, note,
                         "now %s…" % e.get("hash", "")[:16]))
        else:
            rows.append(("MATCH", when, eid, h, note, "seq %d" % e["seq"]))
    return rows


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__.strip().splitlines()[0] + "\n"
                 "usage: witness_replay.py <log-root> <observations-file>")
    root, obs_path = argv[1], argv[2]
    entries = read_log(root)
    obs = read_observations(obs_path)

    broken = chain_problems(entries)
    if broken:
        print("chain is already broken — fix that before reading anything below:")
        for p in broken:
            print("  ! %s" % p)
        print()

    rows = replay(entries, obs)
    width = max((len(r[0]) for r in rows), default=5)
    for verdict, when, eid, h, note, detail in rows:
        print("%-*s  %s  %s %s…  %s" % (width, verdict, when, eid, h[:16], detail))
        if note:
            print("%s  published: %s" % (" " * (width + len(when) + 2), note))

    bad = [r for r in rows if r[0] != "MATCH"]
    print()
    if not obs:
        print("no observations to replay — nothing was witnessed, "
              "so nothing is provable")
        return 0
    if bad:
        print("%d of %d observations DISAGREE with this log. If the receipts "
              "are genuine, this history was rewritten." % (len(bad), len(rows)))
        return 1
    print("all %d published heads still match. Coverage is only as good as "
          "the observations: the highest one here is seq %d, and anything "
          "after it rests on the chain alone."
          % (len(rows), max(e["seq"] for e in entries
                            if e.get("id") in {o[1] for o in obs})))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
