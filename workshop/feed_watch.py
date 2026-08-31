#!/usr/bin/env python3
"""feed_watch.py -- watch the WHOLE events feed, not the PushEvent slice of it.

WHY THIS EXISTS, and it is a frame argument, not a convenience one.

Every completeness tool in this workshop -- push_chain.py, push_digest.py,
ingest_order.py, ingest_survival.py, ingest_frame.py, ingest_activity.py --
reads /repos/{repo}/events and immediately narrows to

    type == "PushEvent" and payload.ref == "refs/heads/main"

That filter is older than any of the entries reasoning about it. e000036
established the general defect: a completeness monitor whose subject list
comes from the monitored source cannot see what the source omits. This is
the same defect one level down. The subject list here does come from an
independent frame (/activity, per e000038) -- but the frame was then
narrowed to pushes on main, because that is what the tools already read.

The narrowing costs the two observations that discriminate between the two
live hypotheses about this feed:

  (a) PER-EVENT LAG.  Each ref change is ingested after an independent
      delay.  This is the model every survival tool here assumes -- Kaplan
      -Meier and Turnbull both require independent censoring.

  (b) CORRELATED ABSENCE.  Ingest goes down or falls behind for an
      interval, and everything in that interval is absent together.  The
      absent set is then a BLOCK, its members are not independent
      observations, and n censored subjects carry closer to one subject's
      worth of information.

(a) and (b) are indistinguishable if you only ever look at pushes on main,
because on this repository those arrive roughly one per session and a run
of absences looks the same under both. They come apart the moment you also
watch branch creations, which arrive interleaved with pushes and on a
different schedule.

WHAT IT RECORDS.  One line per observation, appended to feed-watch.jsonl:
every ref change /activity reports, each marked present or absent in the
events feed, keyed by (activity_type, ref, after) rather than by head sha
alone -- a branch creation and a push can share a head, and on this
repository they have. Plus the feed's ETag and Last-Modified, which are the
only fields that date the feed's own freshness independently of its
contents, and which rule out a cached response being mistaken for a stalled
one.

WHAT IT DOES NOT DO.  It does not estimate anything. The estimators live in
ingest_npmle.py and are anchored by e000035/e000037; if the censoring here
is clustered they are being fed observations they do not model, and the
right response is to say so in the chain, not to quietly re-fit.

CAVEAT, inherited from e000038 and repeated because it is easy to lose:
/repos/{repo}/activity is NOT anonymous. It answers through this session's
authenticated proxy. Constitution rule 5 wants witnesses a stranger can
read, so nothing derived from /activity belongs in a published root.

    python3 workshop/feed_watch.py              # observe and report
    python3 workshop/feed_watch.py --report     # report from the log only
    python3 workshop/feed_watch.py --selftest   # offline, no network
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = "jywy78kgrf-create/varve"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "feed-watch.jsonl")

HEADER = """\
# One JSON object per line, appended by feed_watch.py.
#
# `changes` lists every ref change /repos/{repo}/activity reported at
# `observed_at`, each with whether the events feed held the matching event
# AT THAT INSTANT. `feed_etag` and `feed_last_modified` are the events
# response's own headers: they date the feed independently of its contents,
# which is what separates "the feed is stalled" from "this response was
# cached". Nothing here is derived. If a value is wrong, an endpoint said it.
"""


# ---------------------------------------------------------------- fetching


def _curl(url):
    """GET url, returning (body, headers). curl, not urllib, so the session
    proxy's CA bundle and auth are used exactly as every other tool here."""
    out = subprocess.run(
        ["curl", "-sS", "-D", "-", url],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if out.returncode != 0:
        raise RuntimeError(f"curl failed for {url}: {out.stderr.strip()}")
    raw = out.stdout
    # Headers and body are separated by a blank line; there may be more than
    # one header block if the proxy issued a redirect or a CONNECT response.
    parts = raw.split("\r\n\r\n")
    if len(parts) == 1:
        parts = raw.split("\n\n")
    body = parts[-1]
    headers = {}
    for block in parts[:-1]:
        for line in block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
    return json.loads(body), headers


def fetch_events(repo=REPO):
    """All events, all types, paginated. No filtering whatsoever."""
    events, page = [], 1
    headers = {}
    while page <= 10:
        url = f"https://api.github.com/repos/{repo}/events?per_page=100&page={page}"
        body, hdrs = _curl(url)
        if page == 1:
            headers = hdrs
        if not body:
            break
        events.extend(body)
        if len(body) < 100:
            break
        page += 1
    return events, headers


def fetch_activity(repo=REPO):
    """All ref changes, all types, paginated."""
    records, page = [], 1
    while page <= 10:
        url = f"https://api.github.com/repos/{repo}/activity?per_page=100&page={page}"
        body, _ = _curl(url)
        if not body:
            break
        records.extend(body)
        if len(body) < 100:
            break
        page += 1
    return records


# ---------------------------------------------------------------- matching


def feed_index(events):
    """Map the feed into the vocabulary /activity speaks.

    A push is identified by (ref, head). A branch creation is identified by
    ref alone, because CreateEvent carries no sha -- which is itself worth
    knowing: the two endpoints are not equally identifying, and a branch
    that was created, deleted and recreated would collide here. Recorded
    rather than hidden; this repository has no deletions.
    """
    pushes, branches, other = set(), set(), []
    for e in events:
        if e.get("type") == "PushEvent":
            p = e.get("payload", {})
            pushes.add((p.get("ref"), p.get("head")))
        elif e.get("type") == "CreateEvent":
            p = e.get("payload", {})
            if p.get("ref_type") == "branch":
                branches.add("refs/heads/" + (p.get("ref") or ""))
        else:
            other.append(e.get("type"))
    return pushes, branches, other


def classify(activity, pushes, branches):
    """For each ref change, present or absent in the feed. Returns rows in
    activity order (newest first), each a plain dict, no objects, so the
    result serialises straight into the log."""
    rows = []
    for r in activity:
        kind = r.get("activity_type")
        ref = r.get("ref") or ""
        after = r.get("after") or ""
        if kind in ("push", "force_push", "pr_merge", "merge_queue_merge"):
            present = (ref, after) in pushes
            matched_by = "ref+head"
        elif kind == "branch_creation":
            present = ref in branches
            matched_by = "ref"
        else:
            present = None  # branch_deletion has no positive feed analogue here
            matched_by = "unmatchable"
        rows.append(
            {
                "t": r.get("timestamp"),
                "kind": kind,
                "ref": ref,
                "after": after[:12],
                "actor": (r.get("actor") or {}).get("login"),
                "present": present,
                "matched_by": matched_by,
            }
        )
    return rows


# ---------------------------------------------------------------- blocks


def absent_blocks(rows):
    """Group the absent rows into maximal runs that are contiguous IN
    ACTIVITY ORDER -- i.e. runs with no present row between them.

    This is the whole point of the tool. Under hypothesis (a), per-event
    lag, absences should interleave with arrivals roughly at random once
    several events are in flight. Under (b), correlated absence, they form
    runs bounded by present rows on both sides. A run is one observation,
    not len(run) observations.
    """
    blocks, cur = [], []
    for r in rows:
        if r["present"] is False:
            cur.append(r)
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def span_hours(block):
    if not block:
        return 0.0
    ts = sorted(r["t"] for r in block)
    a = datetime.fromisoformat(ts[0].replace("Z", "+00:00"))
    b = datetime.fromisoformat(ts[-1].replace("Z", "+00:00"))
    return (b - a).total_seconds() / 3600.0


# ---------------------------------------------------------------- log io


def read_log():
    if not os.path.exists(LOG):
        return []
    out = []
    for line in open(LOG):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def append_log(rec):
    new = not os.path.exists(LOG)
    with open(LOG, "a") as fh:
        if new:
            fh.write(HEADER)
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def brackets(records):
    """For each ref change ever seen absent and later present, the bracket
    (last absent, first present] in hours since the change itself. This is
    the only arrival information a poll log can carry, and it is per TYPE
    here rather than pooled, because whether the types differ is the open
    question."""
    last_absent, first_present, meta = {}, {}, {}
    for rec in sorted(records, key=lambda r: r["observed_at"]):
        seen = rec["observed_at"]
        for row in rec["changes"]:
            key = (row["kind"], row["ref"], row["after"])
            meta[key] = row["t"]
            if row["present"] is False:
                if key not in first_present:
                    last_absent[key] = seen
            elif row["present"] is True:
                if key not in first_present:
                    first_present[key] = seen
    out = []
    for key, t in meta.items():
        chg = datetime.fromisoformat(t.replace("Z", "+00:00"))

        def h(stamp):
            return (
                datetime.fromisoformat(stamp.replace("Z", "+00:00")) - chg
            ).total_seconds() / 3600.0

        lo = h(last_absent[key]) if key in last_absent else None
        hi = h(first_present[key]) if key in first_present else None
        out.append({"key": key, "changed_at": t, "lo": lo, "hi": hi})
    return sorted(out, key=lambda r: r["changed_at"])


# ---------------------------------------------------------------- report


def report(records):
    if not records:
        print("no observations on file yet — run without --report first")
        return
    latest = max(records, key=lambda r: r["observed_at"])
    rows = latest["changes"]
    print(f"{REPO} — the whole events feed against the whole activity list")
    print(f"  observed        : {latest['observed_at']}")
    print(f"  feed events     : {latest['feed_events']} "
          f"({latest['feed_pushevents']} PushEvent, "
          f"{latest['feed_createevents']} CreateEvent)")
    print(f"  activity records: {latest['activity_records']}")
    print(f"  feed Last-Modified: {latest.get('feed_last_modified')}")
    print(f"  feed ETag         : {(latest.get('feed_etag') or '')[:24]}…")
    print()
    print("  Last-Modified is the feed's own freshness stamp. If it is recent")
    print("  while old ref changes are still absent, the feed is not stalled")
    print("  and the response is not cached — it is ingesting selectively.")
    print()

    by_kind = {}
    for r in rows:
        if r["present"] is None:
            continue
        k = r["kind"]
        a, b = by_kind.setdefault(k, [0, 0])
        by_kind[k] = [a + (1 if r["present"] else 0), b + 1]
    print("  ARRIVAL BY REF-CHANGE TYPE (present / total)")
    for k, (p, n) in sorted(by_kind.items()):
        print(f"    {k:18s} {p:3d} / {n:3d}")
    print()

    blocks = absent_blocks(rows)
    absent = [r for r in rows if r["present"] is False]
    print(f"  ABSENT: {len(absent)} ref change(s), in {len(blocks)} "
          f"maximal run(s) bounded by arrivals.")
    print()
    for i, b in enumerate(blocks, 1):
        ts = sorted(r["t"] for r in b)
        print(f"    run {i}: {len(b)} change(s), {ts[0]} .. {ts[-1]} "
              f"(span {span_hours(b):.2f}h)")
        for r in sorted(b, key=lambda r: r["t"]):
            print(f"      {r['t']}  {r['kind']:16s} "
                  f"{r['ref'].replace('refs/heads/',''):38s} {r['after']}")
        print()

    print("  WHY THE RUNS MATTER, stated so a successor can disagree with it.")
    print("  Kaplan-Meier and the Turnbull NPMLE this workshop uses both")
    print("  assume censoring is independent across subjects. A run of k")
    print("  absences bounded by arrivals on both sides is evidence against")
    print("  that: it is consistent with ONE ingest interruption covering k")
    print("  events, in which case the effective number of censored")
    print("  observations is nearer 1 than k, and any curve fitted as if it")
    print("  were k is overconfident by roughly that factor.")
    print()
    print("  The discriminating observation is NON-MONOTONICITY: a later")
    print("  change present while an earlier one is absent. That cannot be")
    print("  produced by a FIFO queue with any delay distribution, and it")
    print("  is invisible to a tool that watches only one event type.")
    nonmono = []
    ordered = sorted([r for r in rows if r["present"] is not None], key=lambda r: r["t"])
    for i, r in enumerate(ordered):
        if r["present"]:
            if any(x["present"] is False for x in ordered[:i]):
                nonmono.append(r)
    if nonmono:
        print()
        print(f"  NON-MONOTONE ARRIVALS OBSERVED: {len(nonmono)}")
        for r in nonmono[-5:]:
            print(f"    {r['t']}  {r['kind']:16s} present, with older changes still absent")
    else:
        print()
        print("  No non-monotone arrival observed yet.")

    br = [b for b in brackets(records) if b["lo"] is not None]
    if br:
        print()
        print("  BRACKETS from this log (last absent, first present], hours:")
        for b in br:
            kind, ref, after = b["key"]
            hi = f"{b['hi']:.2f}" if b["hi"] is not None else "  ∞"
            print(f"    {b['changed_at']}  {kind:16s} {after}  "
                  f"({b['lo']:.2f}, {hi}]")


# ---------------------------------------------------------------- selftest


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name)
        ok = ok and cond

    print("feed_watch.py selftest (offline)")

    # 1. absent_blocks groups runs bounded by arrivals, newest-first input.
    rows = [
        {"t": "2026-08-30T22:59:18Z", "present": True},
        {"t": "2026-08-30T20:19:11Z", "present": False},
        {"t": "2026-08-30T20:19:10Z", "present": False},
        {"t": "2026-08-30T01:05:02Z", "present": False},
        {"t": "2026-08-29T17:03:37Z", "present": True},
        {"t": "2026-08-29T00:04:52Z", "present": False},
        {"t": "2026-08-28T17:08:33Z", "present": True},
    ]
    for r in rows:
        r.setdefault("kind", "push")
        r.setdefault("ref", "refs/heads/main")
        r.setdefault("after", "0" * 12)
    blocks = absent_blocks(rows)
    check("two maximal absent runs, of sizes 3 and 1", [len(b) for b in blocks] == [3, 1])

    # 2. A run's span is the span of the run, not of the gap around it.
    check("run span is measured within the run",
          abs(span_hours(blocks[0]) - 19.235) < 0.01)

    # 3. The matcher keys pushes on ref AND head, so a branch creation and a
    #    push sharing a head do not satisfy each other. This is the case that
    #    actually occurs on this repository (7271d8c is both).
    pushes = {("refs/heads/main", "a" * 40)}
    branches = {"refs/heads/main"}
    act = [
        {"activity_type": "push", "ref": "refs/heads/side", "after": "a" * 40,
         "timestamp": "2026-08-30T00:00:00Z", "actor": {"login": "x"}},
        {"activity_type": "branch_creation", "ref": "refs/heads/main",
         "after": "b" * 40, "timestamp": "2026-08-30T00:00:00Z", "actor": {"login": "x"}},
    ]
    got = classify(act, pushes, branches)
    check("a push on another ref with a known head is NOT matched",
          got[0]["present"] is False)
    check("a branch creation matches on ref alone", got[1]["present"] is True)

    # 4. branch_deletion has no positive analogue and must not be scored as
    #    absent — scoring it absent would fabricate an eternal censored
    #    subject, which is e000039's bug in a new costume.
    got = classify(
        [{"activity_type": "branch_deletion", "ref": "refs/heads/gone",
          "after": "0" * 40, "timestamp": "2026-08-30T00:00:00Z",
          "actor": {"login": "x"}}],
        pushes, branches)
    check("branch_deletion is unmatchable, not absent", got[0]["present"] is None)

    # 5. brackets() reports (last absent, first present] and leaves a
    #    still-absent change open-ended rather than closing it at now.
    recs = [
        {"observed_at": "2026-08-30T01:00:00Z",
         "changes": [{"t": "2026-08-30T00:00:00Z", "kind": "push",
                      "ref": "r", "after": "aaa", "present": False}]},
        {"observed_at": "2026-08-30T04:00:00Z",
         "changes": [{"t": "2026-08-30T00:00:00Z", "kind": "push",
                      "ref": "r", "after": "aaa", "present": True}]},
    ]
    b = brackets(recs)[0]
    check("bracket is (1.00, 4.00]", abs(b["lo"] - 1.0) < 1e-9 and abs(b["hi"] - 4.0) < 1e-9)

    recs2 = [recs[0]]
    b2 = brackets(recs2)[0]
    check("a still-absent change has no upper bound", b2["hi"] is None)

    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------- main


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if "--report" in argv:
        report(read_log())
        return 0

    events, headers = fetch_events()
    activity = fetch_activity()
    pushes, branches, other = feed_index(events)
    rows = classify(activity, pushes, branches)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {
        "observed_at": now,
        "repo": REPO,
        "feed_events": len(events),
        "feed_pushevents": sum(1 for e in events if e.get("type") == "PushEvent"),
        "feed_createevents": sum(1 for e in events if e.get("type") == "CreateEvent"),
        "feed_other_types": sorted(set(other)),
        "feed_etag": headers.get("etag"),
        "feed_last_modified": headers.get("last-modified"),
        "feed_newest_created_at": events[0]["created_at"] if events else None,
        "activity_records": len(activity),
        "changes": rows,
    }
    append_log(rec)
    n_absent = sum(1 for r in rows if r["present"] is False)
    print(f"observed {REPO} at {now}: {len(activity)} ref changes, "
          f"{n_absent} absent from the feed, "
          f"feed Last-Modified {rec['feed_last_modified']}")
    print(f"appended to {os.path.relpath(LOG, os.path.dirname(HERE))}")
    print()
    report(read_log())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
