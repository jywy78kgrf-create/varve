#!/usr/bin/env python3
"""push_digest3 — the same digest, with a verdict for "the witness back-filled".

WHY THIS FILE EXISTS. push_digest2.py (e000018) fixed the FRONT: when the events
window evicts its oldest entries, a published root can no longer be recomputed,
and the tool now says UNCOVERED instead of accusing a quiet repository of a
force-push. That fix is real and it still works. It assumed, as push_digest.py
did before it, that the only way a prefix moves is by losing its front.

It does not hold. Measured on this repository, 2026-08-29:

    MATCH     n=21 ... n=28
    MISMATCH  n=30    53d32e5d20bc9e570e877356 != d8caa510473bbf579d17d642

The n=30 root was published 2026-08-28T04:0X Z and verified MATCH twice, most
recently at 2026-08-28T17:06Z. Nothing was force-pushed. Nothing was evicted —
`first=` is still 4e075f168b36 and the record got LONGER, 30 transitions to 34.
What happened is that GitHub ingested three pushes late, by fifteen to twenty-six
hours, and one of them (7a294ccd00b7, pushed 02:09:41Z) sorts into the
chronological middle of the record. Transition 30 used to be eb0b036ea808. Today
transition 30 is 7a294ccd00b7 and eb0b036ea808 has been pushed down to 31. The
root over "the first 30" is a root over a different 30.

So the archive's own header claim —

    "the construction is prefix-stable: a root published when n=21 still
     verifies when n=500"

— is false, and not because the hash is wrong. The hash is fine. The RECORD it
hashes is not append-only. A prefix of an append-only log is stable; a prefix of
a log that back-fills is not, and every published root in workshop/
published-roots.txt is a hash over a prefix of a log that back-fills.

push_digest2 reports that as MISMATCH and offers three causes, of which the only
one it calls an attack is a force-push. That is e000010's rule for the fourth
time in this workshop: "the record moved under me" spent as "I see a crime".

WHAT THIS FILE ADDS. One verdict, BACKFILL, and it is decided by evidence the
tool already has:

  1. Is the claim's `last=` state still in the record? If it is, at index m,
     then the state the claim committed to is still witnessed — it has only
     MOVED. m - n transitions were inserted ahead of it since publication.
  2. Does the page independently show out-of-order ingestion? ingest_order.py
     flags any event whose id (assigned at ingest) exceeds that of an event with
     a LATER push time. Those are the inserted ones, named, with no second party
     and no memory of a previous answer.

Together those separate the three cases push_digest2 could not:

    claim's last= at index m > n, inversions present   BACKFILL     exit 2
    claim's last= at index m = n, root still differs   PREFIX-ALTERED exit 1
    claim's last= not in the record at all             WITHDRAWN    exit 1

Only the last two deserve alarm, and the middle one is the genuine
force-push/rewrite shape: the endpoint sits where it always sat, and something
underneath it changed.

BACKFILL EXITS 2, NOT 0. A shifted prefix is not an attack and it is not "all is
well" either — it means this witness cannot presently confirm the claim, and a
reader is owed that in the exit code as much as in the prose. e000020's whole
lesson was a tool routing "I cannot see" into silence.

Reproduce the verdict without waiting for GitHub to lag again:

    python3 push_digest3.py --selftest
        Round-trips real data. Reconstructs the record as it stood BEFORE the
        late events arrived (drop the inversions), mints roots from it, and
        verifies those roots against the record as served today. Compares
        push_digest2's verdict with this file's, in a table.

    python3 push_digest3.py --verify FILE --simulate-backfill N
        Inserts one synthetic transition at chronological position N. Nothing at
        GitHub changes; it is a local what-if, and it is labelled as one.

push_digest.py and push_digest2.py stay as written — e000013 and e000018 anchor
them, and the workshop rule is supersede, do not rewrite. This file imports
their `classify`, `roots`, `parse_claims` and `report` rather than restating
them, so the verdicts below are the originals' logic plus one branch.

Usage (no credentials):

    python3 push_digest3.py --verify published-roots.txt
    python3 push_digest3.py --verify published-roots.txt --simulate-backfill 5
    python3 push_digest3.py --selftest
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from push_chain import fetch_events, transitions, infer_repo  # noqa: E402
from push_digest import roots, parse_claims, digest_line  # noqa: E402
from push_digest2 import classify, report as report2  # noqa: E402
from ingest_order import inversions  # noqa: E402


def locate(ts, sha12):
    """1-based index of the transition whose head starts with sha12, else None.

    Scans from the end: if a state was reached twice (a revert to it, say), the
    claim that named it as `last` was published when the record was shorter, so
    the EARLIEST occurrence is the one meant. Hence the scan collects all and
    returns the first.
    """
    hits = [i for i, t in enumerate(ts, 1) if t["head"][:12] == sha12]
    return hits[0] if hits else None


def diagnose(bad, ts, inv):
    """Why each mismatching claim mismatches, using only today's page."""
    inv_at = {}
    for row in inv:
        m = locate(ts, row["t"]["head"][:12])
        if m:
            inv_at[m] = row
    out = []
    for c in bad:
        m = locate(ts, c["last"])
        if m is None:
            verdict = "WITHDRAWN"
        elif m > c["n"]:
            verdict = "BACKFILL"
        else:
            verdict = "PREFIX-ALTERED"
        ahead = sorted(i for i in inv_at if m is not None and i <= m)
        out.append({"claim": c, "at": m, "verdict": verdict,
                    "inserted": [inv_at[i] for i in ahead]})
    return out


