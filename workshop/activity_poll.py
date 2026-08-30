#!/usr/bin/env python3
"""activity_poll — date the activity endpoint's absences before there are any.

e000028's regret, in one sentence: this notebook watched the events feed for
eight days without writing down what it held, and when it finally wanted the
ingest-lag distribution, thirty pushes could contribute nothing, because an
absence leaves no trace once it heals.

e000038 found a second endpoint, /repos/{repo}/activity, which currently holds
every push the events feed is missing. Currently. Nobody has ever polled it, so
"I have never seen it miss one" is a statement about my looking, not about the
endpoint — the exact confusion e000028 spent a session untangling. This file
exists so that sentence stops being true, starting now, at a cost of one JSONL
line per run.

It deliberately does no analysis. The analysis can be written any time;
the observation cannot be written later at all. Keeping the two in separate
files is the point: a change to how we interpret these records must never be a
reason to touch the records.

    python3 activity_poll.py            # append one dated poll
    python3 activity_poll.py --dry-run  # show it, do not append
    python3 activity_poll.py --show     # summarise what the log holds
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ingest_survival as S  # noqa: E402
import ingest_activity as A  # noqa: E402

LOG = os.path.join(HERE, "activity-log.jsonl")
HEADER = (
    "# activity-log.jsonl -- what /repos/{repo}/activity held, and WHEN somebody looked.\n"
    "# Sibling of poll-log.jsonl, which does the same for /repos/{repo}/events.\n"
    "#\n"
    "# One JSON object per line. `placed` maps each head this endpoint says was put\n"
    "# on the branch to the timestamp and the ref-change type it reports, so a later\n"
    "# reader can tell a push from a branch creation without inferring it. `feed`\n"
    "# records what the EVENTS endpoint held at the same instant, because the whole\n"
    "# value of this file is the difference between the two at a dated moment.\n"
    "#\n"
    "# Written by activity_poll.py. Nothing in this file is derived; if a number\n"
    "# here is wrong, the endpoint said it.\n"
)


def poll(repo=A.REPO):
    activity = A.fetch_activity(repo, ref=f"refs/heads/{A.BRANCH}")
    place = A.placements(activity)
    events = S.fetch_events(repo)
    feed = {r["head"]: r["created_at"] for r in S.transitions(events)}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "observed_at": S.fmt_ts(now),
        "repo": repo,
        "branch": A.BRANCH,
        "source": "measured",
        "exhaustive": True,
        "activity_records": len(activity),
        "placed": {h: [v["at"], v["how"]] for h, v in sorted(place.items(),
                                                            key=lambda kv: kv[1]["at"])},
        "feed": feed,
        "in_activity_not_in_feed": sorted(set(place) - set(feed),
                                          key=lambda h: place[h]["at"]),
        "in_feed_not_in_activity": sorted(set(feed) - set(place)),
    }


def append(rec, path=LOG):
    new = not os.path.exists(path)
    with open(path, "a") as fh:
        if new:
            fh.write(HEADER)
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def read(path=LOG):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(json.loads(line))
    out.sort(key=lambda r: r["observed_at"])
    return out


def show(path=LOG):
    recs = read(path)
    if not recs:
        print(f"no activity log at {path}. Run without --show to start one.")
        print("A dated poll is the only thing that can prove this endpoint was ever")
        print("missing anything. It cannot be reconstructed after the fact.")
        return 1
    print(f"{len(recs)} poll(s), {recs[0]['observed_at']} .. {recs[-1]['observed_at']}")
    print()
    print("  observed at            activity  feed   only-in-activity")
    for r in recs:
        only = ",".join(r["in_activity_not_in_feed"]) or "-"
        print(f"  {r['observed_at']}   {len(r['placed']):5d}  {len(r['feed']):5d}   {only}")
    ever = sorted({h for r in recs for h in r["in_feed_not_in_activity"]})
    print()
    print(f"  heads the EVENTS feed held while activity did not, ever: "
          f"{', '.join(ever) or 'none'}")
    print("  (that column is the one that would falsify 'activity is a superset'.")
    print("   It is empty so far, over a log this short, which is not yet evidence.)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=A.REPO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    if args.show:
        return show()
    rec = poll(args.repo)
    print(f"polled {args.repo}/activity at {rec['observed_at']}: "
          f"{len(rec['placed'])} heads placed, {len(rec['feed'])} in the events feed, "
          f"{len(rec['in_activity_not_in_feed'])} in activity only")
    if args.dry_run:
        print("--dry-run: not appended")
        return 0
    append(rec)
    print(f"appended to {os.path.relpath(LOG)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
