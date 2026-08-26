#!/usr/bin/env python3
"""push_digest2 — the same digest, with a verdict for "I cannot see".

WHY THIS FILE EXISTS. workshop/push_digest.py (e000013) carries the push-
transition witness out of the sandbox as one 64-hex root, and its verify path
has three verdicts: MATCH, MISMATCH, and UNCOVERED. UNCOVERED is reached by
exactly one test:

    if c["n"] > len(ts):   # the claim covers more transitions than we hold

That test only sees the record getting shorter at the END. The events API does
not expire from the end. It expires from the FRONT — the oldest events drop out
first, under either of the two clocks e000016 established (a 300-event count and
an independent age window). When the front moves, `len(ts)` may still exceed the
claim's n, so the claim is routed to the root comparison, and the root over
transitions 2..22 is not the published root over 1..21. The tool then prints
MISMATCH and lists as a cause:

    - a force-push replaced history, so a recorded push no longer
      names the state GitHub had previously recorded.

against a repository nobody has touched.

MEASURED, not argued. Against this repository's real 27 transitions, harvested
from GitHub this session, with the oldest events dropped and nothing else
changed:

    dropped 0   MATCH     n=21, n=25, n=26                  exit 0
    dropped 1   MISMATCH  n=21, n=25, n=26                  exit 1
    dropped 3   MISMATCH  n=21;  UNCOVERED n=25, n=26       exit 1
    dropped 5   MISMATCH  n=21;  UNCOVERED n=25, n=26       exit 1

One evicted event is enough to turn every published root in the archive into a
false accusation. Note the shape of the dropped-3 and dropped-5 rows: n=25 and
n=26 get the right verdict there, but only because 25 and 26 happen to exceed
the 24 transitions left. The correctness is a coincidence of arithmetic, not a
check — it fails precisely for the claims that reach furthest back, which are
the ones the archive exists to hold.

THE FIX IS ONE COMPARISON, AND THE DATUM WAS ALREADY BEING PUBLISHED. Every
digest line carries `first=` — the 12-hex prefix of the oldest transition's
`before`:

    varve-push-digest v1 owner/repo main n=26 first=4e075f168b36 last=... root=...

push_digest.py mints that field (digest_line) and parses it (CLAIM_RE names the
group) and never reads it again. Nothing in its verify path consults `first`.
So the fact needed to tell "the window rolled past this claim" from "the record
was rewritten" has been present in every line the archive holds since the first
one was published, unused. If our oldest transition's `before` is not the claim's
`first`, we are not looking at a list that starts where the claim starts, and the
only honest verdict is UNCOVERED.

WHAT THIS DOES NOT DO, so nobody inherits a fourth overclaim in this family
(e000011 -> e000012 -> e000016 is the running tally). It does not restore
checkability. Once the front evicts, a root folded from transition 1 is
permanently unverifiable against the live API, because the bytes it commits to
are gone from the only party that could re-serve them. This changes a false
accusation into an honest "I cannot see," and that is all it changes. The
archive of published roots still has a hard expiry; the previous behaviour
disguised that expiry as an attack, which is worse than useless, because the
expiry is a certainty and the attack is not.

Nor is it signed, nor does it defend the log against its writer. Everything
push_digest.py's docstring disclaims, this disclaims too.

ONE CORRECTION TO THE INHERITED DOCSTRING while I am here: push_digest.py says
"the event window is a count, not a duration," and push_chain.py says the same.
e000016 corrected that — there are two clocks, and below 300/W events per day
the calendar binds, which is where this repository lives by roughly ten to one.
Those two files are anchored by e000011 and e000013 and stay as written, per the
workshop's supersede-don't-rewrite rule. This file supersedes their retention
model only, exactly as workshop/events_clock.py already does.

The hashing primitives are IMPORTED from push_digest rather than reimplemented,
deliberately: the canonical bytes and the rolling-root construction must stay
bit-identical or the three roots already published stop verifying. Only the
verdict logic is new.

Usage (no credentials; the events API is public for a public repo):

    python3 push_digest2.py --verify published-roots.txt
    python3 push_digest2.py --verify published-roots.txt --simulate-evict 1
    python3 push_digest2.py --from-harvest ~/h.jsonl --offline --verify ...

`--simulate-evict K` drops the K oldest transitions after fetching and before
verifying. It changes nothing at GitHub; it is how the table above was produced
and how anyone can reproduce it today rather than waiting for the window to roll.

Exit: 0 MATCH; 1 MISMATCH (the record we CAN see disagrees); 2 UNCOVERED or
INCONCLUSIVE (we cannot see far enough to say).
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from push_digest import (  # noqa: E402
    MAGIC, VERSION, digest_line, fetch_events, infer_repo, merge, parse_claims,
    read_harvest, roots, transitions,
)


def classify(claims, ts):
    """Sort claims into (checked, uncovered) with a reason for each uncovered.

    Two ways to be uncovered, and push_digest.py implements only the second:

      FRONT  the claim's `first` is not our oldest transition's `before`, so the
             window has rolled past where this claim begins. The root cannot be
             recomputed at all; the bytes are gone.
      TAIL   the claim covers more transitions than we hold.

    FRONT is checked before TAIL because it is the stronger statement: if the
    list does not start where the claim starts, the length is beside the point.
    """
    ours_first = ts[0]["before"][:12] if ts else ""
    checked, uncovered = [], []
    for c in claims:
        if not ts:
            uncovered.append((c, "FRONT", "we hold no transitions at all"))
        elif c["first"] != "(empty)" and c["first"] != ours_first:
            uncovered.append((c, "FRONT",
                              "claim starts at %s; our oldest is %s"
                              % (c["first"], ours_first)))
        elif c["n"] > len(ts):
            uncovered.append((c, "TAIL",
                              "claim covers %d; we hold %d" % (c["n"], len(ts))))
        else:
            rs = roots(c["repo"], c["branch"], ts)
            checked.append((c, rs[c["n"]] == c["root"], rs[c["n"]]))
    return checked, uncovered


def report(checked, uncovered, ts):
    for c, ok, ours in checked:
        print("  %-9s n=%-5d %s" % ("MATCH" if ok else "MISMATCH", c["n"],
                                    c["root"][:24] + ("" if ok else " != " + ours[:24])))
    for c, why, detail in uncovered:
        print("  %-9s n=%-5d %s   [%s: %s]"
              % ("UNCOVERED", c["n"], c["root"][:24], why, detail))
    print()

    bad = [c for c, ok, _ in checked if not ok]
    good = [c for c, ok, _ in checked if ok]
    front = [u for u in uncovered if u[1] == "FRONT"]

    if not bad and not uncovered:
        newest = max(good, key=lambda c: c["n"])
        print("MATCH: every published root re-derives from GitHub's record today\n"
              "(%d claim%s, newest covering %d transitions)."
              % (len(good), "" if len(good) == 1 else "s", newest["n"]))
        print("\n  we now hold  : %d transitions (%d past the newest claim)"
              % (len(ts), len(ts) - newest["n"]))
        print("  newest state : %s  (%s)" % (ts[-1]["head"][:12], ts[-1]["when"]))
        if len(ts) > newest["n"]:
            print("\nPrefix-stability held across %d further pushes: roots published\n"
                  "when the record was shorter still verify against it now."
                  % (len(ts) - newest["n"]))
        return 0

    if bad:
        print("MISMATCH: %d published root%s not re-derive from the record\n"
              "GitHub serves today." % (len(bad), " does" if len(bad) == 1 else "s do"))
        first_bad = min(bad, key=lambda c: c["n"])
        last_good = max(good, key=lambda c: c["n"]) if good else None
        if last_good and last_good["n"] < first_bad["n"]:
            print("\nBracketed by the archive: transitions 1..%d still agree, and\n"
                  "something within 1..%d does not. The divergence is in the range\n"
                  "(%d, %d]." % (last_good["n"], first_bad["n"],
                                 last_good["n"], first_bad["n"]))
        else:
            # push_digest.py printed "Only one root was checkable" here
            # unconditionally, which is false whenever several were checked and
            # all of them failed -- the commonest case under front eviction.
            print("\n%d root%s checkable and %s failed, so there is no agreeing\n"
                  "prefix to bracket against: this says THAT the record changed and\n"
                  "cannot say WHERE. A root hashes a whole prefix; only a root that\n"
                  "still MATCHES can pin the near end of a range."
                  % (len(checked), " was" if len(checked) == 1 else "s were",
                     "it" if len(checked) == 1 else "all of them"))
        if front:
            print("\nRead that alongside the UNCOVERED rows above: the window has\n"
                  "already rolled past the start of %d claim%s. When the front has\n"
                  "moved, a MISMATCH on a claim that still fits by length is the\n"
                  "same eviction seen from the other side, NOT a second finding."
                  % (len(front), "" if len(front) == 1 else "s"))
        print("\nCauses this tool cannot tell apart, in the order they are likely:\n"
              "  - the published line was miscopied or truncated in transit;\n"
              "  - GitHub withdrew or re-served an event differently;\n"
              "  - a force-push replaced history, so a recorded push no longer\n"
              "    names the state GitHub had previously recorded.\n"
              "Only the last is an attack, and ruling the first two out is a\n"
              "human's job, not this tool's.")
        return 1

    # Nothing checked out badly; we simply could not see far enough.
    if front:
        print("UNCOVERED: the event window has rolled past the start of %d claim%s.\n"
              "Our oldest transition is %s, published lines begin at %s.\n"
              "These roots are no longer checkable against GitHub and never will be\n"
              "again -- the bytes they commit to have been evicted. That is an\n"
              "expiry, not a discrepancy; this tool refuses to call it one."
              % (len(front), "" if len(front) == 1 else "s",
                 ts[0]["before"][:12] if ts else "(none)",
                 ", ".join(sorted({u[0]["first"] for u in front}))))
    else:
        print("INCONCLUSIVE: %d claim%s cover more transitions than we can see (%d)."
              % (len(uncovered), "" if len(uncovered) == 1 else "s", len(ts)))
    if good:
        print("\nWhat did check out: %d root%s, up to n=%d."
              % (len(good), "" if len(good) == 1 else "s",
                 max(c["n"] for c in good)))
    else:
        print("\nNothing in the archive was checkable at all.")
    return 2


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo")
    p.add_argument("--branch", default="main")
    p.add_argument("--from-harvest", metavar="PATH")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--simulate-evict", type=int, default=0, metavar="K",
                   help="drop the K oldest transitions before verifying; "
                        "changes nothing at GitHub")
    p.add_argument("--verify", metavar="CLAIM", required=True,
                   help="a published digest line, or a file of them")
    p.add_argument("--token")
    a = p.parse_args(argv)

    text = a.verify
    if os.path.exists(text):
        with open(text) as f:
            text = f.read()
    claims = parse_claims(text)
    for c in claims:
        if c["ver"] != VERSION:
            sys.exit("a claim is %s, this tool speaks %s" % (c["ver"], VERSION))

    repo = a.repo or claims[-1]["repo"] or infer_repo()
    branch = a.branch if a.branch != "main" else claims[-1]["branch"]

    live, capped = ([], False) if a.offline else fetch_events(repo, a.token)
    ts = merge(read_harvest(a.from_harvest, repo, branch),
               transitions(live, branch))

    if a.simulate_evict:
        dropped, ts = ts[:a.simulate_evict], ts[a.simulate_evict:]
        print("  ! SIMULATING EVICTION: dropped the %d oldest transition%s "
              "(%s..)\n    Nothing at GitHub changed; this is a local what-if.\n"
              % (len(dropped), "" if len(dropped) == 1 else "s",
                 dropped[0]["before"][:12] if dropped else "-"))

    print("%s %s -- %s %s, %d transition%s held%s\n"
          % (MAGIC, VERSION, repo, branch, len(ts),
             "" if len(ts) == 1 else "s",
             " (window CAPPED)" if capped else ""))
    rc = report(*classify(claims, ts), ts=ts)
    if ts:
        print("\n  our line today: %s" % digest_line(repo, branch, ts,
                                                     roots(repo, branch, ts)[-1]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
