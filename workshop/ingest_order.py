#!/usr/bin/env python3
"""ingest_order — prove a record was appended to in the PAST, from one page of it.

WHAT THIS IS FOR. Every witness tool in this workshop compares GitHub's events
record to something it saved earlier: push_digest2.py compares today's rolling
root to a root published yesterday, push_chain2.py compares today's oldest
transition to a `first=` published yesterday. Both are self-comparisons across
time, and both need the earlier answer to have survived. This file needs
neither. It takes ONE page of the events feed and asks a question that answers
itself from inside the page:

    did any event enter this record out of order?

THE OBSERVATION IT RESTS ON. A GitHub event carries two independent orderings.

  created_at   when the push happened. The event says so.
  id           a monotonically increasing integer assigned when the event was
               INGESTED into the feed.

If ingestion were prompt, the two would agree: sort by `created_at` and the ids
come out ascending. When they disagree — when event E claims a time earlier than
event F, but carries a HIGHER id than F — then E entered the record after F did,
which is to say E was inserted into the record's past. The record grew somewhere
other than its end.

That is checkable with no second party, no credentials, no git, and no memory of
a previous answer, because the source is being caught disagreeing with ITSELF
rather than with a copy we kept. It is not a general escape from "you need
someone else" (a source that lies consistently across both fields is invisible
here, and nothing in one page can date the page itself). It is the narrower and
still useful thing: a single client can catch a source's self-inconsistency,
just never its consistency.

MEASURED, 2026-08-29, on jywy78kgrf-create/varve. Of 34 push transitions the
API served, three are inversions:

    7a294ccd00b7  pushed 2026-08-28T02:09:41Z  id 19253690259
    af25c3573a84  pushed 2026-08-28T02:21:30Z  id 19257272426
    154c15cb1df2  pushed 2026-08-28T03:58:19Z  id 19259306455

all three ingested AFTER

    e4f711b7dc77  pushed 2026-08-28T17:08:34Z  id 19247963286

i.e. three pushes from the small hours entered the record after a push from
that evening. Independently confirmed: a session at 2026-08-28T17:06Z looked at
this feed and recorded that those exact three were absent (e000023). This tool
would have said so from the page alone, without that entry existing.

Usage:

    python3 ingest_order.py                 # live, this repo
    python3 ingest_order.py --repo owner/name
    python3 ingest_order.py --selftest      # synthetic, no network
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from push_chain import fetch_events, transitions, infer_repo  # noqa: E402


def inversions(ts):
    """Transitions that entered the record after a LATER-timestamped one.

    `ts` is the chronologically sorted transition list every tool here uses
    (sort key: created_at, then event id). Event E is flagged when some F with
    created_at(F) > created_at(E) has id(F) < id(E): F was already in the record
    when E arrived, and F is younger, so E was written into the record's past.

    Implemented as one right-to-left scan carrying the minimum id seen so far,
    which is O(n) and needs nothing but the page.
    """
    out = []
    min_id_after = None
    min_ref_after = None
    for t in reversed(ts):
        tid = int(t["event"])
        if min_id_after is not None and tid > min_id_after:
            out.append({"t": t, "beaten_by": min_ref_after})
        if min_id_after is None or tid < min_id_after:
            min_id_after, min_ref_after = tid, t
    out.reverse()
    return out


def report(repo, branch, ts, inv):
    print("%s -- %d push transitions on %s, checked for out-of-order ingestion\n"
          % (repo, len(ts), branch))
    print("  oldest push : %s  (%s)" % (ts[0]["when"], ts[0]["event"]))
    print("  newest push : %s  (%s)" % (ts[-1]["when"], ts[-1]["event"]))
    print()

    if not inv:
        print("IN ORDER: sorted by push time, every event id ascends. Nothing in\n"
              "this page entered the record behind an event that happened later.\n"
              "\n"
              "That is a statement about what this page can show, and its reach is\n"
              "short in one specific direction: an event still in flight right now\n"
              "is not here to be out of order. A record that is merely BEHIND looks\n"
              "exactly like a record that is complete. Absence is the one thing a\n"
              "page cannot report about itself.")
        return 0

    print("BACKFILLED: %d event%s entered this record after an event that happened\n"
          "LATER. The record was appended to in its own past.\n"
          % (len(inv), "" if len(inv) == 1 else "s"))
    for row in inv:
        t, b = row["t"], row["beaten_by"]
        print("  %s  pushed %s  id %s" % (t["head"][:12], t["when"], t["event"]))
        print("      was not yet in the record when %s (pushed %s, id %s) arrived"
              % (b["head"][:12], b["when"], b["event"]))
    print("\nWHAT THIS DOES AND DOES NOT MEAN. It does not mean anyone tampered with\n"
          "anything; late ingestion is the ordinary explanation and it is the one to\n"
          "prefer. What it does mean is that this record is NOT append-only, so any\n"
          "witness built on the assumption that a prefix of it is stable -- a rolling\n"
          "hash over the first n transitions, say -- can be invalidated by ingestion\n"
          "alone, with nothing at the repository having changed. See push_digest3.py,\n"
          "which turns this signal into a verdict so that a shifted prefix is not\n"
          "reported as a force-push.")
    return 2


def selftest():
    """Three cases, no network. The middle one is the shape measured on 2026-08-29."""
    def mk(when, ev, head):
        return {"before": "0" * 40, "head": head, "when": when, "event": ev}

    cases = [
        ("prompt ingestion", [
            mk("2026-01-01T00:00:00Z", 100, "a" * 40),
            mk("2026-01-01T01:00:00Z", 101, "b" * 40),
            mk("2026-01-01T02:00:00Z", 102, "c" * 40),
        ], 0),
        ("one old push back-filled after a newer one", [
            mk("2026-01-01T00:00:00Z", 100, "a" * 40),
            mk("2026-01-01T01:00:00Z", 103, "b" * 40),   # arrived last
            mk("2026-01-01T02:00:00Z", 102, "c" * 40),
        ], 1),
        ("three back-filled behind one (the varve case)", [
            mk("2026-08-28T02:09:41Z", 19253690259, "7a294ccd00b7" + "0" * 28),
            mk("2026-08-28T02:16:32Z", 19138604835, "eb0b036ea808" + "0" * 28),
            mk("2026-08-28T02:21:30Z", 19257272426, "af25c3573a84" + "0" * 28),
            mk("2026-08-28T03:58:19Z", 19259306455, "154c15cb1df2" + "0" * 28),
            mk("2026-08-28T17:08:34Z", 19247963286, "e4f711b7dc77" + "0" * 28),
        ], 3),
    ]
    width = max(len(c[0]) for c in cases)
    print("ingest_order selftest -- synthetic pages, no network\n")
    print("  %-*s  %5s  %9s  %8s" % (width, "case", "events", "expected", "found"))
    ok = True
    for label, ts, want in cases:
        got = len(inversions(ts))
        ok = ok and got == want
        print("  %-*s  %5d  %9d  %8d%s"
              % (width, label, len(ts), want, got, "" if got == want else "   <-- FAIL"))
    print("\nThe third case is this repository's real ids and push times, held here\n"
          "so the measurement stays reproducible after the events window rolls and\n"
          "the live feed can no longer show it. It is a fixture, not a fresh reading.")
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--branch", default="main")
    p.add_argument("--selftest", action="store_true",
                   help="run the synthetic table; no network")
    p.add_argument("--token", help="optional; the events API is public")
    a = p.parse_args(argv)

    if a.selftest:
        return selftest()

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")
    events, _ = fetch_events(repo, a.token)
    ts = transitions(events, a.branch)
    if not ts:
        sys.exit("no PushEvents on %r to check with." % a.branch)
    return report(repo, a.branch, ts, inversions(ts))


if __name__ == "__main__":
    sys.exit(main())
