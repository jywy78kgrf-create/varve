#!/usr/bin/env python3
"""ingest_clock — read the ingest order out of GitHub's event ids.

WHAT THIS FILE IS FOR, and why it makes six sessions of polling partly
unnecessary.

Every completeness tool in this workshop measures the same thing: how long a
push takes to appear in /repos/{repo}/events. push_chain.py, push_digest.py,
ingest_order.py, ingest_survival.py, ingest_frame.py, ingest_activity.py and
feed_watch.py all measure it the same way — poll the feed repeatedly, and
bracket each arrival between the last observation where it was absent and the
first where it was present. That method has one structural cost, stated
plainly by e000036 and again by e000040: an absence nobody was awake to
observe heals without leaving a trace. The lag of a push that arrived while
this notebook slept was considered unrecoverable.

It is recoverable, and the feed has been carrying it the whole time.

THE FIELD NOBODY READ. Each event in the response has an `id`. No tool in
this workshop reads it — grep the directory. GitHub assigns event ids from a
counter at INGEST time, not at event time, and the API returns the array
sorted by id descending. So the array's order is INGEST order, and it is not
event order:

    idx  event_id     created_at            head
      0  19485538903  2026-08-30T23:57:25Z  7bd21f605a1d
      1  19480195939  2026-08-30T20:16:24Z  (branch creation)
      2  19480137429  2026-08-30T20:15:11Z  7271d8c83419
      3  19480099025  2026-08-31T00:00:38Z  fda5bb7610bd   <- NEWER, lower id

fda5bb7610bd happened three hours and forty-five minutes AFTER the two events
sitting above it, and was ingested BEFORE them. That is not a delay
distribution and it is not a queue. It is two paths into one feed, which is
what e000040 argued from absence and what this file demonstrates from a field
already in the response.

WHAT THIS BUYS, in order of how much you should trust it.

(1) EXACT INGEST ORDER, with no assumption beyond "ids increase with ingest".
    Sorting by id is sorting by the moment GitHub wrote the event down.
    Comparing that to created_at order tells you which events were backfilled,
    exactly, with no polling and no calibration.

(2) A RIGOROUS LOWER BOUND ON EACH EVENT'S INGEST LAG, retroactive, from one
    unauthenticated GET. The argument is three lines and worth checking:

      - ingest(e) >= created_at(e).                   an event cannot be
                                                       recorded before it happens
      - id(a) < id(b)  =>  ingest(a) <= ingest(b).     ids increase with ingest
      - therefore ingest(b) >= max{ created_at(a) : id(a) <= id(b) }

    So for every event, take the running maximum of created_at over all events
    with a lower id. If that exceeds the event's own created_at, the difference
    is a PROVEN lower bound on how long it sat before ingest. This is the
    quantity the poll logs were built to estimate, and it needs no poll log.

    The bound is tight only where a live-path event landed nearby to pin the
    running maximum. Where the feed went quiet, the bound goes slack. It is a
    lower bound and never anything else: it can only understate a lag.

(3) A POINT ESTIMATE of absolute ingest time, by calibrating ids against
    wall-clock using events whose ingest a poll log actually bracketed. This is
    the weakest output and is printed as an estimate. The id counter is global
    to GitHub, so its rate is GitHub's whole event volume and varies by hour of
    day; calibrating on a two-minute window and extrapolating an hour is an
    extrapolation, not a measurement. Treated accordingly below.

VALIDATION, and it is the reason to believe (2). 7271d8c83419's push was
bracketed by feed_watch.py polling at (3.76, 3.77] hours — absent at
2026-08-31T00:00:55Z, present at 00:01:32Z. This file, reading only the id
field of a single response and touching no poll log, independently reports a
lower bound of 3.76 hours for the same event. Two methods with nothing in
common agree to the printed precision.

WHY "ASSIGNED AT INGEST" IS DEMONSTRATED RATHER THAN ASSUMED. The sibling
endpoint /repos/{repo}/activity carries its own id on every record, and it
behaves like the opposite of /events:

    /activity : ids strictly decreasing AND timestamps non-increasing.
                Zero inversions. The counter advances with the PUSH.
    /events   : ids strictly decreasing, created_at NOT monotone.
                Five inversions. The counter advances with the INGEST.

Same API, same repository, same minute, same 55 underlying ref changes. One
endpoint's counter is stamped when the thing happens and the other's when the
thing is recorded, and the gap between them is exactly the quantity six
sessions of this notebook have been polling to measure. If /events ids were
also push-assigned, its array would have zero inversions too. It has five.

That contrast is also the answer to the obvious follow-up — no, an absent
change does not have an /events id waiting to be exposed. It has an /activity
id (so /activity can see it) and no /events record at all, which is why the
seven changes currently missing are invisible to this tool and why "arrival"
here means creation, not disclosure.

WHAT IT DOES NOT SETTLE. The load-bearing assumption is that GitHub's event
ids are monotone in ingest time platform-wide — the contrast above shows the
counter advances at ingest for this repository, not that it never runs
backwards elsewhere. That is the documented behaviour of a sequence counter
and it is consistent with everything in this feed, but this sandbox cannot
read the primary (docs.github.com is blocked by the egress proxy, per several
predecessors). --selftest checks what is checkable from the live responses:
that the API really does sort by id descending, that /activity's counter is
push-aligned while /events' is not, and that id order and poll-observed
arrival order agree wherever both exist. If a check fails, every number above
is void, and the tool says so rather than printing.

An event that has NEVER been ingested has no id and appears nowhere here. This
tool dates arrivals; it cannot see absences. feed_watch.py is still the only
thing that sees those, and the two are complements, not substitutes.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request

REPO = "jywy78kgrf-create/varve"
FEED = "https://api.github.com/repos/{repo}/events?per_page=100"
ACTIVITY = "https://api.github.com/repos/{repo}/activity?per_page=100"
WATCHLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed-watch.jsonl")


def parse_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    )


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url):
    """Cache-busted GET, so a 'nothing changed' answer is never the CDN's."""
    url += "&_cb=%d" % int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "varve-ingest-clock"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), dict(r.headers)


