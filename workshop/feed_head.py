#!/usr/bin/env python3
"""feed_head — is this push late, or has the whole feed stopped?

Every completeness tool in this workshop asks the events feed one question:
"is head X present?" push_chain.py, push_digest.py, ingest_order.py,
ingest_survival.py, ingest_frame.py and ingest_activity.py all ask it, and
none of them records the answer to a second question that is free to ask at
the same moment:

    what is the newest thing the feed holds AT ALL?

Call that the feed head, H = max(created_at) over every event served. It
costs nothing extra — it is already on the page you fetched — and without it
an absence cannot be attributed. With it, the absences split cleanly in two:

  BELOW-HEAD absence   push time < H.  The feed has ingested events created
                       AFTER this push and still does not hold this push.
                       Whatever is delaying it is specific to it. This is the
                       censored observation the survival tools think they are
                       collecting: one subject, one delay, informative.

  ABOVE-HEAD absence   push time > H.  The feed has ingested nothing created
                       since before this push existed. Its absence and the
                       absence of every other above-head push are ONE fact
                       about the feed, observed several times. It is not
                       evidence about this push's own delay, and it is not
                       independent of its neighbours'.

The distinction is not cosmetic and it is not conservative. On 2026-08-30 this
repository had five absent pushes. ingest_survival.py and ingest_frame.py both
carried them as five censored subjects. Four were above-head, gated on a single
feed-level event: H had not moved since 2026-08-29T17:03:38Z. The number of
independent censoring events was two, not five, and a risk set built from the
five reports a precision the data does not have — the design effect that survey
statistics has had a name for since Kish 1965.

What this tool does NOT claim: that an above-head absence is explained, or
harmless, or destined to heal. The feed back-fills below its own head
(e000027), so an above-head push may be late for its own reasons as well.
The claim is weaker and sharper: above the head those two causes are not
separately identifiable, so an above-head absence must not be spent as if it
were a measurement of this push's delay.

    python3 feed_head.py              # live: classify every current absence
    python3 feed_head.py --history    # H over every poll this workshop has kept
    python3 feed_head.py --selftest   # offline, no network

Reads workshop/poll-log.jsonl and workshop/activity-log.jsonl. Writes nothing:
a change to how we interpret those records must never be a reason to touch them.
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

ACTIVITY_LOG = os.path.join(HERE, "activity-log.jsonl")

BELOW = "below-head"
ABOVE = "above-head"


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(json.loads(line))
    return out


def head_from_headers(repo=A.REPO):
    """The feed head, read from the response headers instead of the body.

    GitHub sets Last-Modified on this endpoint to the created_at of the newest
    event it holds. That makes staleness detectable from a HEAD request: no
    pagination, no JSON, no parsing, one round trip, and it works on a feed too
    large to fetch. Every tool in this workshop has been throwing these headers
    away.

    Returned as a second, independent reading of the same quantity, because a
    body and a header that agree are worth more than either alone — and if they
    ever disagree, that disagreement is the finding.

    Cache-Control on this endpoint is max-age=300. A Last-Modified older than
    five minutes is therefore the origin's answer, not a cache's.
    """
    import email.utils
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/events?per_page=1",
        method="HEAD", headers={"Accept": "application/vnd.github+json"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.headers
    lm = h.get("Last-Modified")
    when = None
    if lm:
        when = S.fmt_ts(email.utils.parsedate_to_datetime(lm).astimezone(
            timezone.utc))
    return {
        "last_modified": when,
        "cache_control": h.get("Cache-Control"),
        "poll_interval": h.get("X-Poll-Interval"),
        "etag": (h.get("ETag") or "")[:18],
    }


def feed_head(created_ats):
    """The newest thing the feed holds, over events of EVERY type.

    Deliberately not restricted to PushEvents. A CreateEvent ingested after
    the last push still proves the feed was alive at that moment, and the
    question here is whether the feed is moving, not whether pushes are.
    """
    return max(created_ats) if created_ats else None


def classify(push_at, head):
    """Where an absent push sits relative to the feed head.

    Both arguments are ISO-8601 UTC strings; string order is time order for
    this format, and every timestamp in these logs is normalised to it.
    """
    if head is None:
        return ABOVE
    return BELOW if push_at < head else ABOVE


def split(placed, feed_created, head):
    """Absences, split. `placed` is head -> (push_at, how) from /activity."""
    below, above = [], []
    for h, (at, how) in sorted(placed.items(), key=lambda kv: kv[1][0]):
        if how != "push" or h in feed_created:
            continue
        (below if classify(at, head) == BELOW else above).append((h, at))
    return below, above


def live(repo=A.REPO):
    activity = A.fetch_activity(repo, ref=f"refs/heads/{A.BRANCH}")
    place = {h: (v["at"], v["how"]) for h, v in A.placements(activity).items()}
    events = S.fetch_events(repo)
    created = [e["created_at"] for e in events]
    feed_created = {r["head"]: r["created_at"] for r in S.transitions(events)}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return place, feed_created, feed_head(created), len(events), now


def report(repo=A.REPO):
    place, feed_created, head, n_events, now = live(repo)
    below, above = split(place, feed_created, head)
    nows = S.fmt_ts(now)
    age = S.hours(S.parse_ts(nows) - S.parse_ts(head)) if head else float("nan")

    try:
        hdr = head_from_headers(repo)
    except Exception as exc:                       # header read is a bonus
        hdr = {"last_modified": None, "error": repr(exc)}

    print()
    print(f"  feed head (any event type) : {head}     <- from the body")
    print(f"  Last-Modified header       : {hdr.get('last_modified')}"
          f"     <- from the headers")
    agree = hdr.get("last_modified") == head
    print(f"  the two agree              : {'yes' if agree else 'NO — see below'}")
    print(f"  Cache-Control              : {hdr.get('cache_control')}")
    print(f"  X-Poll-Interval            : {hdr.get('poll_interval')}s")
    print(f"  read at                    : {nows}")
    print(f"  feed head age              : {age:8.2f}h")
    print(f"  events served              : {n_events}")
    if not agree and hdr.get("last_modified"):
        print()
        print("  THE BODY AND THE HEADER DISAGREE ABOUT THE HEAD. That is a")
        print("  finding, not a bug to paper over: one of the two is stale")
        print("  relative to the other, and which one tells you where the")
        print("  staleness lives. Write it down before you touch this tool.")
    print(f"  pushes on {A.BRANCH} per /activity : "
          f"{sum(1 for v in place.values() if v[1] == 'push')}")
    print(f"  of those, held by the feed : {len(feed_created)}")
    print()

    if not below and not above:
        print("  No absences. The two endpoints agree.")
        return below, above

    print("  ABSENT PUSHES, SPLIT BY WHERE THEY SIT RELATIVE TO THE HEAD")
    print()
    print("  push          pushed at             absent    class")
    print("  ------------  --------------------  --------  ----------")
    for h, at in below:
        lag = S.hours(S.parse_ts(nows) - S.parse_ts(at))
        print(f"  {h[:12]}  {at}  {lag:7.2f}h  {BELOW}")
    for h, at in above:
        lag = S.hours(S.parse_ts(nows) - S.parse_ts(at))
        print(f"  {h[:12]}  {at}  {lag:7.2f}h  {ABOVE}")
    print()

    censored_hours_above = sum(
        S.hours(S.parse_ts(nows) - S.parse_ts(at)) for _, at in above)
    print("  WHAT THE SURVIVAL TOOLS ARE CARRYING")
    print(f"    censored subjects they count      : {len(below) + len(above)}")
    print(f"    independent censoring events      : "
          f"{len(below) + (1 if above else 0)}")
    print(f"    censored subjects that measure")
    print(f"      this push's own delay           : {len(below)}")
    print(f"    censored subject-hours contributed")
    print(f"      by the single feed-level event  : {censored_hours_above:.2f}h")
    print()
    print("  The point estimate survives this; the precision does not. Four")
    print("  observations of one frozen feed are one observation, and a risk")
    print("  set that counts them four times reports a confidence it has not")
    print("  earned. Nothing here says the above-head pushes are fine.")
    print()
    return below, above


def history():
    """Reconstruct H over every poll either log has kept.

    Two sources, and they are not equally good, so they are labelled:

      activity-log.jsonl  records the feed's created_at per head at the moment
                          of the poll. H comes straight out of it.
      poll-log.jsonl      records only which heads were present. H has to be
                          reconstructed as the newest PUSH time among them,
                          using /activity's push clock. That is a lower bound
                          on the true H — a CreateEvent above it would not show
                          — and it is restricted to pushes. Marked as such.
    """
    act = read_jsonl(ACTIVITY_LOG)
    if not act:
        print("  activity-log.jsonl is empty; nothing to reconstruct against.")
        return []
    placed = act[-1]["placed"]
    ptime = {h: v[0] for h, v in placed.items()}
    kind = {h: v[1] for h, v in placed.items()}

    rows = []
    for r in read_jsonl(S.POLL_LOG):
        pres = [h for h in r["present"]
                if h in ptime and kind.get(h) == "push"]
        rows.append({
            "at": r["polled_at"],
            "n": len(r["present"]),
            "src": r.get("source", "?"),
            "head": max((ptime[h] for h in pres), default=None),
            "how": "push-only lower bound",
        })
    for r in act:
        f = r["feed"]
        rows.append({
            "at": r["observed_at"],
            "n": len(f),
            "src": r.get("source", "?"),
            "head": max(f.values()) if f else None,
            "how": "exact (created_at)",
        })
    rows.sort(key=lambda x: x["at"])

    print()
    print("  THE FEED HEAD OVER EVERY POLL THIS WORKSHOP HAS KEPT")
    print()
    print("  polled at             n    source         feed head             "
          "move   basis")
    print("  --------------------  ---  -------------  --------------------  "
          "-----  ---------------------")
    # The two bases are not comparable to each other: the exact basis reads
    # created_at, the reconstructed one reads the push clock, and the feed
    # stamps an event about a second after the push. Comparing across them
    # manufactures an advance every time the basis alternates. So each basis
    # is compared only against its own running maximum.
    running = {}
    last_advance = None
    for x in rows:
        head, basis = x["head"], x["how"]
        if head is None:
            move = "-"
        elif basis not in running:
            move = "-"
        elif head > running[basis]:
            move = "up"
            last_advance = x["at"]
        else:
            move = "FROZE"
        print(f"  {x['at']}  {x['n']:3d}  {x['src']:<13}  {head or '-':<20}  "
              f"{move:<5}  {basis}")
        if head is not None:
            running[basis] = max(head, running.get(basis, head))

    if last_advance and rows:
        span = S.hours(S.parse_ts(rows[-1]["at"]) - S.parse_ts(last_advance))
        print()
        print(f"  Last observed advance : {last_advance}")
        print(f"  Observed frozen since : {span:.2f}h and counting")
        print(f"  Current head          : {max(running.values())}")
        print()
        print("  Both numbers are lower bounds on the freeze. The head is older")
        print("  than the first poll that found it stale, and nobody was looking")
        print("  in between.")
    print()
    print("  Read the `n` column against the head column. A poll where n rises")
    print("  while the head does not is back-fill BELOW the head (e000027's")
    print("  finding). A poll where neither moves is a feed that is not")
    print("  ingesting. The two look identical to a tool that only asks")
    print("  'is head X present?'.")
    print()
    return rows


def selftest():
    """Offline. Every assertion is arithmetic or a fixture."""
    ok = 0

    # classify() is a pure string comparison on ISO-8601 UTC.
    assert classify("2026-08-29T00:04:52Z", "2026-08-29T17:03:38Z") == BELOW
    assert classify("2026-08-30T01:05:02Z", "2026-08-29T17:03:38Z") == ABOVE
    assert classify("2026-08-29T17:03:38Z", "2026-08-29T17:03:38Z") == ABOVE
    ok += 1

    # No head at all: nothing can be below it.
    assert classify("2026-08-22T00:00:00Z", None) == ABOVE
    ok += 1

    # feed_head takes the newest event of ANY type, not the newest push.
    # This is the case that matters: on 2026-08-30 the varve feed's newest
    # event was a CreateEvent at 17:04:52Z, one minute above the newest push.
    assert feed_head(["2026-08-29T17:03:38Z", "2026-08-29T17:04:52Z",
                      "2026-08-22T00:47:16Z"]) == "2026-08-29T17:04:52Z"
    assert feed_head([]) is None
    ok += 1

    # split() on the 2026-08-30T22:48Z state, hand-transcribed from the
    # endpoints. Five absences; four above the head; one below.
    placed = {
        "4e075f168b36": ("2026-08-22T00:43:23Z", "branch_creation"),
        "3193a219b470": ("2026-08-29T17:03:37Z", "push"),
        "5966a7fbeefe": ("2026-08-29T00:04:52Z", "push"),
        "d68f1e047de7": ("2026-08-30T01:05:02Z", "push"),
        "a9133247599d": ("2026-08-30T15:59:58Z", "push"),
        "7271d8c83419": ("2026-08-30T20:15:10Z", "push"),
        "cdce5065a01e": ("2026-08-30T20:19:10Z", "push"),
    }
    feed_created = {"3193a219b470": "2026-08-29T17:03:38Z"}
    head = "2026-08-29T17:04:52Z"
    below, above = split(placed, feed_created, head)
    assert [h for h, _ in below] == ["5966a7fbeefe"], below
    assert [h for h, _ in above] == ["d68f1e047de7", "a9133247599d",
                                     "7271d8c83419", "cdce5065a01e"], above
    ok += 1

    # The branch creation is never an absence, whatever the head says. It is
    # not a push, so it was never owed to the PushEvent stream. e000039 is
    # the entry that cost a session to learn this.
    assert "4e075f168b36" not in [h for h, _ in below + above]
    ok += 1

    # A push that IS in the feed is not an absence even when it sits above
    # a stale head — guards against classifying on time alone.
    below2, above2 = split(
        {"aaaaaaaaaaaa": ("2026-08-31T00:00:00Z", "push")},
        {"aaaaaaaaaaaa": "2026-08-31T00:00:01Z"}, head)
    assert below2 == [] and above2 == []
    ok += 1

    # The header witness: GitHub's RFC-1123 Last-Modified must parse to the
    # same instant the body's newest created_at names. This is the exact pair
    # observed on 2026-08-30T22:53Z, and their agreement is what rules out a
    # caching artefact — Cache-Control on this endpoint is max-age=300, so a
    # head 29 hours old cannot be a cache.
    import email.utils
    got = S.fmt_ts(email.utils.parsedate_to_datetime(
        "Sat, 29 Aug 2026 17:04:52 GMT").astimezone(timezone.utc))
    assert got == "2026-08-29T17:04:52Z", got
    assert got == head
    ok += 1

    # The counting claim this tool exists to make: five censored subjects,
    # two independent censoring events.
    assert len(below) + len(above) == 5
    assert len(below) + (1 if above else 0) == 2
    ok += 1

    print(f"selftest: {ok} groups passed, no network used")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", action="store_true",
                    help="feed head across every kept poll")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--repo", default=A.REPO)
    args = ap.parse_args()
    if args.selftest:
        selftest()
    elif args.history:
        history()
    else:
        report(args.repo)


if __name__ == "__main__":
    main()
