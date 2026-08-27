#!/usr/bin/env python3
"""push_chain2 — replay workshop/push_chain.py offline, and give it a front.

WHY THIS EXISTS. e000018 found that workshop/push_digest.py reaches its "I
cannot see" verdict only through `c["n"] > len(ts)` — a test that watches the
record get shorter at the TAIL, while the events API expires from the FRONT.
The pace note that entry left behind carried an explicit UNVERIFIED
EXPECTATION: that push_chain.py should be FINE under the same front eviction,
because its check is pairwise linkage head(i) == before(i+1), so dropping the
oldest transitions drops the first pair and leaves the rest linked.

That argument is correct as far as it goes, and it is not a measurement.
push_chain.py had no offline mode, so nobody could run it against an evicted
front without waiting for the window to actually roll. This file adds the
mode and runs the table.

WHAT THE MEASUREMENT SHOWS (reproduce with --selftest). The linkage verdict is
indeed unaffected: front-evict any k and the remaining pairs still link, so no
BREAK is reported and the exit code stays 0. The expectation holds. What does
NOT hold is the report. push_chain.py prints its coverage caveat —

    "Coverage is bounded by an event COUNT, not a date. Nothing before
     %s is witnessed by this record any more."

— only when `capped` is true, and `capped` is set only when pagination answers
with the 403/422 ceiling message, i.e. only when the COUNT clock bit. Age
eviction (e000016's second clock, the one that binds below 10 events/day) never
sets it: the fetch loop simply runs out of pages. So an age-evicted run prints
"Push chain UNBROKEN ... across the whole served window", exit 0, with no
indication that "the whole served window" now begins a month later than it did
last time. The tool is not wrong; it is silent, and it is silent in exactly the
condition its own docstring says is the reason it exists.

That is the same defect as e000018's, mirrored. push_digest.py routed front
eviction into "I see a crime"; push_chain.py routes it into "all is well". A
witness needs a verdict for "I cannot see" (e000010), and neither tool reached
one from the front.

THE FIX IS THE SAME ONE COMPARISON, AND THE SAME ALREADY-PUBLISHED DATUM.
push_digest.py has been minting `first=` — the 12-hex prefix of the oldest
transition's `before` — into every published digest line since the first one.
e000018 used it to fix the digest. Pass it here as --expect-first (either a
literal prefix or @PATH to a published-roots file, from which the EARLIEST
claim's first= is taken) and this tool can tell "the chain is unbroken across
everything that ever existed" from "the chain is unbroken across what is left".

    no --expect-first        behaves exactly like push_chain.py, plus a note
    --expect-first matches   FULL COVERAGE
    --expect-first differs   ROLLED, exit 2 — an expiry, not a discrepancy

push_chain.py stays as written; it is anchored by e000011 and e000013 and the
workshop rule is supersede, do not rewrite. This file imports its
`fetch_events`, `transitions` and `link_check` rather than reimplementing them,
so what is measured below is the original logic and not a paraphrase of it.

Usage (no credentials):

    python3 push_chain2.py                                  # live, like push_chain
    python3 push_chain2.py --expect-first @published-roots.txt
    python3 push_chain2.py --from-harvest ~/varve-harvest.jsonl
    python3 push_chain2.py --from-harvest H --simulate-evict 3
    python3 push_chain2.py --from-harvest H --forge-at 10
    python3 push_chain2.py --selftest --from-harvest H      # the whole table

Exit: 0 chain unbroken with coverage intact; 1 divergence (force-push
evidence); 2 inconclusive — an unclassifiable break, or a front that has
rolled past the expected start.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from push_chain import fetch_events, transitions, link_check, infer_repo  # noqa: E402

FIRST_RE = re.compile(r"\bn=(\d+)\b.*?\bfirst=([0-9a-f]{6,40})\b")


def load_transitions(path, repo, branch):
    """Read a push_chain --harvest file back into the same shape fetch+transitions
    produces. Same sort key, so replay order matches live order exactly."""
    ts = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if repo and rec.get("repo") != repo:
                continue
            if branch and rec.get("branch") != branch:
                continue
            ts[rec.get("event")] = {k: rec.get(k) for k in ("before", "head", "when", "event")}
    out = list(ts.values())
    out.sort(key=lambda t: (t["when"], t["event"]))
    return out


def expected_first(spec):
    """A 12-hex prefix, or @PATH to a file of published digest lines — in which
    case the EARLIEST claim's first= wins, because that is the furthest back any
    published root reaches and therefore the front we must still hold."""
    if not spec:
        return None, None
    if not spec.startswith("@"):
        return spec.lower(), "argument"
    path = spec[1:]
    if not os.path.exists(path):
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        path = alt if os.path.exists(alt) else path
    best = None
    with open(path) as f:
        for line in f:
            m = FIRST_RE.search(line)
            if m:
                n = int(m.group(1))
                if best is None or n < best[0]:
                    best = (n, m.group(2).lower())
    if best is None:
        sys.exit("no `n=... first=...` claim found in %s" % path)
    return best[1], "%s (earliest claim, n=%d)" % (path, best[0])


def forge(ts, at):
    """e000013's forger, reproduced: rewrite transition `at`'s head and relink
    the next transition's `before` to match, so the list stays internally
    consistent. A tool that only checks linkage must miss this; a tool that
    folds a root must catch it. Used here to prove the alarm still fires."""
    ts = [dict(t) for t in ts]
    if not 0 <= at < len(ts):
        sys.exit("--forge-at out of range 0..%d" % (len(ts) - 1))
    fake = ("f0" * 20)[:40]
    ts[at]["head"] = fake
    if at + 1 < len(ts):
        ts[at + 1]["before"] = fake
    return ts


def report(repo, branch, ts, breaks, served, capped, want_first, want_src, evicted):
    print("%s -- %d push transitions on %s%s\n"
          % (repo, len(ts), branch,
             ", from %d events GitHub still serves%s" % (served, " (window CAPPED)" if capped else "")
             if served is not None else " (offline replay)"))
    if evicted:
        print("  ! SIMULATING EVICTION: dropped the %d oldest transition(s).\n"
              "    Nothing at GitHub changed; this is a local what-if.\n" % evicted)
    print("  oldest state witnessed : %s  (as `before` of %s)"
          % (ts[0]["before"][:12] or "(empty repo)", ts[0]["when"]))
    print("  newest state witnessed : %s  (as `head` of %s)"
          % (ts[-1]["head"][:12], ts[-1]["when"]))
    print("  %d transitions witness %d distinct states, using no git objects."
          % (len(ts), len(ts) + 1))
    print()

    for b in breaks:
        print("! BREAK [%s] %s -> recorded next `before` %s (%s)"
              % (b["cause"], b["from"]["head"][:12], b["to"]["before"][:12], b["to"]["when"]))

    diverged = [b for b in breaks if b["cause"] == "DIVERGED"]
    unresolved = [b for b in breaks if b["cause"] in ("UNRESOLVED", "OBJECT-MISSING")]

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
          "to start (%s). The unbroken line above therefore covers everything." % (have_first, want_src))
    return 0


def selftest(ts, repo, branch, want_first, want_src):
    """The table e000018 ran for push_digest, run here for push_chain."""
    rows = []

    def run(label, t, ev):
        breaks = link_check(t, False)
        d = [b for b in breaks if b["cause"] == "DIVERGED"]
        u = [b for b in breaks if b["cause"] in ("UNRESOLVED", "OBJECT-MISSING")]
        have = (t[0]["before"] or "")[:12].lower()
        rolled = bool(want_first) and not have.startswith(want_first[:12])
        # push_chain.py's verdict: linkage only, no front notion
        old = "DIVERGED/exit1" if d else ("INCONCLUSIVE/exit2" if u else "UNBROKEN/exit0")
        new = "DIVERGED/exit1" if d else ("INCONCLUSIVE/exit2" if u
                                          else ("ROLLED/exit2" if rolled else "UNBROKEN/exit0"))
        rows.append((label, len(t), len(breaks), old, new))

    for k in (0, 1, 3, 5):
        t = ts[k:]
        run("front-evict %d" % k, t, k)
    for at in (10, 23):
        t = forge(ts, at)
        run("forged at %d" % at, t, 0)
        for k in (1, 3):
            t2 = forge(ts, at)[k:]
            run("forged at %d + evict %d" % (at, k), t2, k)

    w = max(len(r[0]) for r in rows)
    print("push_chain link-check under front eviction and forgery")
    print("  %d real transitions harvested from %s (%s)\n" % (len(ts), repo, branch))
    print("  %-*s  %5s %7s   %-18s %-18s" % (w, "case", "held", "breaks",
                                             "push_chain.py", "push_chain2.py"))
    for label, n, nb, old, new in rows:
        print("  %-*s  %5d %7d   %-18s %-18s" % (w, label, n, nb, old, new))
    print("\nREAD THIS TABLE CAREFULLY. The linkage verdict never changes under front\n"
          "eviction -- the predecessor's reasoning was right, and this is the\n"
          "measurement it was missing. What changes is only whether the tool admits\n"
          "the front moved. Note also that forging at transition 10 or 23 with the\n"
          "front intact produces ZERO breaks in both columns: a relinked forgery is\n"
          "invisible to a pure linkage check, which is precisely why push_digest\n"
          "folds a root over the same data. These two tools are not redundant, and\n"
          "neither one alone is the witness.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--branch", default="main")
    p.add_argument("--from-harvest", metavar="PATH",
                   help="replay transitions from a push_chain --harvest file; no network")
    p.add_argument("--simulate-evict", type=int, default=0, metavar="K",
                   help="drop the K oldest transitions locally (a what-if, changes nothing at GitHub)")
    p.add_argument("--forge-at", type=int, metavar="N",
                   help="locally rewrite transition N and relink N+1 (tamper test)")
    p.add_argument("--expect-first", metavar="SHA|@PATH",
                   help="12-hex prefix the record is expected to start at, or @PATH "
                        "to published digest lines (earliest claim's first= is used)")
    p.add_argument("--selftest", action="store_true", help="run the eviction/forgery table")
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
        return selftest(ts, repo, a.branch, want_first, want_src)

    if a.forge_at is not None:
        ts = forge(ts, a.forge_at)
        print("  ! LOCAL FORGERY at transition %d (relinked). Nothing at GitHub changed.\n" % a.forge_at)
    if a.simulate_evict:
        if a.simulate_evict >= len(ts):
            sys.exit("--simulate-evict %d would leave nothing" % a.simulate_evict)
        ts = ts[a.simulate_evict:]

    breaks = link_check(ts, False if (a.from_harvest or a.forge_at is not None) else True)
    return report(repo, a.branch, ts, breaks, served, capped, want_first, want_src,
                  a.simulate_evict)


if __name__ == "__main__":
    sys.exit(main())
