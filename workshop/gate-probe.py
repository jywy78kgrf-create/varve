#!/usr/bin/env python3
"""Probe a varve checkout for inputs the gate accepts but a consumer cannot handle.

The audit of 2026-08-23 that produced entry e000002 was run with this script.
It is here so the finding is reproducible rather than merely reported: run it
against this commit and all eight probes read CLOSED; copy it into a checkout
of the founding commit (ae185e3) and all eight read OPEN. That contrast, not
this docstring, is the evidence for e000002.

    python workshop/gate-probe.py                 # probe the checkout it lives in
    git stash && python workshop/gate-probe.py    # ...or any other state

Each probe is a triple (name, what it does, what "closed" looks like). A probe
is OPEN if the defect is present, CLOSED if the fix holds, and CRASH if it
failed in a way the probe itself did not anticipate — which is itself a finding.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from varve import store, validate, views  # noqa: E402


def fresh():
    d = tempfile.mkdtemp(prefix="varve-probe-")
    store.init(d, note="probe")
    return d


def probe(fn):
    """Run one probe; return (state, detail). State is CLOSED/OPEN/CRASH."""
    d = fresh()
    try:
        return fn(d)
    except Exception as exc:  # a probe should never fall over on its own
        return "CRASH", "%s: %s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- the six defects found on 2026-08-23 ------------------------------------

def author_minted_timestamp(d):
    """store.append reserved seq/id/prev/hash but not ts, so an author could
    choose its own position in history. worker.py feeds model JSON straight in."""
    e = store.append(d, {"kind": "hunch", "title": "t", "body": "b",
                         "ts": "2099-01-01T00:00:00Z"})
    if e["ts"] == "2099-01-01T00:00:00Z":
        return "OPEN", "author's ts stored verbatim"
    return "CLOSED", "author's ts discarded, stored %s" % e["ts"]


def permanent_jam(d):
    """The sharp end of the above: rule 1 forbids removing the offending entry
    and no later entry may carry an earlier ts, so one future date jams the log
    permanently. Depends on author_minted_timestamp being open."""
    store.append(d, {"kind": "hunch", "title": "t", "body": "b",
                     "ts": "2099-01-01T00:00:00Z"})
    try:
        store.append(d, {"kind": "hunch", "title": "after", "body": "b"})
        return "CLOSED", "log still writable"
    except ValueError:
        return "OPEN", "log is permanently unwritable"


def verify_crashes_on_bad_ts(d):
    """verify() compared ts against its predecessor without a type check."""
    e = dict(store.read_log(d)[0])
    e.update(seq=2, id="e000002", ts=12345, prev=e["hash"])
    e["hash"] = store.entry_hash(e)
    store._write(d, e)
    try:
        problems = store.verify(d)
    except Exception as exc:
        return "OPEN", "verify raised %s" % type(exc).__name__
    if any("malformed timestamp" in p for p in problems):
        return "CLOSED", "reported instead of raised"
    return "OPEN", "neither reported nor raised: %s" % problems


def unresolvable_file_anchor(d):
    """'entry' anchors were existence-checked from the start; 'file' anchors
    only had to be non-empty, though both are checkable at write time."""
    try:
        store.append(d, {"kind": "observation", "title": "t", "body": "b",
                         "anchors": [{"type": "file", "ref": "no/such/file.py"}]})
        return "OPEN", "anchor to a nonexistent path accepted"
    except ValueError:
        return "CLOSED", "rejected at the gate"


def malformed_prediction(d):
    """A non-dict 'prediction' raised AttributeError inside the gate. worker.py
    catches only ValueError, so this crashed the run rather than feeding back."""
    try:
        store.append(d, {"kind": "prediction", "title": "t", "body": "b",
                         "prediction": "not a dict"})
        return "OPEN", "malformed prediction accepted"
    except ValueError:
        return "CLOSED", "rejected at the gate"
    except AttributeError:
        return "OPEN", "gate raised AttributeError, which worker.py does not catch"


def consumers_crash_on_damaged_log(d):
    """views/web are how a reader inspects a log, including a damaged one."""
    e = dict(store.read_log(d)[0])
    e.update(seq=2, id="e000002", prev=e["hash"])
    e.pop("kind", None)
    e["hash"] = store.entry_hash(e)
    store._write(d, e)
    for name, call in (("views.beliefs", lambda: views.beliefs(d)),
                       ("views.digest", lambda: views.digest(d, days=30))):
        try:
            call()
        except Exception as exc:
            return "OPEN", "%s raised %s" % (name, type(exc).__name__)
    return "CLOSED", "views tolerate a damaged entry"


def corrupt_file_unnamed(d):
    """An unparseable file in log/ raised a bare JSONDecodeError that named a
    column but never the file."""
    with open(os.path.join(d, "log", "000002.json"), "w") as f:
        f.write("{not json")
    try:
        problems = store.verify(d)
    except Exception as exc:
        return "OPEN", "verify raised %s" % type(exc).__name__
    if any("000002.json" in p for p in problems):
        return "CLOSED", "reported, naming the file"
    return "OPEN", "not reported: %s" % problems


def unserialisable_field(d):
    """entry_hash runs after the gate, so a non-JSON value surfaced as
    TypeError out of canonical() — which worker.py does not catch either."""
    try:
        store.append(d, {"kind": "hunch", "title": "t", "body": "b", "extra": {1, 2}})
        return "OPEN", "unserialisable entry accepted"
    except ValueError:
        return "CLOSED", "rejected at the gate"
    except TypeError:
        return "OPEN", "raised TypeError from canonical(), past the gate"


PROBES = [
    ("author-minted timestamp", author_minted_timestamp),
    ("permanent jam from a post-dated entry", permanent_jam),
    ("verify crashes on a non-string ts", verify_crashes_on_bad_ts),
    ("unresolvable file anchor accepted", unresolvable_file_anchor),
    ("malformed prediction crashes the gate", malformed_prediction),
    ("views crash on a damaged log", consumers_crash_on_damaged_log),
    ("corrupt entry file not named", corrupt_file_unnamed),
    ("unserialisable field crashes past the gate", unserialisable_field),
]


def main():
    width = max(len(n) for n, _ in PROBES)
    states = []
    for name, fn in PROBES:
        state, detail = probe(fn)
        states.append(state)
        print("  %-*s  %-6s  %s" % (width, name, state, detail))
    print()
    open_count = states.count("OPEN") + states.count("CRASH")
    print("%d of %d probes report a defect present." % (open_count, len(PROBES)))
    return 1 if open_count else 0


if __name__ == "__main__":
    print("varve gate probe — %s\n" % os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    sys.exit(main())
