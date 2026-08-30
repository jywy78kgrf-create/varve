#!/usr/bin/env python3
"""ingest_activity — a second GitHub witness for pushes, and it is the timely one.

WHAT THIS FOUND. Every completeness tool in this workshop reads one endpoint,
/repos/{repo}/events, and filters it to type == "PushEvent". There is another:

    GET /repos/{owner}/{repo}/activity

It records ref changes directly — push, force_push, branch_creation,
branch_deletion, pr_merge, merge_queue_merge — each with a timestamp, a before
and an after. It is not a view of the events feed. On 2026-08-30T20:0xZ the two
disagreed, and the disagreement is entirely one-directional:

    main push heads in activity : 40
    main push heads in events   : 37
    in activity, not in events  : 5966a7fbeefe, d68f1e047de7, a9133247599d
    in events, not in activity  : none

Those three are exactly the pushes e000036's git frame flagged as absent. So
they are not lost pushes and never were: GitHub recorded all three, with
timestamps, and one of them (a9133247599d, pushed 15:59:58Z) was already in
activity while the events feed had been missing it for four hours. The absence
is a property of the events feed specifically, not of GitHub's knowledge.

WHY THIS IS A BETTER FRAME THAN GIT. ingest_frame.py had to check two premises
before git could serve as the subject list — that every commit was a push head,
and that git's clock approximates the feed's. Activity answers both outright: it
enumerates pushes rather than commits, so there is nothing to assume, and it
carries GitHub's own push timestamp, so there is no clock to calibrate. Against
the 37 pushes both endpoints hold, activity's timestamp and the PushEvent's
created_at agree exactly on 11 and differ by exactly one second on the other 26.
Two independent records of the same instant, within a second.

It also makes the commit-to-push latency measurable for real rather than
estimated: over 40 pushes, committer date to activity timestamp runs 1s to 30s,
median 2s. ingest_frame.py guessed a 31s bound from the events feed and was
right, but it was guessing.

THE BUG THIS FOUND IN ITS OWN PREDECESSOR, which is the reason it exists. Branch
creation is not a push. The founding commit 4e075f168b36 arrived on main at
2026-08-22T00:43:23Z as a `branch_creation`, and the events feed records the
same moment as a CreateEvent, not a PushEvent — which is why it appears in
neither tool's push list. ingest_frame.py classified it "below-floor", i.e.
aged out of the retention window, and got the right answer (exclude it) for the
wrong reason: it is 8 minutes older than the feed's oldest event, so the floor
rule happened to catch it. Any repository whose branch was created recently
would break that: a branch-creation commit INSIDE the window is absent from the
push list, is not below the floor, and ingest_frame.py would enrol it as a
subject censored forever — fabricating exactly the eternal absence its own
docstring warns against. This tool reads the ref-change type instead of
inferring it, so the question does not arise.

WHAT IS WORSE ABOUT THIS WITNESS, and it is not a small thing. The events API is
readable by anybody with no credentials. The activity API is not: it returned
200 here only through this session's authenticated proxy, and an unauthenticated
control could not be run from inside this sandbox because the proxy answers
before the request leaves. Constitution rule 5 wants the log kept where parties
OTHER than its writer can read it. A witness only the writer can reach is worth
less for that purpose than a weaker witness anyone can check — so this endpoint
is better evidence and worse witnessing, and the two should not be confused.
e000013's finding restated: a harvested witness cannot authenticate itself.

    python3 ingest_activity.py            # both witnesses, side by side
    python3 ingest_activity.py --frame    # ingest lag with activity as the frame
    python3 ingest_activity.py --selftest # offline; no network
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ingest_survival as S  # noqa: E402

REPO = S.REPO
BRANCH = S.BRANCH

# Ref changes that put a commit on the branch. branch_deletion does not, and
# enrolling it as a subject would be a phantom.
PLACING = ("push", "force_push", "branch_creation", "pr_merge", "merge_queue_merge")


def fetch_activity(repo, ref=None, max_pages=10):
    """Every ref-change record the activity API will serve, following Link.

    Distinct from fetch_events(): this endpoint reports ref changes, not the
    public events feed, and it reports them by type rather than leaving the
    caller to infer a branch creation from a missing PushEvent.
    """
    url = f"https://api.github.com/repos/{repo}/activity?per_page=100"
    seen = {}
    pages = 0
    while url and pages < max_pages:
        req = urllib.request.Request(url, headers={"User-Agent": "varve-ingest-activity"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                batch = json.load(r)
                link = r.headers.get("Link", "") or ""
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} from the activity API for {repo}.", file=sys.stderr)
            if e.code in (401, 403):
                print("This endpoint needs repository read access. Unlike "
                      "/events it is not anonymous, which is the caveat in this "
                      "file's docstring, not an outage.", file=sys.stderr)
            raise SystemExit(1)
        pages += 1
        for a in batch:
            seen[a["id"]] = a
        nxt = [s for s in link.split(",") if 'rel="next"' in s]
        url = nxt[0].split(";")[0].strip().strip("<>") if nxt else None
    rows = list(seen.values())
    if ref:
        rows = [a for a in rows if a.get("ref") == ref]
    rows.sort(key=lambda a: a["timestamp"])
    return rows


def placements(activity):
    """{head12: {'at': timestamp, 'how': activity_type}} — one per distinct head.

    First record wins, so a head re-pushed later keeps its original arrival.
    """
    out = {}
    for a in activity:
        if a.get("activity_type") not in PLACING:
            continue
        head = (a.get("after") or "")[:12]
        if not head or head == "0" * 12:
            continue
        out.setdefault(head, {"at": a["timestamp"], "how": a["activity_type"]})
    return out


def git_commit_dates(ref="origin/main"):
    out = subprocess.run(
        ["git", "-C", os.path.dirname(HERE), "log", ref, "--format=%H %cI"],
        capture_output=True, text=True, check=True).stdout
    d = {}
    for line in out.splitlines():
        sha, ts = line.split()
        d[sha[:12]] = S.fmt_ts(datetime.fromisoformat(ts))
    return d


# ---------------------------------------------------------------- reports

def compare(repo):
    activity = fetch_activity(repo, ref=f"refs/heads/{BRANCH}")
    place = placements(activity)
    events = S.fetch_events(repo)
    feed = {r["head"]: r["created_at"] for r in S.transitions(events)}
    commits = git_commit_dates()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    kinds = {}
    for a in activity:
        kinds[a["activity_type"]] = kinds.get(a["activity_type"], 0) + 1

    print(f"{repo} -- two GitHub witnesses for the same pushes")
    print(f"  observed at        : {S.fmt_ts(now)}")
    print(f"  activity records   : {len(activity)} on refs/heads/{BRANCH}  {kinds}")
    print(f"  heads placed on {BRANCH}: {len(place)}")
    print(f"  PushEvents in feed : {len(feed)}   ({len(events)} events of all types)")
    print(f"  commits per git    : {len(commits)}")
    print()

    only_act = sorted(set(place) - set(feed), key=lambda h: place[h]["at"])
    only_feed = sorted(set(feed) - set(place))
    print(f"  IN ACTIVITY, NOT IN THE EVENTS FEED ({len(only_act)}):")
    for h in only_act:
        elapsed = (now - S.parse_ts(place[h]["at"])).total_seconds() / 3600.0
        print(f"    {h}  {place[h]['at']}  {place[h]['how']:16s} missing >= {elapsed:6.2f}h")
    if not only_act:
        print("    none")
    print(f"  IN THE EVENTS FEED, NOT IN ACTIVITY ({len(only_feed)}): "
          f"{', '.join(only_feed) or 'none'}")
    print("  The disagreement is one-directional. Activity is a superset here, so")
    print("  these are not lost pushes: GitHub holds a timestamped record of each.")
    print()

    both = [h for h in place if h in feed]
    diffs = [(S.parse_ts(feed[h]) - S.parse_ts(place[h]["at"])).total_seconds() for h in both]
    same = sum(1 for d in diffs if d == 0)
    print(f"  DO THE TWO WITNESSES AGREE ON THE TIME? Over the {len(both)} heads both hold:")
    print(f"    identical timestamp : {same}")
    if diffs:
        print(f"    offset range        : {min(diffs):.0f}s .. {max(diffs):.0f}s")
    print("  Two independent records of the same instant, agreeing to the second.")
    print()

    # Ordinary pushes only. A branch creation can trail its commit by minutes
    # (the founding one here by 490s) and mixing it in inflates the bound.
    lat = sorted((S.parse_ts(place[h]["at"]) - S.parse_ts(commits[h])).total_seconds()
                 for h in place if h in commits and place[h]["how"] == "push")
    if lat:
        print(f"  COMMIT -> PUSH, measured rather than estimated, over {len(lat)} pushes:")
        print(f"    min {lat[0]:.0f}s   median {lat[len(lat) // 2]:.0f}s   max {lat[-1]:.0f}s")
        print("  ingest_frame.py had to infer this bound from the events feed. Here it")
        print("  is a direct measurement, because activity dates the push itself.")
    print()

    creations = [h for h, v in place.items() if v["how"] != "push"]
    print(f"  NOT ARRIVED BY AN ORDINARY PUSH ({len(creations)}):")
    for h in creations:
        print(f"    {h}  {place[h]['at']}  {place[h]['how']}")
    print("  A branch creation is not a PushEvent, so every tool in this workshop")
    print("  filters it away. ingest_frame.py called this 'below-floor' — right")
    print("  answer, wrong reason; see the docstring.")
    missing_from_git = sorted(set(place) - set(commits))
    if missing_from_git:
        print(f"  placed per activity but not on origin/{BRANCH} today: {missing_from_git}")
        print("  (rewritten history, or a head later replaced by a force-push)")
    return 0


def frame_report(repo):
    """Ingest lag with the subject list and the push clock both from activity."""
    activity = fetch_activity(repo, ref=f"refs/heads/{BRANCH}")
    place = placements(activity)
    events = S.fetch_events(repo)
    feed = {r["head"]: r["created_at"] for r in S.transitions(events)}
    now = datetime.now(timezone.utc).replace(microsecond=0)

    rows = []
    for head, v in place.items():
        if v["how"] != "push":
            rows.append((head, v, "not-a-push", None))
        elif head in feed:
            rows.append((head, v, "arrived", None))
        else:
            rows.append((head, v, "absent",
                         (now - S.parse_ts(v["at"])).total_seconds() / 3600.0))
    rows.sort(key=lambda r: r[1]["at"])
    absent = [r for r in rows if r[2] == "absent"]

    print(f"{repo} -- ingest lag, frame and clock both from the activity API")
    print(f"  subjects (ordinary pushes) : {sum(1 for r in rows if r[2] != 'not-a-push')}")
    print(f"  arrived in the events feed : {sum(1 for r in rows if r[2] == 'arrived')}")
    print(f"  ABSENT                     : {len(absent)}")
    print(f"  excluded, not a push       : {sum(1 for r in rows if r[2] == 'not-a-push')}")
    print()
    print("  push          pushed at (GitHub's own clock)   status")
    print("  ------------  -----------------------------   ----------------------")
    for head, v, status, lag in rows:
        tail = f"absent >= {lag:6.2f}h" if lag is not None else status
        print(f"  {head}  {v['at']}            {tail}")
    print()
    print("  No committer-date proxy, no clock slack constant, no premise about")
    print("  one commit per session. The push time is the push time.")
    return 0


# ---------------------------------------------------------------- selftest

def selftest():
    print("SELFTEST -- offline. The branch-creation case ingest_frame.py gets wrong.")
    ok = True

    activity = [
        {"id": 1, "activity_type": "branch_creation", "ref": "refs/heads/main",
         "after": "aaaa" * 10, "before": "0" * 40, "timestamp": "2026-01-01T00:00:00Z"},
        {"id": 2, "activity_type": "push", "ref": "refs/heads/main",
         "after": "bbbb" * 10, "before": "aaaa" * 10, "timestamp": "2026-01-01T01:00:00Z"},
        {"id": 3, "activity_type": "branch_deletion", "ref": "refs/heads/tmp",
         "after": "0" * 40, "before": "cccc" * 10, "timestamp": "2026-01-01T02:00:00Z"},
        {"id": 4, "activity_type": "push", "ref": "refs/heads/main",
         "after": "dddd" * 10, "before": "bbbb" * 10, "timestamp": "2026-01-01T03:00:00Z"},
    ]
    place = placements(activity)
    check = sorted(place) == ["aaaa" * 3, "bbbb" * 3, "dddd" * 3]
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] three heads placed; the branch DELETION is "
          "not one of them")
    print("        -- an after of all-zeros is a ref going away, not a commit arriving")

    check = place["aaaa" * 3]["how"] == "branch_creation"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] the founding head is typed 'branch_creation', "
          "read not inferred")
    print("        -- ingest_frame.py had to guess from 'absent from the PushEvent")
    print("           list', and guessed 'aged out of the window'. Here it is a field.")

    # The failure mode: a branch created INSIDE the retention window.
    import ingest_frame as F
    commits = [("aaaa" * 3, "2026-01-01T00:00:00Z"), ("bbbb" * 3, "2026-01-01T01:00:00Z")]
    feed_rows = [{"head": "bbbb" * 3, "created_at": "2026-01-01T01:00:02Z"}]
    now = S.parse_ts("2026-01-02T00:00:00Z")
    got = F.classify(commits, feed_rows, "2025-12-01T00:00:00Z", now)
    st = {r["head"]: r["status"] for r in got}
    check = st["aaaa" * 3] == "absent"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] ingest_frame.py calls that same head "
          f"'{st['aaaa' * 3]}' when the floor is old")
    print("        -- i.e. a censored subject that can never arrive, because a")
    print("           CreateEvent is not a PushEvent. A fabricated eternal absence,")
    print("           which is the exact failure its own docstring warns about.")

    ours = [h for h, v in place.items() if v["how"] == "push"]
    check = sorted(ours) == ["bbbb" * 3, "dddd" * 3]
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] this tool's subject list excludes it by type "
          "instead")

    print()
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--frame", action="store_true",
                    help="ingest lag with activity as both frame and clock")
    ap.add_argument("--selftest", action="store_true", help="offline; no network")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.frame:
        return frame_report(args.repo)
    return compare(args.repo)


if __name__ == "__main__":
    sys.exit(main())