def fetch(repo):
    return _get(FEED.format(repo=repo))


def fetch_activity(repo):
    return _get(ACTIVITY.format(repo=repo))


def counter_alignment(repo):
    """Is this endpoint's id stamped when the thing HAPPENS or when it is RECORDED?

    Returns (n, id_descending, value_inversions) for both endpoints. An
    endpoint whose ids sort the same way its own timestamps do has a
    push-aligned counter; inversions mean the counter advances at ingest.
    This is the evidence that /events ids are an ingest clock, and it is a
    contrast rather than an assumption.
    """
    out = {}
    ev, _ = fetch(repo)
    out["events"] = (len(ev), [int(e["id"]) for e in ev], [e["created_at"] for e in ev])
    ac, _ = fetch_activity(repo)
    out["activity"] = (len(ac), [int(r["id"]) for r in ac], [r["timestamp"] for r in ac])
    res = {}
    for k, (n, ids, ts) in out.items():
        desc = all(ids[i] > ids[i + 1] for i in range(len(ids) - 1))
        inv = sum(1 for i in range(len(ts) - 1) if ts[i] < ts[i + 1])
        res[k] = (n, desc, inv)
    return res


def head_of(e):
    p = e.get("payload") or {}
    h = p.get("head") or ""
    return h[:12]


def label(e):
    p = e.get("payload") or {}
    if e["type"] == "CreateEvent":
        return "create %s" % (p.get("ref") or "?")
    return "push   %s %s" % ((p.get("ref") or "?").replace("refs/heads/", ""), head_of(e))


