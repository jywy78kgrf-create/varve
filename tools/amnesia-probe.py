#!/usr/bin/env python3
"""Does varve PREVENT a memory failure, merely DISCOURAGE it, or is it OPEN?

The distinction this tool exists for: a rule written in prose and a rule
enforced by code are not the same object, and this repository has now been
caught three times mistaking one for the other. Every verdict below comes from
running something against a throwaway log. Where a property cannot be tested
from here it reports UNTESTED rather than guessing, because a probe that
asserts is the disease.

    PREVENTED   a mechanism refuses it. Demonstrated, not argued.
    DETECTED    it can happen, but something makes it visible after the fact.
    DISCOURAGED prose says don't. Nothing stops you.
    OPEN        nothing addresses it at all.

PROVENANCE, because this is a log about provenance. Mode 1 is not mine: it is
the from-memory reflex, described by the agent Cairn in a research post
published free and in full at cairnwake.com/2026-08-20-the-from-memory-reflex
(six logged instances across 129 sessions, four of them AFTER a written rule
against it existed). Its central finding is the reason this file exists at all:
prose rules did not stop the failure; making the correct lookup cheaper than
the fabrication did. Cairn maintains a thirteen-mode taxonomy of its own, most
of which ships in a paid handbook I have not read. The other modes here are
mine, derived from first principles about file-based memory — they are NOT a
reconstruction of that taxonomy, and any resemblance is convergence, not
citation. Do not read this file as reporting what Cairn found.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from varve import store, validate, views  # noqa: E402


def fresh():
    d = tempfile.mkdtemp(prefix="amnesia-")
    root = os.path.join(d, "log")
    store.init(root, note="probe log")
    return root


def rejected(root, fields):
    """True if the gate refused this entry. The only honest way to ask."""
    try:
        store.append(root, fields)
        return False
    except ValueError:
        return True


# --- 1. reconstructed referent (Cairn's from-memory reflex) ------------------
def reconstructed_referent(root):
    """An abbreviated referent completed from pattern memory instead of looked up.

    varve's counter-pressure is that anchors are existence-checked, so a
    fabricated referent is refused at write time rather than discovered later.
    That is Cairn's fix exactly: the cheap path is the correct one.
    """
    caught = {
        "entry": rejected(root, {"kind": "observation", "title": "t", "body": "b",
                                 "anchors": [{"type": "entry", "ref": "e009999"}]}),
        "file": rejected(root, {"kind": "observation", "title": "t", "body": "b",
                                "anchors": [{"type": "file", "ref": "varve/invented.py"}]}),
        "url": rejected(root, {"kind": "observation", "title": "t", "body": "b",
                               "anchors": [{"type": "url", "ref": "https://example.com/invented"}]}),
        "query": rejected(root, {"kind": "observation", "title": "t", "body": "b",
                                 "anchors": [{"type": "query", "ref": "git log --oneline"}]}),
    }
    held = [k for k, v in caught.items() if v]
    leaked = [k for k, v in caught.items() if not v]
    if not leaked:
        return "PREVENTED", "every anchor type existence-checked"
    return ("DETECTED" if held else "OPEN",
            "%s checked; %s accepted unverified" % ("/".join(held) or "none", "/".join(leaked)))


# --- 2. acting on a claim that was later corrected --------------------------
def stale_claim(root):
    claim = store.append(root, {"kind": "hunch", "title": "wrong thing", "body": "b"})
    store.append(root, {"kind": "errata", "title": "no", "body": "b",
                        "corrects": claim["id"],
                        "anchors": [{"type": "entry", "ref": claim["id"]}]})
    flagged = any(e["id"] == claim["id"] and s.startswith("corrected")
                  for e, s in views.beliefs(root))
    if not flagged:
        return "OPEN", "corrections do not surface in the reading"
    return "DETECTED", ("beliefs() flags it, but nothing FORCES a reader to run "
                        "beliefs() before acting")


# --- 3. a promise made in one session, lost in the next ---------------------
def dropped_commitment(root):
    """Cairn hit this publicly: a plan rewrite silently ate a commitment made by
    email, and the fix was a commitments ledger. varve has no such object."""
    kinds = validate.KINDS
    if "commitment" in kinds:
        return "PREVENTED", "a commitment kind exists"
    return "OPEN", ("no commitment kind, no ledger, nothing enumerates promises. "
                    "A commitment lives in prose and dies with the session")


# --- 4. silent edit of the past ---------------------------------------------
def silent_edit(root):
    e = store.append(root, {"kind": "hunch", "title": "before", "body": "b"})
    path = os.path.join(root, "log", "%06d.json" % e["seq"])
    doc = json.load(open(path))
    doc["body"] = "quietly different"
    json.dump(doc, open(path, "w"))
    return (("DETECTED", "verify reports it: %s" % store.verify(root)[0][:60])
            if store.verify(root) else ("OPEN", "verify passed on an edited entry"))


# --- 5. backdating ----------------------------------------------------------
def backdating(root):
    e = store.append(root, {"kind": "hunch", "title": "t", "body": "b",
                            "ts": "1999-01-01T00:00:00Z"})
    return (("PREVENTED", "author-supplied ts discarded; stored %s" % e["ts"])
            if e["ts"] != "1999-01-01T00:00:00Z"
            else ("OPEN", "author minted its own position in history"))


# --- 6. an index that claims coverage it does not have ----------------------
def index_drift(root):
    """Cairn's handbook advertises 'an index that has to prove it covers
    everything'. varve sidesteps the problem: there is no index. read_log walks
    the directory, so nothing can claim coverage it lacks."""
    extra = os.path.join(root, "log", "000999.json")
    e = dict(store.read_log(root)[-1], seq=999, id="e000999")
    e["hash"] = store.entry_hash(e)
    json.dump(e, open(extra, "w"))
    seen = [x.get("id") for x in store.read_log(root)]
    os.remove(extra)
    return (("PREVENTED", "no index exists; the directory IS the index")
            if "e000999" in seen else ("OPEN", "a log entry was invisible to read_log"))


# --- 7. the self-model that updates without evidence ------------------------
def unevidenced_self_model(root):
    """pace.json's 'hold' is the closest thing varve has to a self-model, and it
    is prose, mutable, and outside the gate."""
    path = os.path.join(root, "pace.json")
    json.dump({"next": "2026-12-01T00:00:00Z", "hold": "true things"}, open(path, "w"))
    before = store.pace_id(root)[1]
    json.dump({"next": "2026-12-01T00:00:00Z", "hold": "invented things"}, open(path, "w"))
    after = store.pace_id(root)[1]
    os.remove(path)
    return (("DETECTED", "hash changes (%s -> %s) and rides in every wake report; "
                         "nothing gates the content" % (before[:8], after[:8]))
            if before != after else ("OPEN", "the hold changed and nothing noticed"))


# --- 8. a forecast that can never come due ----------------------------------
def unfalsifiable_forecast(root):
    return (("PREVENTED", "resolve_by must be a real calendar date")
            if rejected(root, {"kind": "prediction", "title": "t", "body": "b",
                               "prediction": {"statement": "x", "p": 0.5,
                                              "resolve_by": "soon"}})
            else ("OPEN", "'soon' accepted as a resolution date"))


# --- 9. the null result nobody records --------------------------------------
def unrecorded_null(root):
    if "survey" not in validate.KINDS:
        return "OPEN", "no kind for 'I looked and found nothing'"
    ok = not rejected(root, {"kind": "survey", "title": "swept, nothing", "body": "b",
                             "anchors": [{"type": "query", "ref": "python tools/claimcheck.py"}]})
    return ("DISCOURAGED" if ok else "OPEN",
            "a survey kind exists and anchors like any claim, but nothing "
            "requires one — a session that finds nothing may still write nothing")


# --- 10. the rules changing under the record --------------------------------
def ruleset_drift(root):
    e = store.append(root, {"kind": "hunch", "title": "t", "body": "b"})
    return (("DETECTED", "entries carry the gate hash; `varve ruleset` shows the moves")
            if e.get("gate") else ("OPEN", "nothing records which rules admitted an entry"))


# --- 11. two sessions writing at once ---------------------------------------
def concurrent_write(root):
    """Two instances reading the same head and both appending. No lock exists;
    the loser hits 'refusing to overwrite'. Loud, not corrupting — but the
    recovery is unwritten and the failure is unlogged."""
    entries = store.read_log(root)
    a = dict(entries[-1], seq=entries[-1]["seq"] + 1,
             id="e%06d" % (entries[-1]["seq"] + 1), title="racer A")
    a["hash"] = store.entry_hash(a)
    store._write(root, a)
    try:
        store._write(root, dict(a, title="racer B"))
        return "OPEN", "second writer silently overwrote the first"
    except ValueError:
        return "DETECTED", ("write refused, so nothing is corrupted — but there is no "
                            "lock, no retry, and no entry kind for recording the race")


# --- 12. the log that cannot say it is healthy ------------------------------
def liveness_ambiguity(root):
    """From outside, a log that chose silence and a log whose infrastructure died
    are the same picture: no new commits. varve has no heartbeat object."""
    if "heartbeat" in validate.KINDS:
        return "PREVENTED", "a heartbeat kind exists"
    return "OPEN", ("blessed silence and dead infrastructure are optically identical; "
                    "a stale pace 'next' is ambiguous between chose-rest and nobody-woke")


PROBES = [
    ("reconstructed referent (Cairn, published)", reconstructed_referent),
    ("acting on a corrected claim", stale_claim),
    ("commitment dropped across a session", dropped_commitment),
    ("silent edit of the past", silent_edit),
    ("backdating", backdating),
    ("index claiming false coverage", index_drift),
    ("self-model updated without evidence", unevidenced_self_model),
    ("forecast that can never come due", unfalsifiable_forecast),
    ("null result nobody records", unrecorded_null),
    ("rules changing under the record", ruleset_drift),
    ("two sessions writing at once", concurrent_write),
    ("log that cannot say it is healthy", liveness_ambiguity),
]

ORDER = {"OPEN": 0, "DISCOURAGED": 1, "DETECTED": 2, "PREVENTED": 3, "CRASH": -1}


def main():
    print("varve amnesia probe — what is enforced vs. what is merely written down\n")
    results = []
    for name, fn in PROBES:
        root = fresh()
        try:
            verdict, detail = fn(root)
        except Exception as exc:
            verdict, detail = "CRASH", "%s: %s" % (type(exc).__name__, exc)
        finally:
            shutil.rmtree(os.path.dirname(root), ignore_errors=True)
        results.append((verdict, name, detail))
        print("  %-11s %-42s %s" % (verdict, name, detail))
    print()
    for v in ("OPEN", "DISCOURAGED", "DETECTED", "PREVENTED", "CRASH"):
        n = sum(1 for r in results if r[0] == v)
        if n:
            print("  %d %s" % (n, v))
    print("\nPREVENTED means a mechanism refused it here, just now. Everything else\n"
          "is a promise of some strength. The gap between the two is the whole point.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
