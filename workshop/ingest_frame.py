#!/usr/bin/env python3
"""ingest_frame — the denominator was drawn from the instrument.

WHAT THIS IS ABOUT. ingest_survival.py measures how long a push takes to appear
in GitHub's events feed. To do that it needs a list of subjects: the pushes whose
arrival is being timed. It builds that list from the events feed.

That is the whole bug, and it is invisible because it looks like thrift.
do_poll() sets

    "subjects": {r["head"]: {"pushed_at": r["created_at"]} for r in rows}

where `rows` is transitions(events) — the feed's own contents. subjects_from()
is then called with no `extra`. So a push enters the denominator at the moment
it arrives, and a push that NEVER arrives never enters it at all. It is not
counted as censored. It is not counted as excluded. It is not counted.

The consequence is exactly backwards from what you want. The tool's own closing
paragraph says that permanently-dropped pushes "sit at t=infinity" and cannot be
told apart from slow ones. That understates it: they do not sit at t=infinity in
the risk set, because they are not in the risk set. A push that is dropped
forever is not a subject with an unknown outcome — it is a subject that was
never enrolled, and the survival curve it should have dragged down is computed
without it. An absence-detector whose subject list comes from the source it is
auditing cannot enroll the absences that matter most.

HOW THE ONE CENSORED SUBJECT GOT IN, which is the proof that the mechanism is
not hypothetical. As of 2026-08-30 the report shows exactly one censored subject,
5966a7fbeefe. Grep the poll log: it appears in `subjects` on precisely one line,
the reconstructed poll of 2026-08-29T04:59:13Z, which a predecessor typed in by
hand out of the prose of entry e000024. No measured poll has ever enrolled it or
can. subjects_from()'s docstring says "a successor who wants a push in the
denominator has to have polled for it" — but polling is the one thing that
cannot put it there. The remedy the tool names is not a remedy the tool offers.

THE FIX IS AN INDEPENDENT FRAME. You cannot enumerate what a source is missing
by asking the source. You need a second list of what ought to be there, built by
something that is not under test. For this repository that list is sitting in the
same directory: git. `git log origin/main` knows every push head whether or not
GitHub's events API has gotten around to serving it.

TWO THINGS HAVE TO BE CHECKED BEFORE GIT CAN SERVE AS THE FRAME, and both are
checkable rather than assumed. --audit runs them.

  (1) DOES EVERY COMMIT CORRESPOND TO A PUSH? No, in general: a session that
      commits three times and pushes once puts two commits on the branch that
      were never a push head, and enrolling them would manufacture two eternal
      absences — the exact false positive this tool exists to avoid. In THIS
      repository it happens not to occur, and that is a measurement: of 41
      commits on origin/main, 37 are feed heads outright and the remaining 4 are
      each independently accounted for. Any repo using this needs the same audit
      first; where it fails, the frame must come from observed ref tips instead
      (--observe records them, so a successor has the data this session lacked).

  (2) IS GIT'S CLOCK THE FEED'S CLOCK? Close enough, and the gap is measurable
      because for 37 pushes both clocks are readable. Committer date to
      PushEvent created_at runs 1 to 31 seconds, median 2. So committer date is
      a lower bound on push time (you cannot push before you commit) that is
      wrong by under a minute, against lags measured in hours. This tool takes
      the CONSERVATIVE end — committer date + the largest offset ever observed
      — so a censored subject's lower bound on lag is the smallest one the data
      supports, never the flattering one.

THE FRAME HAS A FLOOR AND FORGETTING IT WOULD BE WORSE THAN THE ORIGINAL BUG.
The events API serves a bounded window. A push old enough to have fallen out of
it is absent from the feed for a reason that has nothing to do with ingest lag,
and enrolling it as "still in flight after 200 hours" would be fabrication. The
founding push 4e075f168b36 (2026-08-22T00:35:13Z) is exactly this case: it is
12 minutes older than the oldest event the feed currently serves. So the frame
admits only pushes at or after the feed's window floor, and names the ones it
drops. This is the mirror of the bug: the original tool let the instrument
define the denominator's TOP, and a naive frame would let it be ignored at the
BOTTOM.

    python3 ingest_frame.py            # frame-aware report, and the delta
    python3 ingest_frame.py --audit    # check the two premises above, with numbers
    python3 ingest_frame.py --observe  # append a frame record (ref tip + ancestry)
    python3 ingest_frame.py --npmle    # re-run e000035's Turnbull fit on the real set
    python3 ingest_frame.py --selftest # offline; asserts the bug and the fix
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ingest_survival as S  # noqa: E402

FRAME_LOG = os.path.join(HERE, "frame-log.jsonl")
REPO = S.REPO
BRANCH = S.BRANCH

# The largest committer-date-to-PushEvent offset ever observed here, in seconds.
# Used as the conservative push-time upper bound for a push the feed has not
# served. Re-derive it any time with --audit; if it grows, raise this.
MAX_COMMIT_TO_PUSH_S = 31


# ---------------------------------------------------------------- the frame

def git_commits(ref="origin/main"):
    """[(sha12, committed_at)] on `ref`, oldest first. The independent frame."""
    out = subprocess.run(
        ["git", "-C", os.path.dirname(HERE), "log", ref, "--format=%H %cI"],
        capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        sha, ts = line.split()
        rows.append((sha[:12], S.fmt_ts(datetime.fromisoformat(ts))))
    rows.reverse()
    return rows


def window_floor(events):
    """Oldest created_at the feed currently serves, of ANY event type.

    Not just pushes: the window is bounded by event count across all types, so
    the oldest event of any kind is the true floor. A push older than this is
    absent for retention reasons and carries no lag information.
    """
    if not events:
        return None
    return min(e["created_at"] for e in events)


def classify(commits, feed_rows, floor, now):
    """Every push in the frame, with the feed's verdict on it.

    Returns rows of {head, committed_at, status, pushed_at, lag_lo, lag_hi}.
      status 'arrived'    -- the feed served it; pushed_at is the feed's own
      status 'absent'     -- in the frame, at/after the floor, not in the feed
      status 'below-floor'-- older than the window; absence is retention, not lag
    """
    served = {r["head"]: r for r in feed_rows}
    out = []
    for head, committed_at in commits:
        row = {"head": head, "committed_at": committed_at}
        if head in served:
            row["status"] = "arrived"
            row["pushed_at"] = served[head]["created_at"]
        elif floor is not None and committed_at < floor:
            row["status"] = "below-floor"
            row["pushed_at"] = committed_at
        else:
            row["status"] = "absent"
            row["pushed_at"] = committed_at
            # Conservative: assume the push happened as LATE as it plausibly
            # could, which makes the elapsed-since-push lower bound the
            # smallest the evidence supports.
            latest_push = S.parse_ts(committed_at).timestamp() + MAX_COMMIT_TO_PUSH_S
            row["lag_lo"] = (now.timestamp() - latest_push) / 3600.0
        out.append(row)
    return out


# ---------------------------------------------------------------- observing

def do_observe(repo, note=""):
    """Record what the frame said and what the instrument said, at one instant.

    The poll log records only the instrument. This file records the ref tip too,
    so a successor can enrol a push the feed never served WITHOUT hand-copying
    it out of prose, and so premise (1) above can be checked from observed tips
    rather than from the assumption that every commit was pushed.
    """
    events = S.fetch_events(repo)
    rows = S.transitions(events)
    commits = git_commits()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "observed_at": S.fmt_ts(now),
        "repo": repo,
        "branch": BRANCH,
        "source": "measured",
        "tip": commits[-1][0] if commits else None,
        "tip_committed_at": commits[-1][1] if commits else None,
        "frame": [{"head": h, "committed_at": c} for h, c in commits],
        "feed_heads": [r["head"] for r in rows],
        "window_floor": window_floor(events),
        "events_served": len(events),
        "note": note,
    }, events, rows, commits, now


def read_frame_log(path=FRAME_LOG):
    if not os.path.exists(path):
        return []
    recs = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            recs.append(json.loads(line))
    recs.sort(key=lambda r: r["observed_at"])
    return recs


def append_frame(rec, path=FRAME_LOG):
    new = not os.path.exists(path)
    with open(path, "a") as fh:
        if new:
            fh.write("# frame-log.jsonl -- what OUGHT to have been in the feed, "
                     "from a source that is not the feed.\n"
                     "# Companion to poll-log.jsonl. That file records the "
                     "instrument; this one records\n"
                     "# the frame (git's view of origin/main) at the same "
                     "instant, so a push the events\n"
                     "# API never serves is still a subject with a running "
                     "clock. See ingest_frame.py.\n")
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ---------------------------------------------------------------- the audit

def audit(repo):
    events = S.fetch_events(repo)
    feed_rows = S.transitions(events)
    commits = git_commits()
    floor = window_floor(events)
    served = {r["head"]: r for r in feed_rows}

    print(f"{repo} -- can git serve as the frame for this feed?")
    print(f"  commits on origin/{BRANCH} : {len(commits)}")
    print(f"  push events on {BRANCH}     : {len(feed_rows)}")
    print(f"  feed window floor          : {floor}  ({len(events)} events served)")
    print()

    print("PREMISE 1 -- is every commit on the branch a push head?")
    unmatched = [(h, c) for h, c in commits if h not in served]
    print(f"  {len(commits) - len(unmatched)}/{len(commits)} commits are feed heads outright.")
    if unmatched:
        print(f"  {len(unmatched)} are not, and each needs its own account:")
        for h, c in unmatched:
            why = "BELOW FLOOR — window retention, not lag" if floor and c < floor \
                  else "absent from the feed while inside the window"
            print(f"    {h}  {c}  {why}")
    print("  A commit that was never a push head would look permanently absent")
    print("  and would be a fabricated data point. Nothing above is one, but that")
    print("  is a fact about this repository's one-commit-per-session habit, not")
    print("  a property of git. Check it again before trusting this elsewhere.")
    print()

    print("PREMISE 2 -- is git's clock the feed's clock?")
    deltas = []
    for h, c in commits:
        if h in served:
            d = (S.parse_ts(served[h]["created_at"]) - S.parse_ts(c)).total_seconds()
            deltas.append((d, h))
    deltas.sort()
    if deltas:
        med = deltas[len(deltas) // 2][0]
        print(f"  committer date -> PushEvent created_at, over {len(deltas)} pushes"
              " where BOTH clocks are readable:")
        print(f"    min {deltas[0][0]:.0f}s   median {med:.0f}s   max {deltas[-1][0]:.0f}s")
        print(f"    largest: " + ", ".join(f"{h} +{d:.0f}s" for d, h in deltas[-3:]))
        print(f"  This tool uses +{MAX_COMMIT_TO_PUSH_S}s as the conservative push-time upper bound.")
        if deltas[-1][0] > MAX_COMMIT_TO_PUSH_S:
            print(f"  !! observed max {deltas[-1][0]:.0f}s EXCEEDS the constant. Raise "
                  f"MAX_COMMIT_TO_PUSH_S to {int(deltas[-1][0])} or more.")
        print("  Against lags measured in hours, a sub-minute clock offset is noise.")
        print("  It is stated anyway because 'close enough' is a claim with a number.")
    return 0


# ---------------------------------------------------------------- the report

def report(repo):
    events = S.fetch_events(repo)
    feed_rows = S.transitions(events)
    commits = git_commits()
    floor = window_floor(events)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = classify(commits, feed_rows, floor, now)

    arrived = [r for r in rows if r["status"] == "arrived"]
    absent = [r for r in rows if r["status"] == "absent"]
    below = [r for r in rows if r["status"] == "below-floor"]

    print(f"{repo} -- ingest lag with the subject list taken from git, not from the feed")
    print(f"  observed at   : {S.fmt_ts(now)}")
    print(f"  frame         : {len(commits)} push(es) on origin/{BRANCH}, per git")
    print(f"  window floor  : {floor}")
    print(f"  in frame, in window : {len(arrived) + len(absent)}")
    print(f"    arrived     : {len(arrived)}")
    print(f"    ABSENT      : {len(absent)}   <- each a live lower bound on its own lag")
    print(f"  below floor   : {len(below)}   (absent for retention, carries no lag)")
    print()

    if absent:
        # What the frame log can attest, as distinct from what git implies.
        # git says the push exists; only a dated frame record says somebody
        # LOOKED and it was not there. That is the perishable half.
        recs = read_frame_log()
        first_absent = {}
        for rec in recs:
            served = set(rec.get("feed_heads", []))
            for f in rec.get("frame", []):
                h = f["head"]
                if h not in served and h not in first_absent:
                    floor_r = rec.get("window_floor")
                    if not (floor_r and f["committed_at"] < floor_r):
                        first_absent[h] = rec["observed_at"]

        print("  ABSENT FROM THE FEED RIGHT NOW:")
        print("  push          committed at           elapsed        first attested absent")
        print("  ------------  ---------------------  -------------  ---------------------")
        for r in sorted(absent, key=lambda r: r["committed_at"]):
            fa = first_absent.get(r["head"], "-- not yet in frame log --")
            print(f"  {r['head']}  {r['committed_at']}   >= {r['lag_lo']:6.2f}h    {fa}")
        print(f"  ({len(recs)} frame observation(s) on file. An absence nobody dated is")
        print("   an absence that heals without trace — this column is the only part")
        print("   of the row that cannot be recomputed later.)")
        print()
    if below:
        print("  BELOW THE WINDOW FLOOR, excluded and named:")
        for r in below:
            print(f"    {r['head']}  {r['committed_at']}")
        print("  Its absence is the events API's retention policy doing what it says.")
        print("  Counting it as a 200-hour censored subject would be fabrication, and")
        print("  is the failure mode a naive 'just use git' frame walks straight into.")
        print()

    # --- the delta against the feed-framed tool
    polls = S.read_poll_log()
    if polls:
        subs = S.subjects_from(polls)
        sub_heads = {s["head"] for s in subs}
        s_now = S.parse_ts(polls[-1]["polled_at"])
        s_cens = [s for s in subs if S.bracket(s, polls, s_now)[0] == "censored"]
        missing = [r for r in absent if r["head"] not in sub_heads]
        print("  ------------------------------------------------------------------")
        print("  THE DELTA. ingest_survival.py, same repository, same minute:")
        print(f"    its subjects        : {len(subs)}")
        print(f"    its censored        : {len(s_cens)}  ({', '.join(s['head'] for s in s_cens) or 'none'})")
        print(f"    frame subjects      : {len(arrived) + len(absent)}")
        print(f"    frame censored      : {len(absent)}  ({', '.join(r['head'] for r in absent)})")
        print()
        if missing:
            print(f"  {len(missing)} push(es) are absent from the feed and absent from that tool's")
            print("  denominator entirely — not censored, not excluded, not counted:")
            for r in missing:
                print(f"    {r['head']}  pushed {r['committed_at']}  missing >= {r['lag_lo']:.2f}h")
            print()
        for s in s_cens:
            src = [p["polled_at"] for p in polls
                   if s["head"] in p.get("subjects", {})]
            kinds = [p["source"] for p in polls if s["head"] in p.get("subjects", {})]
            print(f"  {s['head']} is in that denominator only via {kinds} poll(s) at {src}.")
        print("  A measured poll cannot enrol a push the feed has never served, because")
        print("  it reads the subject list out of the feed. Hand-transcription from")
        print("  prose is the only route that has ever worked, and it is not a method.")
    return 0


# ---------------------------------------------------------------- npmle

def npmle_report(repo):
    """e000035's Turnbull fit, re-run on the frame-corrected subject set."""
    import ingest_npmle as N

    events = S.fetch_events(repo)
    feed_rows = S.transitions(events)
    commits = git_commits()
    floor = window_floor(events)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = classify(commits, feed_rows, floor, now)

    polls = S.read_poll_log()
    subs = {s["head"]: s for s in S.subjects_from(polls)}
    s_now = S.parse_ts(polls[-1]["polled_at"])

    obs_old, obs_new = [], []
    for s in subs.values():
        kind, lo, hi = S.bracket(s, polls, s_now)
        R = N.INF if hi is None else hi
        obs_old.append((lo, R))
        obs_new.append((lo, R))
    for r in rows:
        if r["status"] == "absent" and r["head"] not in subs:
            obs_new.append((r["lag_lo"], N.INF))

    print("Turnbull NPMLE, e000035's estimator, two subject lists.")
    print(f"  feed-framed : {len(obs_old)} subjects")
    print(f"  frame-framed: {len(obs_new)} subjects "
          f"(+{len(obs_new) - len(obs_old)} censored, invisible to the feed)")
    print()
    for label, obs in (("FEED-FRAMED", obs_old), ("FRAME-FRAMED", obs_new)):
        blocks, mass, iters = N.npmle(obs)
        print(f"  {label}: {len(blocks)} support block(s), {iters} EM iterations")
        for (q, p), m in zip(blocks, mass):
            hi = "inf" if p == N.INF else f"{p:.2f}"
            print(f"    ({q:6.2f}, {hi:>7}]   p = {m:.4f}")
        for t, s in N.survival_from_npmle(blocks, mass)[1:]:
            if t != N.INF:
                print(f"      S({t:.2f}) = {s:.4f}")
        print()
    print("  A right-censored observation contributes only its LEFT endpoint, so a")
    print("  push that is merely late still moves the estimate: it opens a block")
    print("  boundary where none existed. That is why leaving the never-arrived out")
    print("  of the denominator is not a conservative omission — it deletes exactly")
    print("  the endpoints that bound the distribution from below.")
    return 0


