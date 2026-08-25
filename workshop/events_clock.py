#!/usr/bin/env python3
"""events_clock — how long until GitHub's events API forgets a given push?

WHY THIS FILE EXISTS, and what it corrects.

workshop/push_chain.py measures the events window as a COUNT. Its docstring
says, in as many words, "the ceiling is 300 events, and it is a count, not a
duration: a busy day evicts more history than a quiet month," and its
no-transitions exit says "the window is a count, not a duration." e000012 built
the same claim into the chain ("denominated in pushes rather than days") and
e000014 staked a prediction on it. push_chain.py is anchored by e000011 and
e000013, so per workshop/README.md it is not rewritten; this file supersedes
its RETENTION MODEL only. Everything push_chain.py says about transitions and
linkage is untouched and still correct.

The correction: there are TWO independent eviction clocks, not one.

    count clock : the ~300-event pagination ceiling. Spent by writing.
    age clock   : events older than the retention window are dropped
                  REGARDLESS of count. Spent by the calendar.

The age clock is what push_chain.py and e000012 missed. GitHub's own
documentation states it with a parenthetical that exists precisely to rule out
the count-only reading, quoted in a community thread this sandbox can reach
(github.com/orgs/community/discussions/141827):

    "Only events created within the past 90 days will be included in
     timelines. Events older than 90 days will not be included (even if the
     total number of events in the timeline is less than 300)."

"even if ... less than 300" is the whole ballgame. Age evicts on its own.

WHICH WINDOW IS IN FORCE IS NOT SETTLED HERE. Two independent web searches this
session report that GitHub moved the window from 90 days to 30 days effective
2025-01-30, announced in a changelog post. This sandbox's egress proxy blocks
both docs.github.com and github.blog (CONNECT 403), so the primary text could
not be read, and the reachable community thread quotes the OLDER 90-day
wording. So this tool treats the window as UNKNOWN, carries both candidates,
and prints what the live data would have to look like to settle it. Do not let
it claim more than that.

WHY THE DIFFERENCE MATTERS FOR A QUIET LOG. The two clocks scale in opposite
directions with activity, so the binding one depends entirely on how much the
log is written to:

    at 10 events/day  the count clock binds  (300 events in 30 days)
    at  1 event /day  the age  clock binds   (30 days is 30 events, not 300)

e000011's inversion — "the log is well-witnessed exactly in proportion to how
little it is written to" — only holds while the count clock binds. Below the
crossover it is simply false: writing less does not buy one extra day of
memory, because the calendar is spending the budget instead. This repository
is currently far below the crossover.

USAGE (no credentials; the events API is public for a public repo):

    python3 events_clock.py                      # both clocks, this repo
    python3 events_clock.py --watch 4e075f168b36 # track one push by `before`
    python3 events_clock.py --json

EXIT: 0 always unless the fetch fails. This is an instrument, not a check.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
COUNT_CEILING = 300
# Candidate retention windows, in days. Both are documented figures for the
# same endpoint at different times; neither is measured by this tool.
CANDIDATE_WINDOWS = (30, 90)
UTC = datetime.timezone.utc


def git(*args):
    out = subprocess.run(("git",) + args, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def infer_repo():
    url = git("remote", "get-url", "origin") or ""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def parse_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def fetch_events(repo, token=None):
    """Every event GitHub still serves, newest first. A non-list response is
    the pagination ceiling closing, which is coverage information, not error."""
    out, page, capped = [], 1, False
    while page <= 10:
        url = "%s/repos/%s/events?per_page=100&page=%d" % (API, repo, page)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "varve-events-clock",
            **({"Authorization": "Bearer %s" % token} if token else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 422):
                capped = True
                break
            sys.exit("GitHub API %s for %s events" % (e.code, repo))
        if not isinstance(data, list):
            capped = True
            break
        if not data:
            break
        out.extend(data)
        page += 1
    return out, capped


def rate(events, now, hours):
    cut = now - datetime.timedelta(hours=hours)
    return sum(1 for e in events if parse_ts(e["created_at"]) >= cut)


def find_watched(events, before_prefix):
    """The PushEvent whose payload `before` starts with the given prefix."""
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        b = (e.get("payload") or {}).get("before") or ""
        if b.startswith(before_prefix):
            return e
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--watch", metavar="BEFORE",
                   help="track the PushEvent carrying this `before` sha "
                        "(prefix ok) and report its remaining life")
    p.add_argument("--now", metavar="ISO8601",
                   help="override the clock, for testing (UTC)")
    p.add_argument("--token", help="optional; the events API is public")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")
    now = parse_ts(a.now) if a.now else datetime.datetime.now(UTC).replace(microsecond=0)

    events, capped = fetch_events(repo, a.token)
    if not events:
        sys.exit("no events served for %s -- nothing to measure" % repo)

    stamps = sorted(parse_ts(e["created_at"]) for e in events)
    oldest, newest = stamps[0], stamps[-1]
    served = len(events)
    oldest_age = (now - oldest).total_seconds() / 86400.0

    r24, r72 = rate(events, now, 24), rate(events, now, 72)
    span = max((now - oldest).total_seconds() / 86400.0, 1e-9)
    cumulative = served / span

    # COUNT CLOCK. Headroom in events; days-to-eviction depends on future rate,
    # which is unknowable, so it is projected at each observed rate separately
    # rather than at one blended number that hides the assumption.
    headroom = COUNT_CEILING - served
    projections = {}
    for label, per_day in (("trailing 24h", r24), ("trailing 72h", r72 / 3.0),
                           ("cumulative", cumulative)):
        projections[label] = (per_day, (headroom / per_day) if per_day > 0 else None)

    # AGE CLOCK. Independent of everything above.
    age_evictions = {w: oldest + datetime.timedelta(days=w) for w in CANDIDATE_WINDOWS}

    # What the live data settles about the window. The oldest served event's age
    # is a LOWER BOUND on the true window only while the count is below the
    # ceiling -- once capped, eviction is ambiguous between the two clocks.
    verdict = []
    if capped or served >= COUNT_CEILING:
        verdict.append("count ceiling reached -- age of oldest served event no "
                       "longer bounds the retention window (either clock could "
                       "be doing the evicting)")
    else:
        verdict.append("count is below the ceiling (%d/%d), so the oldest served "
                       "event's age of %.2f days is a hard LOWER BOUND on the "
                       "retention window" % (served, COUNT_CEILING, oldest_age))
        for w in CANDIDATE_WINDOWS:
            if oldest_age > w:
                verdict.append("REFUTED: a %d-day window is impossible -- an "
                               "event %.2f days old is still served with the "
                               "count under the ceiling" % (w, oldest_age))
            else:
                verdict.append("a %d-day window is still consistent with the "
                               "data (need %.2f more days of an unevicted "
                               "oldest event to refute it)" % (w, w - oldest_age))

    watched = None
    if a.watch:
        ev = find_watched(events, a.watch)
        if ev is None:
            watched = {"before": a.watch, "still_served": False}
        else:
            when = parse_ts(ev["created_at"])
            watched = {
                "before": (ev["payload"] or {}).get("before"),
                "still_served": True,
                "created_at": ev["created_at"],
                "age_days": round((now - when).total_seconds() / 86400.0, 3),
                "newer_events": sum(1 for s in stamps if s > when),
                "count_evicts_after_n_more_events": COUNT_CEILING - sum(
                    1 for s in stamps if s > when) - 1,
                "age_evicts_on": {w: (when + datetime.timedelta(days=w)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ") for w in CANDIDATE_WINDOWS},
            }

    if a.json:
        print(json.dumps({
            "repo": repo, "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "events_served": served, "window_capped": capped,
            "oldest_event": oldest.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "newest_event": newest.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "oldest_age_days": round(oldest_age, 3),
            "rate_24h": r24, "rate_72h": r72,
            "cumulative_per_day": round(cumulative, 3),
            "count_headroom": headroom,
            "count_projections_days": {k: (None if v[1] is None else round(v[1], 1))
                                       for k, v in projections.items()},
            "age_eviction_of_oldest": {w: d.strftime("%Y-%m-%dT%H:%M:%SZ")
                                       for w, d in age_evictions.items()},
            "window_days_candidates": list(CANDIDATE_WINDOWS),
            "window_lower_bound_days": (None if capped else round(oldest_age, 3)),
            "verdict": verdict, "watched": watched,
        }, indent=1))
        return 0

    print("%s -- %d events served%s, spanning %s .. %s\n"
          % (repo, served, " (CAPPED)" if capped else "",
             oldest.strftime("%Y-%m-%d %H:%M"), newest.strftime("%Y-%m-%d %H:%M")))

    print("COUNT CLOCK   %d of %d used, %d headroom" % (served, COUNT_CEILING, headroom))
    print("  observed rate   trailing 24h : %d events" % r24)
    print("                  trailing 72h : %d events (%.2f/day)" % (r72, r72 / 3.0))
    print("                  cumulative   : %.2f events/day over %.2f days"
          % (cumulative, span))
    for label, (per_day, days) in projections.items():
        if days is None:
            print("  at the %-13s rate (%.2f/day): NEVER -- no events in that window"
                  % (label, per_day))
        else:
            when = now + datetime.timedelta(days=days)
            print("  at the %-13s rate (%.2f/day): ceiling in %.0f days, %s"
                  % (label, per_day, days, when.strftime("%Y-%m-%d")))

    print("\nAGE CLOCK     oldest served event is %.2f days old" % oldest_age)
    print("  the window in force is NOT measured here -- see this file's docstring.")
    for w in CANDIDATE_WINDOWS:
        print("  if the window is %2d days: that event drops out %s"
              % (w, age_evictions[w].strftime("%Y-%m-%d")))

    print("\nWHICH CLOCK BINDS")
    soonest_count = min((d for _, d in projections.values() if d is not None),
                        default=None)
    for w in CANDIDATE_WINDOWS:
        age_days = w - oldest_age
        if soonest_count is None:
            print("  %2d-day window: AGE binds (no measurable write rate at all)" % w)
        elif age_days < soonest_count:
            print("  %2d-day window: AGE binds -- %.0f days vs %.0f for the count"
                  % (w, age_days, soonest_count))
        else:
            print("  %2d-day window: COUNT binds -- %.0f days vs %.0f for age"
                  % (w, soonest_count, age_days))

    print("\nWHAT THE LIVE DATA SETTLES")
    for line in verdict:
        print("  - %s" % line)

    if watched is not None:
        print("\nWATCHED PUSH  before=%s" % watched["before"])
        if not watched["still_served"]:
            print("  NOT SERVED -- already evicted, or never in this window.")
            print("  Which clock evicted it cannot be read off its absence; compare")
            print("  the count against the ceiling and the date against the window.")
        else:
            print("  still served, created %s (%.2f days old)"
                  % (watched["created_at"], watched["age_days"]))
            print("  %d newer events sit above it; the count evicts it after %d more"
                  % (watched["newer_events"],
                     watched["count_evicts_after_n_more_events"]))
            for w in CANDIDATE_WINDOWS:
                print("  a %2d-day age window evicts it on %s"
                      % (w, watched["age_evicts_on"][w]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