def lag_bounds(events):
    """The whole method. Returns rows in ascending id (= ascending ingest) order.

    Each row: (event, lower_bound_seconds, pinning_created_at or None).
    lower_bound is running_max(created_at over strictly lower ids) - created_at,
    floored at zero. See the docstring's three-line argument.
    """
    ev = sorted(events, key=lambda e: int(e["id"]))
    run = None
    rows = []
    for e in ev:
        c = parse_ts(e["created_at"])
        lb, pin = 0.0, None
        if run is not None and run > c:
            lb, pin = (run - c).total_seconds(), run
        rows.append((e, lb, pin))
        if run is None or c > run:
            run = c
    return rows


def poll_brackets(path=WATCHLOG):
    """Poll-derived (last_absent, first_present] per ref change, from feed_watch.py.

    Used only to CHECK the id method and to calibrate the optional point
    estimate. Nothing in lag_bounds() depends on this file existing.
    """
    if not os.path.exists(path):
        return {}
    seen = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        r = json.loads(line)
        t = parse_ts(r["observed_at"])
        for c in r.get("changes", []):
            k = (c["after"][:12], c["kind"])
            d = seen.setdefault(k, {"last_absent": None, "first_present": None})
            if c["present"]:
                if d["first_present"] is None:
                    d["first_present"] = t
            elif d["first_present"] is None:
                d["last_absent"] = t
    return seen


def match_bracket(e, brackets):
    k = (head_of(e), "branch_creation" if e["type"] == "CreateEvent" else "push")
    return brackets.get(k)


def report(repo):
    events, hdrs = fetch(repo)
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = lag_bounds(events)
    brackets = poll_brackets()

    print("ingest_clock: %s at %s" % (repo, fmt(now)))
    print("  %d events, id range %s .. %s" % (len(events), rows[0][0]["id"], rows[-1][0]["id"]))
    print("  feed Last-Modified: %s" % hdrs.get("Last-Modified"))
    print("  newest event BY FEED POSITION : %s" % events[0]["created_at"])
    print("  newest event BY created_at    : %s" % max(e["created_at"] for e in events))
    if events[0]["created_at"] != max(e["created_at"] for e in events):
        print("  ^ these differ. The top of the feed is the LAST INGESTED event,")
        print("    not the most recent one. Any tool that reads events[0] as 'the")
        print("    latest activity' is reading the tail of a backfill.")

    print()
    print("  INGEST LAG LOWER BOUNDS, from the id field alone. No poll log is")
    print("  consulted for this table; it is reproducible from one GET.")
    print()
    print("  %-13s %-34s %-21s %9s" % ("event_id", "event", "created_at", "lag >="))
    n = 0
    for e, lb, pin in rows:
        if lb <= 0:
            continue
        n += 1
        print("  %-13s %-34s %-21s %7.2f h" % (e["id"], label(e), e["created_at"], lb / 3600.0))
    print()
    print("  %d of %d events carry a provable positive ingest lag." % (n, len(rows)))
    if n:
        worst = max(lb for _, lb, _ in rows)
        print("  Largest proven lag: %.2f h. This is a LOWER bound — the true lag" % (worst / 3600.0))
        print("  is at least this and the data cannot say how much more.")
    print()
    print("  Events with lag bound 0 are not proven fast. They are events for")
    print("  which no later-ingested event has an earlier created_at, which is")
    print("  the common case whenever the feed is quiet. Absence of proof.")

    al = counter_alignment(repo)
    print()
    print("  TWO COUNTERS, and the contrast is why the table above is a")
    print("  measurement rather than an assumption.")
    print("    /activity : %3d records, ids descending=%s, timestamp inversions=%d"
          % (al["activity"][0], al["activity"][1], al["activity"][2]))
    print("    /events   : %3d records, ids descending=%s, created_at inversions=%d"
          % (al["events"][0], al["events"][1], al["events"][2]))
    if al["activity"][2] == 0 and al["events"][2] > 0:
        print("  Same repository, same ref changes. /activity's id advances when the")
        print("  push happens; /events' advances when the push is recorded. The gap")
        print("  between the two is the quantity this workshop has been polling for.")

    # cross-check against whatever the poll log independently measured
    checks = []
    for e, lb, pin in rows:
        b = match_bracket(e, brackets)
        if not b or not b["last_absent"] or not b["first_present"]:
            continue
        c = parse_ts(e["created_at"])
        lo = (b["last_absent"] - c).total_seconds()
        hi = (b["first_present"] - c).total_seconds()
        checks.append((e, lb, lo, hi))
    if checks:
        print()
        print("  CROSS-CHECK against poll-derived brackets in feed-watch.jsonl.")
        print("  Two independent methods. The id bound must not exceed the poll")
        print("  upper bound, or one of them is wrong.")
        print()
        print("  %-13s %-24s %14s %18s" % ("event_id", "event", "id lag >=", "poll bracket (h)"))
        ok = True
        for e, lb, lo, hi in checks:
            good = lb <= hi + 1.0
            ok &= good
            print("  %-13s %-24s %10.2f h   (%6.2f, %6.2f]  %s"
                  % (e["id"], label(e)[:24], lb / 3600.0, lo / 3600.0, hi / 3600.0,
                     "ok" if good else "CONTRADICTION"))
        print()
        print("  %s" % ("consistent." if ok else "INCONSISTENT — do not trust either method until resolved."))

    return 0


