#!/usr/bin/env python3
"""ingest_tail.py -- bound the PERMANENT-LOSS RATE of a back-filling feed.

Companion to ingest_survival.py, and a partial disagreement with it. Run that
one first; it answers a different question and it answers it correctly.

    python3 ingest_tail.py            # bound the loss rate from the poll log
    python3 ingest_tail.py --selftest # offline, no network

WHY THIS FILE EXISTS.

ingest_survival.py's module docstring states, as its point (2):

    THE LOSS RATE IS NOT IDENTIFIABLE FROM THE FEED. ... If some pushes are
    dropped permanently, that mass sits at t=infinity and is indistinguishable,
    at any finite observation, from mass that has simply not arrived. No amount
    of polling settles it.

The first sentence of that is a claim about a POPULATION RATE. The reasoning
offered for it is a claim about ONE SUBJECT. They are not the same claim, and
only the second one is true.

For a single push still absent, yes: "lost or merely slow?" has no answer at any
finite observation time, and it never will. That is real and it is the honest
core of e000028.

But the loss RATE is a property of many pushes, and most of this feed's pushes
are not in that ambiguous state at all. A push observed PRESENT is a settled
case: it arrived, so it was not permanently dropped. Thirty-seven of this
repository's thirty-eight witnessed pushes are settled that way. Zero permanent
losses have ever been observed. That is not nothing -- it is a one-sided
binomial observation, and it bounds the loss rate above by ordinary means.

"No amount of polling settles it" is therefore false as stated: each additional
settled push tightens the bound, without limit. What no amount of polling buys
is a POINT ESTIMATE, or any lower bound above zero -- you can never prove the
feed loses something. Bounded above and not point-identified is a much weaker
statement than "not identifiable", and it is the true one.

THE THIRTY DISCARDED PUSHES ARE THE EVIDENCE.

ingest_survival.py prints the thirty pre-poll-log pushes under a heading that
says they "contribute exactly nothing to the lag distribution" and calls them
THE COST OF NOT HAVING KEPT POLLS. About the lag DISTRIBUTION that is right:
their only bound is "arrived within 184h", which measures the gap in the poll
log, and feeding them to Kaplan-Meier as arrivals at 184h is the exact bug that
tool was built to stop making.

About the TAIL it is backwards. Those thirty are thirty confirmed arrivals --
thirty confirmed non-losses -- and they are most of the sample that bounds the
very quantity e000028 declared unmeasurable. The tool discards, with a lecture
attached, the data that answers its own headline question. The two questions
have opposite data requirements:

    the lag distribution's SHAPE needs a poll that saw the push ABSENT.
    the loss RATE's upper bound needs only that the push was eventually SEEN.

So the same subject can be worthless for one and load-bearing for the other,
and a tool that sorts subjects once, for one question, will mis-sort them for
the other. That is the transferable lesson here, more than the number.

THIS IS FORTY YEARS OLD AND HAS THREE NAMES.

Also worth writing down, because this notebook derived it from scratch and did
not need to. The shape -- events that occur at one time and become visible at a
later, random time; a count that rises on its own while nothing upstream
changes -- is standard, and it is standard in at least three literatures that
mostly do not cite each other:

  * actuarial reserving: IBNR, "incurred but not reported", and the chain-ladder
    method for estimating it (Mack 1993, W2134548066, 568 citations).
  * epidemiology: reporting-delay adjustment and nowcasting of surveillance
    counts (Lawless 1994, W2102383033, "occurred but not reported" / OBNR --
    an abstract that names disease cases and insurance claims in one breath).
  * biostatistics: right-truncated and left-censored delay distributions
    (Kalbfleisch & Lawless; Sun 1999, W2002174290).

Sun 1999 is worth naming twice. It estimates a delay distribution from data in
which reporting delays "were recorded only from November 1990 rather than from
the beginning of the epidemic" -- structurally identical to a poll log that
begins on 2026-08-28 with 184 hours of pushes behind it -- and its result is
that INCLUDING the left-censored observations improves the precision of the
estimate over analysing the uncensored ones alone. Precisely the observations
ingest_survival.py names and drops.

This file does not implement Sun's estimator. It does the elementary thing the
data already supports, and points at the estimator for whoever wants the rest.

WHAT THIS FILE DOES NOT CLAIM.

  * No lower bound on the loss rate. Zero losses observed is consistent with a
    feed that loses nothing, and no observation can ever rule that in or out
    from above. The bound here is one-sided by construction.
  * No claim that any particular absent push will arrive. That is the
    per-subject question and it stays unanswerable; see e000029.
  * The bound is a bound on THIS feed under THIS repository's push pattern,
    from a sample of 38 that one party assembled. It is not a GitHub SLA.
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ingest_survival as ISV  # noqa: E402


# ------------------------------------------------------------------ statistics

def binom_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p). Exact, no dependencies."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
    return min(1.0, total)


def clopper_pearson_upper(k, n, alpha=0.05):
    """Exact one-sided upper confidence limit on p, having seen k events in n.

    The limit U solves P(X <= k | n, U) = alpha. Found by bisection because the
    CDF is monotone decreasing in p, which is all bisection needs. For k=0 this
    reduces to 1 - alpha**(1/n) -- the 'rule of three' when alpha=0.05 and n is
    large -- and --selftest checks that it does.
    """
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- the analysis

def fates(subjects, polls, now):
    """Split witnessed pushes into settled arrivals and unresolved absences.

    Deliberately NOT the cohort split. in_cohort() asks 'does this push tell me
    anything about how LONG ingestion takes', and answers no for 30 of 38. This
    asks 'is this push's fate SETTLED', and answers yes for 37 of 38. Same
    subjects, different question, near-opposite partition. That divergence is
    the whole point of this file.
    """
    arrived, censored = [], []
    for s in subjects:
        kind, lo, hi = ISV.bracket(s, polls, now)
        if kind == "censored":
            censored.append((s, lo))
        else:
            arrived.append((s, hi))
    return arrived, censored


def report(subjects, polls, now, alpha=0.05):
    arrived, censored = fates(subjects, polls, now)
    n_arr, n_cen = len(arrived), len(censored)

    print("permanent-loss rate of the events feed -- an upper bound, one-sided")
    print(f"  observed at   : {ISV.fmt_ts(now)}")
    print(f"  poll log      : {len(polls)} poll(s)")
    print(f"  witnessed     : {n_arr + n_cen} push(es)")
    print()
    print(f"  SETTLED   ({n_arr}): observed present at some poll. Arrived, therefore")
    print("                 not permanently dropped. These are the sample.")
    print(f"  UNRESOLVED ({n_cen}): never yet observed present. Fate genuinely unknown;")
    print("                 lost and merely-slow are indistinguishable here, forever.")
    print()

    if n_arr == 0:
        print("  Nothing settled yet. No bound available.")
        return 1

    conf = int(round((1 - alpha) * 100))

    # Optimistic reading: the unresolved push is not counted against the feed.
    u_opt = clopper_pearson_upper(0, n_arr, alpha)
    # Adversarial reading: assume every unresolved push is permanently lost.
    u_adv = clopper_pearson_upper(n_cen, n_arr + n_cen, alpha)

    print(f"  losses observed: 0 in {n_arr} settled pushes")
    print(f"  {conf}% upper bound on per-push permanent-loss probability")
    print(f"    treating unresolved pushes as not-yet-arrived : {u_opt * 100:6.2f}%")
    print(f"    treating ALL {n_cen} unresolved as lost (worst case): {u_adv * 100:6.2f}%")
    print()
    print("  The truth is inside that interval and cannot be narrowed by waiting")
    print("  on the unresolved push alone -- but it narrows with every push that")
    print("  settles, which is the half e000028 said was impossible.")
    print()

    if arrived:
        worst = max(hi for _, hi in arrived)
        print(f"  slowest CONFIRMED arrival, over all {n_arr} settled pushes: <= {worst:.0f}h")
        print("  (an upper bound per push, not a measurement: for pushes that predate")
        print("   the poll log it is the push-to-first-poll gap. Loose, and still a")
        print("   real ceiling -- nothing in this feed's history is known to have")
        print("   taken longer, and 'loose upper bound' is not 'no information'.)")
    print()
    print("  " + "-" * 66)
    print("  WHAT THIS DOES NOT SAY.")
    print()
    print("  There is no lower bound here and there cannot be one. Zero observed")
    print("  losses is consistent with a feed that never loses anything, and no")
    print("  finite observation distinguishes 'loses nothing' from 'loses rarely'.")
    print("  A one-sided bound is the whole of what this shape of data supports.")
    print()
    print("  Nor does this rescue the per-subject question. Whether one named")
    print("  absent push is lost or slow is still unanswerable at every finite")
    print("  observation time, exactly as e000028 says. What this file corrects")
    print("  is only the slide from that true per-subject claim to the false")
    print("  population claim that the RATE cannot be bounded at all.")
    return 0


# ------------------------------------------------------------------- self-test

def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond:
            ok = False

    print("clopper_pearson_upper")
    # k=0 has the closed form 1 - alpha**(1/n); bisection must reproduce it.
    for n in (1, 5, 37, 200):
        closed = 1 - 0.05 ** (1.0 / n)
        got = clopper_pearson_upper(0, n, 0.05)
        check(f"k=0 n={n}: {got:.6f} == closed form {closed:.6f}",
              abs(got - closed) < 1e-6)
    # The rule of three is an APPROXIMATION to that closed form, good for large
    # n. Assert it as an approximation, not as the definition.
    got37 = clopper_pearson_upper(0, 37, 0.05)
    check(f"rule of three 3/37={3/37:.4f} approximates exact {got37:.4f} to 1e-2",
          abs(got37 - 3 / 37) < 1e-2)
    check("more evidence tightens: U(0,100) < U(0,37)",
          clopper_pearson_upper(0, 100) < clopper_pearson_upper(0, 37))
    check("an observed failure loosens: U(1,38) > U(0,38)",
          clopper_pearson_upper(1, 38) > clopper_pearson_upper(0, 38))
    check("U(0,n) is a probability", 0 < clopper_pearson_upper(0, 37) < 1)
    check("k=n gives no information: U == 1", clopper_pearson_upper(5, 5) == 1.0)
    check("n=0 gives no information: U == 1", clopper_pearson_upper(0, 0) == 1.0)

    print("binom_cdf")
    check("P(X<=n | n,p) == 1", abs(binom_cdf(3, 3, 0.4) - 1.0) < 1e-12)
    check("P(X<=0 | n,p) == (1-p)^n",
          abs(binom_cdf(0, 4, 0.25) - 0.75 ** 4) < 1e-12)
    check("monotone decreasing in p", binom_cdf(1, 10, 0.1) > binom_cdf(1, 10, 0.5))

    print("fates")
    # A synthetic feed: one push seen present, one never seen. No network.
    polls = [
        {"polled_at": "2026-01-01T00:00:00Z", "exhaustive": True,
         "present": [], "subjects": {
             "aaa": {"pushed_at": "2026-01-01T00:00:00Z"},
             "bbb": {"pushed_at": "2026-01-01T00:00:00Z"}}},
        {"polled_at": "2026-01-02T00:00:00Z", "exhaustive": True,
         "present": ["aaa"], "subjects": {}},
    ]
    subjects = ISV.subjects_from(polls)
    now = ISV.parse_ts("2026-01-03T00:00:00Z")
    arrived, censored = fates(subjects, polls, now)
    check("one settled, one unresolved", len(arrived) == 1 and len(censored) == 1)
    check("the settled one is aaa", arrived[0][0]["head"] == "aaa")
    check("the unresolved one is bbb", censored[0][0]["head"] == "bbb")
    # And the divergence this file is about: bbb is IN the survival cohort
    # (it was seen absent) while contributing nothing to the loss bound, and a
    # pre-log arrival would be the reverse. Assert the first half directly.
    check("unresolved push is in the survival cohort but not the loss sample",
          ISV.in_cohort(censored[0][0], polls, "censored") is True)

    print()
    print("SELFTEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="run offline checks and exit")
    ap.add_argument("--alpha", type=float, default=0.05,
                    help="one-sided error rate for the bound (default 0.05)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    polls = ISV.read_poll_log()
    if not polls:
        print("no poll log; run: python3 ingest_survival.py --poll")
        return 1
    subjects = ISV.subjects_from(polls)
    now = ISV.parse_ts(polls[-1]["polled_at"])
    return report(subjects, polls, now, alpha=args.alpha)


if __name__ == "__main__":
    raise SystemExit(main())
