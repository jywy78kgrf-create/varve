#!/usr/bin/env python3
"""push_chain3 — stop printing FULL COVERAGE seven lines under a BREAK.

WHY THIS FILE EXISTS. push_chain2.py (e000020) gave the linkage witness a front,
so "unbroken across everything that ever existed" could be told from "unbroken
across what is left". That fix is real and this file keeps it. What e000023 then
found is that the same report has a hole of its own, and it is four lines of
`report()`:

    diverged   = [b for b in breaks if b["cause"] == "DIVERGED"]
    unresolved = [b for b in breaks if b["cause"] in ("UNRESOLVED", "OBJECT-MISSING")]

`link_check` produces a fourth cause, EVENT-GAP — head(i) is a genuine ancestor
of before(i+1), so a push happened between them that the record does not hold —
and EVENT-GAP is in neither list. It falls through both early returns into the
unconditional

    Push chain UNBROKEN: every recorded push names the previous recorded
    push as its parent state, across the whole served window.
    FULL COVERAGE: ... The unbroken line above therefore covers everything.

exit 0. The BREAK is printed and then contradicted by the summary a reader
actually reads. That is e000010's rule a third time in this workshop: a witness
needs a verdict for "I cannot see", and this one had the observation and threw
it away at the report layer.

THE VERDICT THIS FILE ADDS. HOLED, exit 2. A gap is not a divergence — nothing
contradicts anything, and calling it an attack would be e000018's error. It is
also not "all is well": between the two ends of a gap the record witnesses
NOTHING, and that is precisely the window in which a rewrite would leave no
trace here. Both halves have to reach the reader, so HOLED says what is missing
and refuses exit 0.

WHAT A GAP TURNED OUT TO BE, MEASURED. e000023 found four pushes absent from
this repository's events record and declined to guess why. On 2026-08-29 three
of the four had arrived, fifteen to twenty-six hours late, and the ingestion
order proves it: their event ids, assigned at ingest, exceed the id of a push
that happened that evening (see ingest_order.py). So the ordinary cause of a
gap here is a push still in flight, not an eviction and not a rewrite — and the
tell is that the missing state is usually witnessed anyway, as the `before` of
the push after it. GitHub records the STATE while still missing the TRANSITION
into it.

This file reports that distinction, because it changes what a reader should do:
a gap whose missing state is witnessed elsewhere in the page is a wait-and-recheck;
a gap whose missing state appears nowhere is a look-harder.

    python3 push_chain3.py --simulate-gap N
        Drops transition N locally, without relinking, so HOLED can be watched
        firing. e000020's own lesson is that a verdict nobody has seen fire is a
        verdict being trusted rather than checked, and push_chain2.py shipped
        --simulate-evict for exactly that reason.

push_chain.py and push_chain2.py stay as written — e000011, e000013, e000020 and
e000023 anchor them, and the workshop rule is supersede, do not rewrite. This
file imports their `fetch_events`, `transitions`, `link_check`, `expected_first`,
`forge` and `load_transitions`, so the logic below is theirs plus one bucket.

Usage (no credentials):

    python3 push_chain3.py --expect-first @published-roots.txt
    python3 push_chain3.py --expect-first @published-roots.txt --simulate-gap 12
    python3 push_chain3.py --selftest
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from push_chain import fetch_events, transitions, link_check, infer_repo  # noqa: E402
from push_chain2 import expected_first, forge, load_transitions  # noqa: E402
from ingest_order import inversions  # noqa: E402

GAP_CAUSES = ("EVENT-GAP",)
UNRESOLVED_CAUSES = ("UNRESOLVED", "OBJECT-MISSING")


def witnessed_states(ts):
    """Every state this record mentions, in either role."""
    seen = set()
    for t in ts:
        seen.add(t["before"])
        seen.add(t["head"])
    return seen


def report(repo, branch, ts, breaks, served, capped, want_first, want_src,
           evicted, gapped):
    print("%s -- %d push transitions on %s, from %s events GitHub still serves\n"
          % (repo, len(ts), branch,
             served if served is not None else "harvested"))
    if evicted:
        print("  ! SIMULATING EVICTION: dropped the %d oldest transition(s).\n"
              "    Nothing at GitHub changed; this is a local what-if.\n" % evicted)
    if gapped:
        print("  ! SIMULATING A GAP: dropped transition %d without relinking.\n"
              "    Nothing at GitHub changed; this is a local what-if.\n" % gapped)
    print("  oldest state witnessed : %s  (as `before` of %s)"
          % (ts[0]["before"][:12] or "(empty repo)", ts[0]["when"]))
    print("  newest state witnessed : %s  (as `head` of %s)"
          % (ts[-1]["head"][:12], ts[-1]["when"]))
    print("  %d transitions witness %d distinct states, using no git objects."
          % (len(ts), len(ts) + 1))
    print()

    for b in breaks:
        print("! BREAK [%s] %s -> recorded next `before` %s (%s)"
              % (b["cause"], b["from"]["head"][:12], b["to"]["before"][:12],
                 b["to"]["when"]))

    diverged = [b for b in breaks if b["cause"] == "DIVERGED"]
    unresolved = [b for b in breaks if b["cause"] in UNRESOLVED_CAUSES]
    gaps = [b for b in breaks if b["cause"] in GAP_CAUSES]

    have_first = (ts[0]["before"] or "")[:12].lower()
    rolled = bool(want_first) and not have_first.startswith(want_first[:12]) \
        and not want_first[:12].startswith(have_first)

    if diverged:
        print("\n%d transition(s) DIVERGED: GitHub recorded a push whose parent state\n"
              "is not the state it previously recorded. That is a force-push, and it\n"
              "is visible here without reading a single commit object." % len(diverged))
        return 1
    if unresolved:
        print("\nINCONCLUSIVE: %d break(s) could not be classified. Either an event\n"
              "was evicted or history diverged and the objects are already collected."
              % len(unresolved))
        return 2

    # --- the branch push_chain2.py does not have -------------------------
    if gaps:
        seen = witnessed_states(ts)
        inv = inversions(ts)
        print("\nHOLED: %d gap(s) in the middle of the record. Each end links to real\n"
              "history -- git confirms the earlier head is an ANCESTOR of the later\n"
              "`before` -- so nothing here contradicts anything. What is missing is\n"
              "the push event itself, and across a gap this record witnesses nothing."
              % len(gaps))
        print("\nThe UNBROKEN/FULL COVERAGE summary is deliberately NOT printed below.\n"
              "push_chain2.py prints it anyway, exit 0, seven lines under these same\n"
              "BREAK lines (e000023). A gap is not a divergence and it is not health.")
        for b in gaps:
            missing = b["to"]["before"]
            print("\n  gap after %s, before the push at %s"
                  % (b["from"]["head"][:12], b["to"]["when"]))
            if missing in seen:
                print("    the missing state %s IS witnessed here, as the `before` of\n"
                      "    the next recorded push. GitHub holds the state and not the\n"
                      "    transition into it -- the fingerprint of a push still in\n"
                      "    flight. Measured on this repository 2026-08-29: three such\n"
                      "    gaps closed on their own after 15 to 26 hours. Re-check\n"
                      "    before concluding anything." % missing[:12])
            else:
                print("    the missing state %s appears NOWHERE else in this record,\n"
                      "    in either role. That is the harder case: the record does not\n"
                      "    know this state existed at all. Look harder." % missing[:12])
        if inv:
            print("\nCorroboration from the page alone: %d event(s) here ingested OUT OF\n"
                  "ORDER -- an id assigned at ingest that exceeds the id of a push that\n"
                  "happened later. This record demonstrably back-fills, which makes late\n"
                  "ingestion the leading explanation for the gap(s) above. Run\n"
                  "ingest_order.py for the names." % len(inv))
        else:
            print("\nNo out-of-order ingestion is visible in this page, so there is no\n"
                  "positive evidence of back-fill to lean on. The gap stands unexplained.")
        if want_first:
            print("\n  coverage: our front is %s -- %s" % (have_first,
                  "ROLLED, it is not where the record is expected to start" if rolled
                  else "where the record is expected to start,"))
            if not rolled:
                print("            per %s." % want_src)
            print("  Front intact, middle holed: these are independent conditions, and\n"
                  "  only the front was checkable before this file existed.")
        return 2
    # ---------------------------------------------------------------------

    print("Push chain UNBROKEN: every recorded push names the previous recorded\n"
          "push as its parent state, across the whole served window.")

    if not want_first:
        print("\n  coverage: UNKNOWN front. This says the transitions still served link\n"
              "  to each other -- not that they reach back to where they used to. Pass\n"
              "  --expect-first (or @published-roots.txt) to make that checkable.")
        return 0
    if rolled:
        print("\nROLLED: the served window no longer reaches the expected start.\n"
              "  expected front : %s   (%s)\n"
              "  our front      : %s   (%s)\n"
              "The transitions before it are gone from the only party that could\n"
              "re-serve them, so the states they witnessed are no longer witnessed\n"
              "by this record and never will be again. That is an expiry, not a\n"
              "discrepancy, and it is not evidence of a rewrite -- but neither is\n"
              "the UNBROKEN line above evidence about anything older than our front."
              % (want_first[:12], want_src, have_first or "(none)", ts[0]["when"]))
        return 2
    print("\nFULL COVERAGE: our front is %s, which is where the record is expected\n"
          "to start (%s). The unbroken line above therefore covers everything."
          % (have_first, want_src))
    return 0


def drop(ts, at):
    """Remove transition `at` (1-based) without relinking — a hole, not a rewrite."""
    if not 2 <= at <= len(ts) - 1:
        sys.exit("--simulate-gap %d must leave a transition on each side (2..%d)"
                 % (at, len(ts) - 1))
    return ts[:at - 1] + ts[at:]


def gapfree_prefix(ts):
    """The longest leading run of `ts` with no EVENT-GAP in it, and where it stopped.

    The table below only means something against a clean baseline. On 2026-08-29
    this repository's LIVE record already contains a gap, so every row of the
    first version of this table inherited it and read HOLED — including the
    forged rows, which made the closing paragraph of this file false about its
    own output. Truncating to the clean prefix is the honest fix: it is still
    entirely real data, and what it costs is stated in the header rather than
    quietly absorbed.
    """
    breaks = link_check(ts, True)
    gaps = [b for b in breaks if b["cause"] in GAP_CAUSES]
    if not gaps:
        return ts, None
    first = gaps[0]
    for i, t in enumerate(ts):
        if t is first["from"]:
            return ts[:i + 1], first
    return ts, None


def selftest(ts, repo, branch, want_first, want_src, truncated=None):
    """e000018's table shape, run for the verdict this file adds."""
    rows = []

    def run(label, t):
        breaks = link_check(t, True)
        d = [b for b in breaks if b["cause"] == "DIVERGED"]
        u = [b for b in breaks if b["cause"] in UNRESOLVED_CAUSES]
        g = [b for b in breaks if b["cause"] in GAP_CAUSES]
        have = (t[0]["before"] or "")[:12].lower()
        rolled = bool(want_first) and not have.startswith(want_first[:12])
        old = ("DIVERGED/1" if d else "INCONCLUSIVE/2" if u
               else "ROLLED/2" if rolled else "UNBROKEN/0")
        new = ("DIVERGED/1" if d else "INCONCLUSIVE/2" if u else "HOLED/2" if g
               else "ROLLED/2" if rolled else "UNBROKEN/0")
        rows.append((label, len(t), len(breaks), old, new))

    run("untouched", ts)
    for k in (1, 3):
        run("front-evict %d" % k, ts[k:])
    for at in (5, len(ts) // 2, len(ts) - 1):
        if 2 <= at <= len(ts) - 1:
            run("gap at %d" % at, drop(ts, at))
    for at in (5, len(ts) // 2):
        run("forged at %d" % at, forge(ts, at))

    w = max(len(r[0]) for r in rows)
    print("push_chain verdicts under eviction, gaps and forgery")
    print("  %d real transitions harvested from %s (%s)" % (len(ts), repo, branch))
    if truncated:
        print("\n  BASELINE TRUNCATED. The live record carries a real gap after %s\n"
              "  (the push at %s is missing). Every row was inheriting it, so the\n"
              "  table below runs on the %d transitions BEFORE that gap -- real data,\n"
              "  clean baseline. The live verdict is in the non-selftest run, not here."
              % (truncated["from"]["head"][:12], truncated["to"]["when"], len(ts)))
    print()
    print("  %-*s  %5s %7s   %-16s %-16s" % (w, "case", "held", "breaks",
                                             "push_chain2.py", "push_chain3.py"))
    for label, n, nb, old, new in rows:
        print("  %-*s  %5d %7d   %-16s %-16s" % (w, label, n, nb, old, new))
    print("\nREAD THE GAP ROWS. Every one of them produces a BREAK that push_chain2.py\n"
          "PRINTS and then overrules: the two columns differ on exactly the cases\n"
          "where the record has a hole in it, and the older tool exits 0 on all of\n"
          "them. The front-evict and forged rows are unchanged from e000020's table,\n"
          "which is the point -- this is one bucket added, not a new witness.\n"
          "\n"
          "Note the forged rows still read UNBROKEN in both columns. A relinked\n"
          "forgery is invisible to a pure linkage check; that is push_digest's job,\n"
          "and neither tool alone is the witness.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--branch", default="main")
    p.add_argument("--from-harvest", metavar="PATH",
                   help="replay transitions from a push_chain --harvest file; no network")
    p.add_argument("--simulate-evict", type=int, default=0, metavar="K",
                   help="drop the K oldest transitions locally (a what-if)")
    p.add_argument("--simulate-gap", type=int, default=0, metavar="N",
                   help="drop transition N locally WITHOUT relinking, so HOLED fires")
    p.add_argument("--forge-at", type=int, metavar="N",
                   help="locally rewrite transition N and relink N+1 (tamper test)")
    p.add_argument("--expect-first", metavar="SHA|@PATH",
                   help="12-hex prefix the record is expected to start at, or @PATH "
                        "to published digest lines (earliest claim's first= is used)")
    p.add_argument("--selftest", action="store_true",
                   help="run the eviction/gap/forgery table")
    p.add_argument("--token", help="optional; the events API is public")
    a = p.parse_args(argv)

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")

    served = capped = None
    if a.from_harvest:
        ts = load_transitions(a.from_harvest, repo, a.branch)
    else:
        events, capped = fetch_events(repo, a.token)
        served = len(events)
        ts = transitions(events, a.branch)
    if not ts:
        sys.exit("no PushEvents on %r to check with." % a.branch)

    want_first, want_src = expected_first(a.expect_first)

    if a.selftest:
        clean, truncated = gapfree_prefix(ts)
        return selftest(clean, repo, a.branch, want_first, want_src, truncated)

    if a.forge_at is not None:
        ts = forge(ts, a.forge_at)
        print("  ! LOCAL FORGERY at transition %d (relinked). Nothing at GitHub "
              "changed.\n" % a.forge_at)
    if a.simulate_evict:
        if a.simulate_evict >= len(ts):
            sys.exit("--simulate-evict %d would leave nothing" % a.simulate_evict)
        ts = ts[a.simulate_evict:]
    if a.simulate_gap:
        ts = drop(ts, a.simulate_gap)

    breaks = link_check(ts, False if a.from_harvest else True)
    return report(repo, a.branch, ts, breaks, served, capped, want_first, want_src,
                  a.simulate_evict, a.simulate_gap)


if __name__ == "__main__":
    sys.exit(main())
