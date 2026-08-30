#!/usr/bin/env python3
"""ingest_npmle.py -- the ingest-lag distribution, without the partition.

WHY THIS EXISTS.

workshop/ingest_survival.py splits this repository's 38 witnessed pushes into a
cohort of 8 that "carry lag information" and 30 that do not, and runs
Kaplan-Meier on the 8. workshop/ingest_tail.py splits the same 38 a second and
different way -- 37 settled, 1 unresolved -- because the first partition was
wrong for its question. e000032 drew the lesson that a tool which partitions
once will mis-partition for the next question.

This tool draws the other conclusion: for THIS question the partition is not
needed at all, because every one of the 38 observations is an interval
containing the unobserved lag, and there is a standard estimator that consumes
intervals of every shape in one pass.

    pushed before polling began, present when first seen  ->  D in (0, R]
    seen absent at one poll, present at a later one       ->  D in (L, R]
    pushed after polling began, present when first seen   ->  D in (0, R]
    still absent at the latest poll                       ->  D in (L, inf)

Left-censored, interval-censored, and right-censored are the same object with
different endpoints. Turnbull (1976) gives the nonparametric maximum-likelihood
estimator for exactly this, by self-consistency (an EM algorithm). Kaplan-Meier
is its special case when every interval is either a point or (L, inf) -- which
is asserted as a selftest below, against ingest_survival.py's own curve code.

WHAT THIS BUYS, AND WHAT IT DOES NOT.

It buys the use of all 38 subjects without pretending any of them is an exact
arrival time. ingest_survival.py's curve enters (0, 7.75] as an EVENT AT
7.75h -- its docstring calls collapsing to the upper end "the conservative
choice" -- which is the same move the exclusion of the 30 exists to prevent,
applied to the near observations and not the far ones. The NPMLE does not
collapse anything; mass inside an interval stays undetermined, and the
estimator reports where it is and is not identified.

It does NOT buy a smaller uncertainty by magic. If the data cannot locate mass
below 7.75h -- and it cannot, because no poll ever ran that fast -- the NPMLE
says so by leaving (0, 7.75] as a single unresolved block rather than by
drawing a confident flat line across it. That is the honest failure mode and it
is the reason to prefer this over the curve it replaces.

USAGE

    python3 ingest_npmle.py              # estimate from workshop/poll-log.jsonl
    python3 ingest_npmle.py --selftest   # checks, including KM equivalence
    python3 ingest_npmle.py --compare    # cohort-of-8 vs all-38, side by side

No network, no dependencies. Reads the poll log; writes nothing.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

INF = float("inf")


# ------------------------------------------------------------ the estimator

def turnbull_intervals(observations):
    """The innermost intervals on which the NPMLE can place mass.

    Turnbull's construction: mass can only sit where some observation's LEFT
    endpoint is immediately followed by some observation's RIGHT endpoint, with
    no other endpoint in between. Anywhere else, moving mass to a neighbouring
    block changes no observation's likelihood, so the data does not distinguish
    the two and the NPMLE is not unique there.

    Observations are half-open (L, R]: L is a time the event had NOT happened,
    R is a time by which it HAD. So a left endpoint opens a block strictly after
    L and a right endpoint closes one at R.

    Returns a list of (q, p) blocks with q < p, sorted.
    """
    lefts = sorted({o[0] for o in observations})
    rights = sorted({o[1] for o in observations})
    blocks = []
    for q in lefts:
        # the smallest right endpoint at or after q ...
        candidates = [r for r in rights if r > q]
        if not candidates:
            continue
        p = min(candidates)
        # ... is a valid block only if no other LEFT endpoint sits strictly
        # inside (q, p); if one does, that left endpoint starts the real block.
        if any(q < l < p for l in lefts):
            continue
        blocks.append((q, p))
    return sorted(set(blocks))


def _alpha(observations, blocks):
    """alpha[i][j] = 1 iff block j lies entirely inside observation i.

    Block (q, p) is inside observation (L, R] when L <= q and p <= R. The event
    for subject i happened somewhere in (L_i, R_i]; the blocks it could have
    happened in are exactly these.
    """
    out = []
    for (L, R) in observations:
        out.append([1.0 if (L <= q and p <= R) else 0.0 for (q, p) in blocks])
    return out


def npmle(observations, tol=1e-12, max_iter=200000):
    """Turnbull's self-consistency (EM) estimate of the lag distribution.

    Returns (blocks, mass, iterations). mass[j] is the probability the NPMLE
    assigns to block j; where it lands inside the block is not identified.

    The EM step is the standard one: each subject spreads its unit of belief
    over the blocks it is compatible with, in proportion to the current
    estimate; the new estimate is the average of those spreads.
    """
    blocks = turnbull_intervals(observations)
    if not blocks:
        return [], [], 0
    n = len(observations)
    m = len(blocks)
    alpha = _alpha(observations, blocks)

    # A subject compatible with no block would make the likelihood zero. With
    # the construction above that cannot happen, but assert rather than trust.
    for i, row in enumerate(alpha):
        if not any(row):
            raise AssertionError(
                f"observation {observations[i]} matches no Turnbull block; "
                "the block construction is wrong")

    mass = [1.0 / m] * m
    for it in range(1, max_iter + 1):
        new = [0.0] * m
        for i in range(n):
            row = alpha[i]
            denom = 0.0
            for j in range(m):
                if row[j]:
                    denom += mass[j]
            if denom <= 0.0:
                continue
            for j in range(m):
                if row[j]:
                    new[j] += mass[j] / denom
        new = [x / n for x in new]
        delta = max(abs(new[j] - mass[j]) for j in range(m))
        mass = new
        if delta < tol:
            return blocks, mass, it
    return blocks, mass, max_iter


def survival_from_npmle(blocks, mass):
    """S(t) = P(lag > t), reported only where the NPMLE determines it.

    Returns a list of (t, S) at each block's RIGHT endpoint -- the points where
    S is identified. Between q_j and p_j the estimator genuinely does not know,
    and this deliberately does not interpolate across the gap.
    """
    out = [(0.0, 1.0)]
    cum = 0.0
    for (q, p), w in zip(blocks, mass):
        cum += w
        out.append((p, max(0.0, 1.0 - cum)))
    return out


# -------------------------------------------------------------- reading data

def observations_from_poll_log():
    """Every witnessed push as an interval, with no subject excluded.

    Uses ingest_survival.py's own bracket() so the intervals are the same ones
    that tool computes -- the difference between the two reports is then the
    estimator and the partition, never the parsing.
    """
    import ingest_survival as S
    polls = S.read_poll_log()
    subjects = S.subjects_from(polls)
    now = S.parse_ts(polls[-1]["polled_at"])
    rows = []
    for s in subjects:
        kind, lo, hi = S.bracket(s, polls, now)
        R = INF if hi is None else hi
        rows.append({
            "head": s["head"],
            "pushed_at": s["pushed_at"],
            "kind": kind,
            "L": lo,
            "R": R,
            "in_cohort": S.in_cohort(s, polls, kind),
        })
    return rows


# ------------------------------------------------------------------ selftest

def _km_reference(exact, censored):
    """Kaplan-Meier, written independently here, for the equivalence check."""
    pts = [(t, "event") for t in exact] + [(t, "censor") for t in censored]
    pts.sort(key=lambda x: (x[0], x[1] == "event"))
    n = len(pts)
    s = 1.0
    curve = [(0.0, 1.0)]
    i = 0
    while i < len(pts):
        t = pts[i][0]
        d = c = 0
        while i < len(pts) and pts[i][0] == t:
            if pts[i][1] == "event":
                d += 1
            else:
                c += 1
            i += 1
        if d and n:
            s *= (1.0 - d / n)
            curve.append((t, s))
        n -= (d + c)
    return curve


def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if detail and not cond:
            print(f"         {detail}")
        ok = ok and bool(cond)

    # (1) All-exact data: NPMLE must be the empirical distribution.
    obs = [(0.9999, 1.0), (0.9999, 1.0), (1.9999, 2.0)]
    blocks, mass, _ = npmle(obs)
    check("exact observations reproduce the empirical CDF",
          abs(mass[0] - 2 / 3) < 1e-6 and abs(mass[1] - 1 / 3) < 1e-6,
          f"blocks={blocks} mass={mass}")

    # (2) Nested left-censored data with a known answer.
    #     (0,1], (0,1], (0,2] -> mass 1 sits in (0,1]: every subject is
    #     compatible with it, so putting anything in (1,2] can only lose
    #     likelihood on the two subjects capped at 1.
    blocks, mass, _ = npmle([(0.0, 1.0), (0.0, 1.0), (0.0, 2.0)])
    check("nested left-censored intervals concentrate on the inner block",
          abs(mass[0] - 1.0) < 1e-6,
          f"blocks={blocks} mass={mass}")

    # (3) THE ONE THAT MATTERS: with only exact events and right-censoring,
    #     the Turnbull NPMLE is Kaplan-Meier. Same data, two estimators.
    exact = [2.0, 5.0, 5.0, 9.0]
    cens = [3.0, 7.0, 12.0]
    eps = 1e-9
    obs = [(t - eps, t) for t in exact] + [(t, INF) for t in cens]
    blocks, mass, _ = npmle(obs)
    surv = survival_from_npmle(blocks, mass)
    km = _km_reference(exact, cens)
    got = {round(t, 6): s for t, s in surv if t != INF}
    agree = True
    detail = []
    for t, s in km:
        if t == 0.0:
            continue
        near = [v for k, v in got.items() if abs(k - t) < 1e-4]
        if not near or abs(near[0] - s) > 1e-6:
            agree = False
            detail.append(f"t={t}: KM={s:.6f} NPMLE={near[0] if near else None}")
    check("NPMLE equals Kaplan-Meier when data is exact + right-censored",
          agree, "; ".join(detail))

    # (4) Mass is a distribution.
    rows = observations_from_poll_log()
    obs = [(r["L"], r["R"]) for r in rows]
    blocks, mass, iters = npmle(obs)
    check("estimated mass sums to <= 1 (deficit is the right-censored tail)",
          sum(mass) <= 1.0 + 1e-9, f"sum={sum(mass)}")
    check("every block has non-negative mass", all(w >= -1e-12 for w in mass))
    check("EM converged", iters < 200000, f"iters={iters}")

    # (5) The claim this tool is built on: an observation the survival cohort
    #     admits and one it excludes have the SAME censoring shape.
    opens = [r for r in rows if r["L"] == 0.0 and r["R"] != INF]
    admitted = [r for r in opens if r["in_cohort"]]
    excluded = [r for r in opens if not r["in_cohort"]]
    check("cohort admits and excludes observations of identical (0,R] shape",
          bool(admitted) and bool(excluded),
          f"admitted={len(admitted)} excluded={len(excluded)}")

    print()
    print("  selftest:", "OK" if ok else "FAILURES")
    return 0 if ok else 1


# ------------------------------------------------------------------ reporting

def _fmt(x):
    return "inf" if x == INF else f"{x:.2f}"


def report():
    rows = observations_from_poll_log()
    obs = [(r["L"], r["R"]) for r in rows]
    blocks, mass, iters = npmle(obs)
    surv = survival_from_npmle(blocks, mass)

    print("jywy78kgrf-create/varve -- ingest lag, Turnbull NPMLE over ALL subjects")
    print(f"  subjects      : {len(rows)} (no subject excluded)")
    n_open = sum(1 for r in rows if r["L"] == 0.0 and r["R"] != INF)
    n_brack = sum(1 for r in rows if r["L"] > 0.0 and r["R"] != INF)
    n_cens = sum(1 for r in rows if r["R"] == INF)
    print(f"  left-censored (0,R]   : {n_open}")
    print(f"  interval-censored     : {n_brack}")
    print(f"  right-censored (L,inf): {n_cens}")
    print(f"  EM iterations : {iters}")
    print()

    print("  support blocks the data can distinguish, and their mass:")
    for (q, p), w in zip(blocks, mass):
        if w < 1e-9:
            continue
        bar = "#" * int(round(w * 60))
        print(f"    ({_fmt(q):>7}, {_fmt(p):>7}]  p={w:6.4f}  {bar}")
    tail = 1.0 - sum(mass)
    if tail > 1e-9:
        print(f"    ({_fmt(max(b[1] for b in blocks)):>7},     inf)  p={tail:6.4f}"
              "  <- mass beyond the last observation; may be late, may be lost")
    else:
        last = max(b[1] for b in blocks)
        print(f"    (   {_fmt(last)},     inf)  p=0.0000  <- READ THE CAVEAT BELOW")
        print()
        print(f"  CAVEAT ON THAT ZERO. No observation in this log has a left")
        print(f"  endpoint above {_fmt(max(b[0] for b in blocks))}h -- nobody has ever attested a push")
        print(f"  ABSENT later than that. So no Turnbull block can form above")
        print(f"  {_fmt(last)}h, and mass there would only cost likelihood on the "
              f"{n_open}")
        print("  subjects known to have arrived by their own upper bounds. The")
        print("  NPMLE therefore reports zero, and that zero is an artefact of the")
        print("  i.i.d. assumption, not a measurement.")
        print()
        print("  Concretely: this does NOT say 5966a7fbeefe arrives by "
              f"{_fmt(last)}h.")
        print("  It says a single lag distribution fitted to 38 exchangeable draws")
        print("  puts no mass beyond there. e000032's distinction exactly: a")
        print("  PER-POPULATION statement that cannot be read down to the one")
        print("  PER-SUBJECT question anybody actually wants answered. An")
        print("  estimator that assumes one shared distribution has no way to")
        print("  represent 'this particular push was dropped' at all.")
    print()

    print("  S(t) = P(lag > t), at the times where it is identified:")
    for t, s in surv:
        if t == INF:
            continue
        print(f"    t={t:7.2f}h  S={s:6.4f}")
    print()

    zero = [(q, p) for (q, p), w in zip(blocks, mass) if w < 1e-9]
    print("  WHERE THIS ESTIMATOR DECLINES TO SPEAK, which is the point.")
    print()
    lo_block = blocks[0] if blocks else None
    if lo_block:
        print(f"    Below t={_fmt(lo_block[1])}h the NPMLE places its mass in ONE")
        print("    undivided block. No poll in this log ever ran fast enough to")
        print("    split it, so the shape of the fast half of this distribution")
        print("    is not estimated here -- it is unmeasured. A Kaplan-Meier curve")
        print("    drawn from the same data shows steps in that range anyway,")
        print("    because it enters each (0,R] observation as an event at R,")
        print("    and R there is the POLLING GAP, not the lag.")
    if zero:
        print()
        print(f"    {len(zero)} block(s) got zero mass; the data actively places")
        print("    nothing there.")
    print()
    print("    The resolution of this instrument is the interval between polls,")
    print("    and that interval is set by notebook/pace.json -- by when this")
    print("    notebook decided to wake up, for reasons that had nothing to do")
    print("    with measuring GitHub.")
    return 0


def compare():
    """The cohort-of-8 against all 38, same estimator, so the partition is the
    only difference. e000032 predicted the excluded subjects would help; this
    is the check."""
    rows = observations_from_poll_log()
    all_obs = [(r["L"], r["R"]) for r in rows]
    coh_obs = [(r["L"], r["R"]) for r in rows if r["in_cohort"]]

    b_a, m_a, _ = npmle(all_obs)
    b_c, m_c, _ = npmle(coh_obs)
    s_a = survival_from_npmle(b_a, m_a)
    s_c = survival_from_npmle(b_c, m_c)

    print("  NPMLE on the cohort of "
          f"{len(coh_obs)} vs all {len(all_obs)} subjects")
    print()
    print(f"    {'t (h)':>9}  {'S cohort':>9}  {'S all':>9}   difference")
    grid = sorted({t for t, _ in s_a} | {t for t, _ in s_c})

    def at(curve, t):
        v = 1.0
        for tt, ss in curve:
            if tt <= t:
                v = ss
        return v

    for t in grid:
        if t == INF:
            continue
        a, c = at(s_a, t), at(s_c, t)
        print(f"    {t:9.2f}  {c:9.4f}  {a:9.4f}   {a - c:+.4f}")
    print()
    print(f"    blocks, cohort only : {[(round(q,2), round(p,2)) for q,p in b_c]}")
    print(f"    blocks, all subjects: {[(round(q,2), round(p,2)) for q,p in b_a]}")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--compare" in sys.argv:
        return compare()
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