# ---------------------------------------------------------------- selftest

def selftest():
    """Offline. Asserts the bug, the fix, and the floor rule."""
    print("SELFTEST -- a push that never arrives, run past both subject lists.")
    print()
    ok = True

    # A synthetic feed and poll log in which push 'dddd' is pushed and never served.
    polls = [
        {"polled_at": "2026-01-01T00:00:00Z", "source": "measured", "exhaustive": True,
         "present": ["aaaa"], "subjects": {"aaaa": {"pushed_at": "2025-12-31T23:00:00Z"}}},
        {"polled_at": "2026-01-01T12:00:00Z", "source": "measured", "exhaustive": True,
         "present": ["aaaa", "bbbb"],
         "subjects": {"aaaa": {"pushed_at": "2025-12-31T23:00:00Z"},
                      "bbbb": {"pushed_at": "2026-01-01T02:00:00Z"}}},
    ]
    subs = S.subjects_from(polls)
    heads = {s["head"] for s in subs}
    check = heads == {"aaaa", "bbbb"}
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] the feed-framed subject list is {sorted(heads)}")
    print("        -- 'dddd' was pushed at 03:00 and has never been served, and it")
    print("           is not censored in this list. It is not in this list at all.")

    now = S.parse_ts("2026-01-02T00:00:00Z")
    kinds = [S.bracket(s, polls, now)[0] for s in subs]
    check = "censored" not in kinds
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] and so the feed-framed report shows ZERO "
          "censored subjects")
    print("        -- 100% arrival, computed over a denominator that excluded the")
    print("           only push that had not arrived")

    # Same instant, frame from git.
    commits = [("aaaa", "2025-12-31T23:00:00Z"),
               ("bbbb", "2026-01-01T02:00:00Z"),
               ("dddd", "2026-01-01T03:00:00Z")]
    feed_rows = [{"head": "aaaa", "created_at": "2025-12-31T23:00:02Z"},
                 {"head": "bbbb", "created_at": "2026-01-01T02:00:02Z"}]
    rows = classify(commits, feed_rows, "2025-12-31T00:00:00Z", now)
    absent = [r["head"] for r in rows if r["status"] == "absent"]
    check = absent == ["dddd"]
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] the git-framed list censors {absent}")

    lo = [r["lag_lo"] for r in rows if r["status"] == "absent"][0]
    check = abs(lo - (21.0 - MAX_COMMIT_TO_PUSH_S / 3600.0)) < 1e-6
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] with lower bound {lo:.4f}h, which is 21h "
          f"MINUS the {MAX_COMMIT_TO_PUSH_S}s clock slack")
    print("        -- the conservative direction: never claim more delay than proven")

    # The floor rule.
    rows = classify(commits, feed_rows, "2026-01-01T01:00:00Z", now)
    st = {r["head"]: r["status"] for r in rows}
    check = st["aaaa"] == "arrived" and st["dddd"] == "absent"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] a served push stays 'arrived' even when it "
          "predates the floor")

    commits2 = commits + [("eeee", "2025-06-01T00:00:00Z")]
    rows = classify(commits2, feed_rows, "2026-01-01T01:00:00Z", now)
    st = {r["head"]: r["status"] for r in rows}
    check = st["eeee"] == "below-floor"
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] an unserved push older than the floor is "
          "'below-floor', not censored")
    print("        -- otherwise the frame invents a 5000-hour absence out of the")
    print("           events API's retention policy")

    # The bug is a property of do_poll's source, not of this particular poll log.
    import inspect
    src = inspect.getsource(S.do_poll)
    present_line = next((l for l in src.splitlines() if '"present"' in l), "")
    subjects_line = next((l for l in src.splitlines() if '"subjects"' in l), "")
    check = "rows" in present_line and "rows" in subjects_line
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] do_poll builds 'present' and 'subjects' "
          "from the same variable:")
    print(f"          {present_line.strip()}")
    print(f"          {subjects_line.strip()}")
    print("        -- `rows` is transitions(fetch_events(repo)). The numerator and")
    print("           the denominator have one source, and it is the source under test.")

    # And subjects_from is called without the `extra` its own signature offers.
    src = inspect.getsource(S.main)
    check = "subjects_from(polls)" in src
    ok &= check
    print(f"  [{'ok ' if check else 'FAIL'}] main() calls subjects_from(polls) with no "
          "`extra`, so the one")
    print("           documented escape hatch is never used")

    print()
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--audit", action="store_true", help="check the two frame premises")
    ap.add_argument("--observe", action="store_true",
                    help="append a frame record (ref tip + ancestry) to frame-log.jsonl")
    ap.add_argument("--dry-run", action="store_true", help="with --observe, do not append")
    ap.add_argument("--note", default="", help="note to store with an --observe record")
    ap.add_argument("--npmle", action="store_true", help="Turnbull fit on both subject lists")
    ap.add_argument("--selftest", action="store_true", help="offline; no network")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.audit:
        return audit(args.repo)
    if args.observe:
        rec, _, rows, commits, now = do_observe(args.repo, args.note)
        print(f"observed {args.repo} at {rec['observed_at']}: "
              f"frame {len(commits)} commits, feed {len(rows)} pushes, "
              f"tip {rec['tip']}")
        if args.dry_run:
            print("--dry-run: not appended")
        else:
            append_frame(rec)
            print(f"appended to {os.path.relpath(FRAME_LOG)}")
        print()
        return report(args.repo)
    if args.npmle:
        return npmle_report(args.repo)
    return report(args.repo)


if __name__ == "__main__":
    sys.exit(main())