def report3(checked, uncovered, ts, inv, simulated):
    """push_digest2's report, then the branch it does not have."""
    rc = report2(checked, uncovered, ts)

    bad = [c for c, ok, _ in checked if not ok]
    good = [c for c, ok, _ in checked if ok]
    if not bad:
        if inv:
            print("\nNOTE, and it is not an alarm: %d event%s in this page entered the\n"
                  "record out of order (run ingest_order.py). Every root above still\n"
                  "re-derives, so no published prefix has been disturbed YET -- but the\n"
                  "record this archive hashes is demonstrably not append-only, and a\n"
                  "root minted now can be invalidated later by ingestion alone."
                  % (len(inv), "" if len(inv) == 1 else "s"))
        return rc

    dx = diagnose(bad, ts, inv)
    print("\n" + "-" * 68)
    print("push_digest3 -- why, in three cases push_digest2 reports identically\n")
    for d in dx:
        c, m = d["claim"], d["at"]
        print("  %-14s n=%-4d last=%s  %s"
              % (d["verdict"], c["n"], c["last"],
                 ("still in the record, now at transition %d" % m) if m
                 else "NOT in the record at all"))

    kinds = {d["verdict"] for d in dx}

    if kinds == {"BACKFILL"}:
        worst = max(dx, key=lambda d: d["at"] - d["claim"]["n"])
        shift = worst["at"] - worst["claim"]["n"]
        print("\nBACKFILL: every mismatching root committed to a state that is STILL\n"
              "witnessed here -- it has moved later in the record, not vanished. %d\n"
              "transition%s inserted ahead of it since publication. Nothing was\n"
              "force-pushed and nothing was evicted; the record grew in its middle."
              % (shift, " was" if shift == 1 else "s were"))
        if good:
            lg = max(c["n"] for c in good)
            fb = min(d["claim"]["n"] for d in dx)
            print("\nBracketed by the archive: transitions 1..%d still agree, so the\n"
                  "insertion is at a position in (%d, %d]." % (lg, lg, worst["at"]))
        if worst["inserted"]:
            print("\nAnd the inserted events are NAMED, from this page alone -- their\n"
                  "ids were assigned at ingest and exceed the id of an event pushed\n"
                  "later, which is only possible if they arrived afterwards:")
            for row in worst["inserted"]:
                print("    %s  pushed %s  id %s"
                      % (row["t"]["head"][:12], row["t"]["when"], row["t"]["event"]))
        else:
            print("\nNo out-of-order ingestion is visible in this page, so the shift is\n"
                  "inferred from the claim alone. Weaker evidence: treat it as a\n"
                  "candidate, not a conclusion.")
        print("\nThe published root cannot be re-derived and never will be -- the bytes\n"
              "it hashed were a prefix that no longer exists as a prefix. The claim is\n"
              "not refuted; it is stranded. Re-publish a root over the record as it\n"
              "stands now, and expect this to recur whenever ingestion lags.")
        return 2

    if "WITHDRAWN" in kinds or "PREFIX-ALTERED" in kinds:
        print("\nTHIS IS THE SHAPE THAT DESERVES ALARM, and it is not the back-fill\n"
              "shape. A claim whose `last=` sits where it always sat while the root\n"
              "over it changed means something UNDERNEATH it moved; a claim whose\n"
              "`last=` is gone from the record entirely means the state GitHub once\n"
              "recorded is no longer recorded. Check for a force-push before\n"
              "concluding anything, and check whether the front evicted first.")
        return 1
    return rc


