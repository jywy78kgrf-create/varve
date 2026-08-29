#!/usr/bin/env python3
"""ingest_survival — "is this record complete?" is a survival question, not a yes/no.

WHAT THIS IS FOR. Every completeness check in this workshop asks a binary:
push_chain3.py asks whether a transition is present and says HOLED when it is
not; push_digest3.py asks whether a published prefix re-derives. Both are
correct and both are answering the wrong SHAPE of question, because the source
they interrogate back-fills. Measured on this repository (e000024, e000025,
e000027): a push can enter the events feed fifteen to thirty-eight hours after
it happened, and sort into the record's chronological middle.

Against a back-filling source, "absent" is never a fact about the record. It is
a fact about the record AND the clock you read it at. The same push is a defect
at hour 20 and a success at hour 30, and nothing about the world changed in
between — only the observer's watch moved. push_chain3.py's own advice, "re-check
before concluding anything", is honest about this and has no stopping rule: it
does not say when re-checking ends and loss begins. Nothing in this workshop did.

THE FRAME THAT FITS. This is right-censored survival data, the same structure as
time-to-failure in reliability engineering or time-to-event in a clinical trial.
Each push is a subject. The event is "ingested". Some subjects have been
observed to have the event, with a bracketed time. Some have not had it YET, and
those are censored observations — not failures, not successes, and NOT
discardable. Two consequences follow immediately and both bite:

  (1) THE OBSERVED LAG DISTRIBUTION IS BIASED SHORT, ALWAYS. A lag is only
      measurable if the event arrived. Every push still in flight contributes
      nothing to your sample, and the pushes still in flight are exactly the
      slow ones. So a mean or median over arrived-only lags is not a cautious
      estimate of the true lag — it is guaranteed to understate it, by an
      amount you cannot bound from the arrived-only data. This tool therefore
      REFUSES to print a mean lag over arrived events alone, and says why.

  (2) THE LOSS RATE IS NOT IDENTIFIABLE FROM THE FEED. "Arrived so far" divided
      by "pushed so far" is not a completion rate; it is a completion rate
      censored at your poll time, and it rises on its own as you wait. If some
      pushes are dropped permanently, that mass sits at t=infinity and is
      indistinguishable, at any finite observation, from mass that has simply
      not arrived. No amount of polling settles it. This is e000025's boundary
      — "alone you can catch contradiction, never omission" — restated in a
      form that says exactly how much you lose: not the lag, only the tail.

WHAT MAKES THE MEASUREMENT POSSIBLE AT ALL, and it is the practical point.
A single client cannot see an omission from one page (ingest_order.py's limit:
an event still in flight is not on the page to be out of order). But a client
that WRITES DOWN ITS POLLS becomes its own second party across time. A poll
record — "at time T, this feed held exactly these transitions" — is worth
something no later page can reconstruct, because absence leaves no trace once
it heals. Two polls bracket an ingest time; one poll plus now censors it. That
is the whole mechanism, and it costs a JSONL line per session.

This notebook lost that data for eight days and had to reconstruct three polls
out of PROSE in e000023, e000024 and workshop/published-roots.txt in order to
seed this file. Those three are marked `reconstructed` in the poll log and are
not treated as exhaustive — they attest the presences and absences their entries
actually assert, and nothing else. Every poll from here on is `measured`.

    python3 ingest_survival.py              # analyse the poll log
    python3 ingest_survival.py --poll       # fetch the feed, append a poll, analyse
    python3 ingest_survival.py --selftest   # offline; shows the naive ratio flipping

THE SELFTEST IS THE ARGUMENT. It runs one synthetic feed at three observer
clocks. The naive "complete?" verdict RETRACTS itself — a push is reported
MISSING and later reported fine, with nothing about the feed having changed but
the watch. The censored treatment never retracts: a subject is censored at >=L,
and when it finally arrives its bracket NARROWS to (L', H] with H >= L. A bound
is never withdrawn, only tightened.

The survival estimate itself does move between clocks, and the first draft of
this file claimed it did not. That claim was wrong and the selftest caught it:
waiting longer without an arrival is more follow-up time, so the estimate gets
BETTER, which is information accruing rather than an answer flipping. The
property worth having is not that the number is constant — it is that no
statement made at an earlier clock is contradicted by a later one.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = "jywy78kgrf-create/varve"
BRANCH = "main"
HERE = os.path.dirname(os.path.abspath(__file__))
POLL_LOG = os.path.join(HERE, "poll-log.jsonl")


# ---------------------------------------------------------------- time helpers

def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_ts(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours(td):
    return td.total_seconds() / 3600.0


# ---------------------------------------------------------------- feed access

def fetch_events(repo, pages=3):
    """Every event the API will serve, newest id first. Unauthenticated."""
    out = []
    for page in range(1, pages + 1):
        url = f"https://api.github.com/repos/{repo}/events?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"User-Agent": "varve-ingest-survival"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                batch = json.load(r)
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} from the events API for {repo}.", file=sys.stderr)
            if e.code == 403:
                print("403 from api.github.com is a SCOPE answer, not an outage: this "
                      "sandbox reaches only repositories attached to the session.",
                      file=sys.stderr)
            raise SystemExit(1)
        if not batch:
            break
        out.extend(batch)
    return out


def transitions(events, branch=BRANCH):
    """(head12, before12, created_at, id) per push event on `branch`, ingest order."""
    rows = []
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        if e.get("payload", {}).get("ref") != f"refs/heads/{branch}":
            continue
        p = e["payload"]
        rows.append({
            "head": p["head"][:12],
            "before": p["before"][:12],
            "created_at": e["created_at"],
            "id": int(e["id"]),
        })
    rows.sort(key=lambda r: r["id"])
    return rows


# ---------------------------------------------------------------- the poll log

def read_poll_log(path=POLL_LOG):
    if not os.path.exists(path):
        return []
    polls = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            polls.append(json.loads(line))
    polls.sort(key=lambda p: p["polled_at"])
    return polls


def append_poll(poll, path=POLL_LOG):
    with open(path, "a") as fh:
        fh.write(json.dumps(poll, sort_keys=True) + "\n")


def poll_says(poll, head):
    """Did this poll attest head present / absent / nothing?

    An exhaustive poll (we held the whole page) answers for every head: anything
    not in `present` was absent. A reconstructed poll answers only for the heads
    its source entry actually named. Conflating the two would manufacture
    absences that nobody ever observed, which is the one way this file could
    fabricate data, so it is the one distinction it enforces.
    """
    if head in poll.get("present", []):
        return "present"
    if head in poll.get("absent", []):
        return "absent"
    if poll.get("exhaustive"):
        return "absent"
    return None


# ---------------------------------------------------------------- the analysis

def in_cohort(subject, polls, kind):
    """Does this push carry ANY usable information about ingest lag?

    Two ways in, and a push needs one of them:

      (a) it was pushed at or after the first poll in the log. Then the first
          poll that covers it is at most one polling interval later, so an
          'arrived by H' bound is a real measurement of lag.

      (b) some poll saw it ABSENT. Then a lower bound exists regardless of when
          polling started.

    Everything else is a push that happened before anyone was watching and was
    already present the first time anyone looked. Its only bound is "the lag was
    at most the time from the push to the first poll" — which for this
    repository's back-catalogue is up to 184 hours, a number that measures the
    gap in the poll log and not the behaviour of the feed. Those subjects are
    LEFT-CENSORED: standard Kaplan-Meier cannot consume them, and feeding them
    in as arrivals at 184h would draw a survival curve that is mostly an
    artefact of when this file was created. The first draft of this tool did
    exactly that. They are counted and named instead.

    The exclusion is not free and the bias runs both ways, so it is stated in
    the report rather than buried: route (b) selects for slow pushes (you only
    see an absence if the push was slow enough to be absent when someone
    looked), while route (a) is unbiased but only covers pushes made after
    polling began. As the log fills with route-(a) subjects the bias decays; at
    a cohort of seven it is severe.
    """
    if kind == "censored":
        return True
    if not polls:
        return False
    if subject["pushed_at"] >= polls[0]["polled_at"]:
        return True
    pushed = parse_ts(subject["pushed_at"])
    for p in polls:
        if parse_ts(p["polled_at"]) >= pushed and poll_says(p, subject["head"]) == "absent":
            return True
    return False


def bracket(subject, polls, now):
    """Right-censored ingest observation for one push.

    Returns (kind, lo_h, hi_h) where kind is 'measured' (lo,hi bracket the lag),
    'measured-open' (arrived, but no poll ever saw it absent, so lo is 0), or
    'censored' (not arrived as of the latest poll; lo is a LOWER BOUND and hi is
    None — the event may arrive at any later time, or never).
    """
    pushed = parse_ts(subject["pushed_at"])
    last_absent = None
    first_present = None
    for p in polls:
        t = parse_ts(p["polled_at"])
        if t < pushed:
            continue
        verdict = poll_says(p, subject["head"])
        if verdict == "absent":
            if first_present is None:
                last_absent = t
        elif verdict == "present":
            if first_present is None:
                first_present = t

    if first_present is None:
        return ("censored", hours(now - pushed), None)
    lo = hours(last_absent - pushed) if last_absent else 0.0
    kind = "measured" if last_absent else "measured-open"
    return (kind, lo, hours(first_present - pushed))


def subjects_from(polls, extra=()):
    """Every push any poll has ever mentioned, plus explicitly supplied ones.

    A push nobody ever recorded as present OR absent is invisible here, which is
    the honest state: this file measures what the poll log witnessed, and a
    successor who wants a push in the denominator has to have polled for it.
    """
    seen = {}
    for p in polls:
        for head, meta in p.get("subjects", {}).items():
            seen.setdefault(head, {"head": head, **meta})
    for s in extra:
        seen.setdefault(s["head"], dict(s))
    return sorted(seen.values(), key=lambda s: s["pushed_at"])


def survival_curve(observations):
    """Fraction of pushes NOT YET ingested, as a step function of hours since push.

    Kaplan-Meier with the brackets collapsed to their upper end (the conservative
    choice for a survival curve is the LATEST time the event could have happened,
    because assuming the earliest would understate the lag exactly the way
    dropping censored subjects does). Censored subjects leave the risk set at
    their censoring time without counting as events.
    """
    pts = []
    for kind, lo, hi in observations:
        if kind == "censored":
            pts.append((lo, "censor"))
        else:
            pts.append((hi, "event"))
    pts.sort(key=lambda x: (x[0], x[1] == "event"))

    n_at_risk = len(pts)
    s = 1.0
    curve = [(0.0, 1.0, n_at_risk)]
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
        if d and n_at_risk:
            s *= (1.0 - d / n_at_risk)
            curve.append((t, s, n_at_risk))
        n_at_risk -= (d + c)
    return curve


# ---------------------------------------------------------------- reporting

def report(repo, subjects, polls, now):
    rows = []
    for s in subjects:
        obs = bracket(s, polls, now)
        rows.append((s, obs, in_cohort(s, polls, obs[0])))
    cohort = [(s, o) for s, o, c in rows if c]
    excluded = [(s, o) for s, o, c in rows if not c]

    print(f"{repo} -- ingest lag as right-censored survival data")
    print(f"  poll log      : {len(polls)} poll(s), "
          f"{sum(1 for p in polls if p.get('source') == 'measured')} measured, "
          f"{sum(1 for p in polls if p.get('source') == 'reconstructed')} reconstructed")
    if polls:
        print(f"  window        : {polls[0]['polled_at']} .. {polls[-1]['polled_at']}")
    print(f"  observed now  : {fmt_ts(now)}")
    print(f"  subjects      : {len(subjects)} push(es) the poll log witnessed, "
          f"{len(cohort)} in cohort, {len(excluded)} carrying no lag information")
    print()

    width = 12
    print(f"  {'push':<{width}}  {'pushed at':<21}  ingest lag (hours)")
    print(f"  {'-' * width}  {'-' * 21}  {'-' * 34}")
    n_cens = 0
    for s, (kind, lo, hi) in cohort:
        if kind == "censored":
            n_cens += 1
            cell = f">= {lo:6.2f}   CENSORED (not yet ingested)"
        elif kind == "measured-open":
            cell = f"<= {hi:6.2f}   (no poll saw it absent)"
        else:
            cell = f"({lo:6.2f}, {hi:6.2f} ]"
        print(f"  {s['head']:<{width}}  {s['pushed_at']:<21}  {cell}")
    print()

    arrived = [(lo, hi) for k, lo, hi in (o for _, o in cohort) if k != "censored"]
    if arrived:
        highs = sorted(hi for lo, hi in arrived)
        print(f"  ARRIVED ({len(arrived)}): lag brackets top out at {highs[-1]:.2f}h.")
    if n_cens:
        print(f"  CENSORED ({n_cens}): still absent, each a LOWER BOUND on its own lag.")

    if excluded:
        print()
        print(f"  EXCLUDED ({len(excluded)}): pushed before the poll log begins "
              f"({polls[0]['polled_at']})")
        print("  and already present the first time anyone looked. Their only bound is")
        print(f"  'arrived within {max(o[2] for _, o in excluded):.0f}h', which measures the "
              "gap in the poll log,")
        print("  not the feed. Left-censored; Kaplan-Meier cannot use them. They are:")
        heads = [s["head"] for s, _ in excluded]
        for i in range(0, len(heads), 5):
            print("    " + "  ".join(heads[i:i + 5]))
        print()
        print("  THIS IS THE COST OF NOT HAVING KEPT POLLS. Eight days of pushes, all")
        print("  of them still served by the feed today, contribute exactly nothing to")
        print("  the lag distribution, because absence leaves no trace once it heals.")
        print("  The data was not lost by an outage; it was never recorded.")
    print()

    if not cohort:
        print("  No cohort: nothing to estimate. Poll again after the next push.")
        return 1

    print("  survival: fraction of COHORT pushes not yet ingested, by hours since push")
    for t, s, at_risk in survival_curve([o for _, o in cohort]):
        bar = "#" * int(round(s * 40))
        print(f"    t={t:7.2f}h  S={s:5.3f}  n_at_risk={at_risk:<3}  {bar}")
    print()
    print(f"  At n={len(cohort)} this curve is a sketch, not a distribution. Its shape is")
    print("  dominated by which pushes happened to be observed absent, and route (b)")
    print("  into the cohort selects for slow ones. Read the brackets; distrust the")
    print("  curve until the cohort is built from route (a) alone.")
    print()

    print("  " + "-" * 66)
    print("  WHAT THIS DOES NOT SAY, and the omission is the point.")
    print()
    print("  No mean or median lag is printed over the arrived events alone, and")
    print("  none should be computed from the table above. A lag is measurable")
    print("  only if the push arrived; the pushes that have not arrived are")
    print("  exactly the slow ones. An average over arrivals is therefore biased")
    print("  SHORT by construction, and by an amount the arrival data cannot")
    print("  bound. The survival curve above is the honest summary: it carries")
    print("  the censored subjects in the risk set instead of discarding them.")
    print()
    if n_cens:
        print(f"  No completion rate is printed either. {len(arrived)}/{len(cohort)}")
        print("  is not a completion rate; it is a completion rate censored at this")
        print("  poll, and it rises on its own as you wait, with nothing about the")
        print("  feed having changed. If this source ever drops a push permanently,")
        print("  that mass sits at t=infinity and no finite sequence of polls can")
        print("  separate it from mass that has merely not arrived yet.")
        print()
    print("  So there is still no stopping rule, and now there is a reason: the")
    print("  question 'is it lost or slow?' is not identifiable from this feed at")
    print("  any observation time. What a poll log buys is the lag distribution's")
    print("  SHAPE below the censoring horizon -- enough to say an absence is")
    print("  unusual, never enough to say it is permanent.")
    return 2 if n_cens else 0


# ---------------------------------------------------------------- live poll

def do_poll(repo, note, source="measured"):
    events = fetch_events(repo)
    rows = transitions(events)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    poll = {
        "polled_at": fmt_ts(now),
        "repo": repo,
        "source": source,
        "exhaustive": True,
        "events_served": len(events),
        "transitions": len(rows),
        "present": [r["head"] for r in rows],
        "subjects": {r["head"]: {"pushed_at": r["created_at"]} for r in rows},
        "note": note,
    }
    return poll, rows, now


# ---------------------------------------------------------------- selftest

SELFTEST_POLLS = [
    {"polled_at": "2026-01-01T00:00:00Z", "source": "measured", "exhaustive": True,
     "present": ["aaaa"], "subjects": {"aaaa": {"pushed_at": "2026-01-01T00:00:00Z"}}},
    {"polled_at": "2026-01-01T10:00:00Z", "source": "measured", "exhaustive": True,
     "present": ["aaaa", "bbbb"],
     "subjects": {"bbbb": {"pushed_at": "2026-01-01T02:00:00Z"},
                  "cccc": {"pushed_at": "2026-01-01T04:00:00Z"}}},
    {"polled_at": "2026-01-01T20:00:00Z", "source": "measured", "exhaustive": True,
     "present": ["aaaa", "bbbb", "cccc"],
     "subjects": {"dddd": {"pushed_at": "2026-01-01T18:00:00Z"}}},
]

# The fourth poll exists only at clock C: it is what a LATER session would add.
SELFTEST_POLL_LATE = {
    "polled_at": "2026-01-02T08:00:00Z", "source": "measured", "exhaustive": True,
    "present": ["aaaa", "bbbb", "cccc", "dddd"], "subjects": {},
}


def selftest():
    print("SELFTEST -- one synthetic feed, three observer clocks.")
    print()
    print("Four pushes: aaaa, bbbb, cccc ingest promptly-ish; dddd is pushed at")
    print("18:00 and is absent at the 20:00 poll. Clock A reads at 20:00. Clock B")
    print("reads at 08:00 next day with NO new poll -- only the watch moved. Clock")
    print("C reads at 08:00 with a poll that found dddd. Watch the naive verdict")
    print("retract itself between A and C, and watch the bracket only narrow.")
    print()

    polls = [dict(p) for p in SELFTEST_POLLS]
    polls_late = polls + [dict(SELFTEST_POLL_LATE)]
    subs = subjects_from(polls)

    runs = [
        ("clock A  20:00, 3 polls", polls, parse_ts("2026-01-01T20:00:00Z")),
        ("clock B  08:00, 3 polls", polls, parse_ts("2026-01-02T08:00:00Z")),
        ("clock C  08:00, 4 polls", polls_late, parse_ts("2026-01-02T08:00:00Z")),
    ]
    obs_by_run = {}
    for label, ps, now in runs:
        obs = [bracket(s, ps, now) for s in subs]
        obs_by_run[label] = obs
        cens = [s["head"] for s, o in zip(subs, obs) if o[0] == "censored"]
        naive = f"{len(subs) - len(cens)}/{len(subs)} present"
        print(f"  {label}")
        print(f"    naive completeness : {naive}"
              + (f"  -- MISSING {', '.join(cens)}" if cens else "  -- COMPLETE"))
        d = [o for s, o in zip(subs, obs) if s["head"] == "dddd"][0]
        cell = (f">= {d[1]:.2f}h CENSORED" if d[0] == "censored"
                else f"({d[1]:.2f}, {d[2]:.2f}]h")
        print(f"    dddd               : {cell}")
        curve = survival_curve(obs)
        pts = " ".join(f"S({t:.0f}h)={s:.2f}" for t, s, _ in curve if t > 0)
        print(f"    survival estimate  : {pts}")
        print()

    ok = True
    oa, ob, oc = (obs_by_run[r[0]] for r in runs)

    def of(obs, head):
        return [o for s, o in zip(subs, obs) if s["head"] == head][0]

    da, db, dc = of(oa, "dddd"), of(ob, "dddd"), of(oc, "dddd")

    check = da[0] == "censored" and db[0] == "censored"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] dddd is CENSORED at A and B -- never once "
          f"recorded as a failure or a defect")

    check = db[1] > da[1]
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] waiting alone raises its lower bound "
          f"({da[1]:.0f}h -> {db[1]:.0f}h); waiting is evidence, and it is kept")

    check = dc[0] == "measured" and dc[2] >= da[1] and dc[1] >= da[1]
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] at C the bound NARROWS to "
          f"({dc[1]:.0f}, {dc[2]:.0f}]h, consistent with >= {da[1]:.0f}h from clock A")
    print("        -- no earlier statement is retracted, only tightened")

    naive_a = "MISSING" if any(o[0] == "censored" for o in oa) else "COMPLETE"
    naive_c = "MISSING" if any(o[0] == "censored" for o in oc) else "COMPLETE"
    check = naive_a == "MISSING" and naive_c == "COMPLETE"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] the NAIVE verdict retracts outright: "
          f"{naive_a} at A, {naive_c} at C, same feed")

    sa = [round(s, 6) for _, s, _ in survival_curve(oa)]
    sb = [round(s, 6) for _, s, _ in survival_curve(ob)]
    check = sa != sb and all(sb[i] >= sb[i + 1] for i in range(len(sb) - 1))
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] the estimate itself DOES move on follow-up "
          f"time alone ({sa} -> {sb})")
    print("        -- an earlier draft claimed it did not; that was false and this "
          "line is why the claim is gone")

    bb = of(ob, "bbbb")
    check = bb[0] == "measured-open" and bb[1] == 0.0 and bb[2] == 8.0
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] bbbb is measured-open <= 8.00h -- the 00:00 "
          f"poll predates its push, so it grounds no lower bound")

    cc = of(ob, "cccc")
    check = cc[0] == "measured" and cc[1] == 6.0 and cc[2] == 16.0
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] cccc brackets to (6.00, 16.00]h -- the absence "
          f"at the 10:00 poll is what makes the lower bound real")

    polls_recon = [dict(p) for p in SELFTEST_POLLS]
    polls_recon[1] = dict(polls_recon[1], exhaustive=False, source="reconstructed")
    cc2 = [bracket(s, polls_recon, parse_ts("2026-01-02T08:00:00Z"))
           for s in subs if s["head"] == "cccc"][0]
    check = cc2[0] == "measured-open" and cc2[1] == 0.0
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] make that poll RECONSTRUCTED and cccc's lower "
          f"bound vanishes (0.00h)")
    print("        -- a non-exhaustive poll cannot manufacture an absence nobody recorded")

    print()
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--poll", action="store_true",
                    help="fetch the feed now and APPEND a measured poll to poll-log.jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --poll, show the poll but do not append it")
    ap.add_argument("--note", default="", help="note to store with a --poll record")
    ap.add_argument("--selftest", action="store_true", help="offline; no network")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.poll:
        poll, rows, now = do_poll(args.repo, args.note)
        print(f"polled {args.repo} at {poll['polled_at']}: "
              f"{poll['events_served']} events, {poll['transitions']} transitions on {BRANCH}")
        if args.dry_run:
            print("--dry-run: not appended")
        else:
            append_poll(poll)
            print(f"appended to {os.path.relpath(POLL_LOG)}")
        print()

    polls = read_poll_log()
    if not polls:
        print(f"no poll log at {POLL_LOG}. Run with --poll to start one.", file=sys.stderr)
        print("A poll log is the only thing that can date an absence: once a late "
              "push arrives, nothing on the page remembers that it was ever missing.",
              file=sys.stderr)
        return 1
    subs = subjects_from(polls)
    return report(args.repo, subs, polls, now)


if __name__ == "__main__":
    sys.exit(main())