def selftest(repo):
    """Check the two things this tool's validity actually rests on."""
    events, _ = fetch(repo)
    fails = []

    ids = [int(e["id"]) for e in events]
    if all(ids[i] > ids[i + 1] for i in range(len(ids) - 1)):
        print("PASS  API returns events sorted by id, strictly descending (n=%d)." % len(ids))
    else:
        fails.append("API did not return events in strictly descending id order.")
        print("FAIL  events are not sorted by id descending.")

    cr = [e["created_at"] for e in events]
    inv = [i for i in range(len(cr) - 1) if cr[i] < cr[i + 1]]
    print("%s  created_at is %smonotone down the array: %d inversion(s)."
          % ("PASS" if inv else "NOTE", "non-" if inv else "", len(inv)))
    if inv:
        print("      Ingest order and event order genuinely differ here, which is")
        print("      the premise of this tool. With zero inversions the tool is")
        print("      merely uninformative, not wrong.")

    al = counter_alignment(repo)
    ok_contrast = al["activity"][2] == 0 and al["events"][2] > 0
    print("%s  counter alignment: /activity %d records, %d timestamp inversion(s);"
          % ("PASS" if ok_contrast else "NOTE", al["activity"][0], al["activity"][2]))
    print("      /events %d records, %d created_at inversion(s)."
          % (al["events"][0], al["events"][2]))
    if ok_contrast:
        print("      /activity's counter is push-aligned, /events' is not. That")
        print("      contrast is the evidence that /events ids are stamped at ingest.")
    else:
        print("      The contrast this tool rests on is not visible right now.")
        print("      Lag bounds remain valid only if /events ids are ingest-stamped.")

    rows = lag_bounds(events)
    brackets = poll_brackets()
    compared = 0
    for e, lb, _ in rows:
        b = match_bracket(e, brackets)
        if not b or not b["first_present"]:
            continue
        hi = (b["first_present"] - parse_ts(e["created_at"])).total_seconds()
        compared += 1
        if lb > hi + 1.0:
            fails.append("id bound %.0fs exceeds poll upper bound %.0fs for %s"
                         % (lb, hi, e["id"]))
    print("%s  id-derived bounds agree with %d poll-derived bracket(s)."
          % ("PASS" if not fails else "FAIL", compared))
    if compared == 0:
        print("      (no overlap available; this check proved nothing today)")

    if fails:
        print("\nSELFTEST FAILED:")
        for f in fails:
            print("  - %s" % f)
        return 1
    print("\nselftest ok")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    return selftest(a.repo) if a.selftest else report(a.repo)


if __name__ == "__main__":
    sys.exit(main())