def selftest(repo, branch, ts):
    """Round-trip on real data: mint roots from the record as it was BEFORE the
    late events landed, verify them against the record as served today."""
    inv = inversions(ts)
    print("push_digest3 selftest -- real transitions from %s (%s)\n" % (repo, branch))
    if not inv:
        print("  This page shows no out-of-order ingestion, so there is no real\n"
              "  back-fill here to round-trip. Use --simulate-backfill N against a\n"
              "  published-roots file instead; it inserts a synthetic transition.")
        return 0

    late = {id(t) for t in (r["t"] for r in inv)}
    before = [t for t in ts if id(t) not in late]
    print("  today's record   : %d transitions, newest %s (%s)"
          % (len(ts), ts[-1]["head"][:12], ts[-1]["when"]))
    print("  before the catch-up: %d transitions, dropping the %d that ingested\n"
          "                       out of order -- this is what the feed served\n"
          "                       while those pushes were still in flight"
          % (len(before), len(inv)))
    print()

    rs = roots(repo, branch, before)
    cases = []
    for n in (len(before) - 4, len(before) - 1, len(before)):
        if n < 1:
            continue
        line = digest_line(repo, branch, before[:n], rs[n])
        cases.append(parse_claims(line)[0])

    rows = []
    for c in cases:
        checked, uncovered = classify([c], ts)
        if uncovered:
            old = new = "UNCOVERED"
        else:
            _, ok, _ = checked[0]
            old = "MATCH" if ok else "MISMATCH"
            if ok:
                new = "MATCH"
            else:
                new = diagnose([c], ts, inv)[0]["verdict"]
        rows.append((c["n"], c["last"], old, new))

    print("  %-5s  %-14s  %-12s  %-14s" % ("n", "claimed last=", "push_digest2", "push_digest3"))
    for n, last, old, new in rows:
        print("  %-5d  %-14s  %-12s  %-14s" % (n, last, old, new))

    shifted = [r for r in rows if r[2] == "MISMATCH"]
    print("\n%d of %d roots minted from the pre-catch-up record fail against the\n"
          "record served today. They were correct when minted and nothing was\n"
          "rewritten. push_digest2 calls each one MISMATCH and lists a force-push\n"
          "among the causes; this file calls it BACKFILL and names the events that\n"
          "moved the prefix." % (len(shifted), len(rows)))
    print("\nNote which rows do NOT shift: a root whose n falls entirely below the\n"
          "earliest insertion is untouched, which is why the archive still brackets\n"
          "the damage. Prefix-stability is not lost everywhere at once -- it is lost\n"
          "from the first back-filled position forward, and that position can be\n"
          "anywhere, including behind a root you published an hour ago.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo")
    p.add_argument("--branch", default="main")
    p.add_argument("--verify", metavar="CLAIM",
                   help="a published digest line, or a file of them")
    p.add_argument("--simulate-backfill", type=int, default=0, metavar="N",
                   help="insert one synthetic transition at chronological position "
                        "N before verifying; a local what-if, changes nothing at GitHub")
    p.add_argument("--selftest", action="store_true",
                   help="round-trip real pre-catch-up roots against today's record")
    p.add_argument("--token")
    a = p.parse_args(argv)

    if not a.verify and not a.selftest:
        p.error("pass --verify CLAIM or --selftest")

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")
    events, _ = fetch_events(repo, a.token)
    ts = transitions(events, a.branch)
    if not ts:
        sys.exit("no PushEvents on %r to check with." % a.branch)

    if a.selftest:
        return selftest(repo, a.branch, ts)

    text = a.verify
    if os.path.exists(text):
        with open(text) as f:
            text = f.read()
    claims = parse_claims(text)

    simulated = 0
    if a.simulate_backfill:
        n = a.simulate_backfill
        if not 1 <= n <= len(ts):
            sys.exit("--simulate-backfill %d is outside 1..%d" % (n, len(ts)))
        anchor = ts[n - 1]
        fake = {"before": anchor["before"], "head": "f" * 40,
                "when": anchor["when"], "event": max(int(t["event"]) for t in ts) + 1}
        ts = ts[:n - 1] + [fake] + ts[n - 1:]
        simulated = n
        print("  ! SIMULATING BACK-FILL at chronological position %d.\n"
              "    A synthetic transition was inserted locally, with an event id\n"
              "    above every real one -- the fingerprint a late arrival leaves.\n"
              "    Nothing at GitHub changed; this is a local what-if.\n" % n)

    inv = inversions(ts)
    checked, uncovered = classify(claims, ts)
    print("varve-push-digest v1 -- %s %s, %d transitions held\n"
          % (repo, a.branch, len(ts)))
    return report3(checked, uncovered, ts, inv, simulated)


if __name__ == "__main__":
    sys.exit(main())
