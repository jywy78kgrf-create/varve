#!/usr/bin/env python3
"""push_chain — witness this repository at the PUSH TRANSITION layer.

workshop/ci_witness.py (and its successor ci_witness2.py) read GitHub's
workflow-run index, which records, per run, a `head_sha`. That is a record of a
SET of states: which trees were pushed. This tool reads a different GitHub
subsystem, the repository events API, which records for every PushEvent both
`before` and `head`. That is a record of TRANSITIONS: which state replaced
which. The difference is not cosmetic.

    run index      {H1, H2, H3, ...}          a set of heads
    events         H0->H1, H1->H2, H2->H3     a linked list of transitions

Why the linked list is stronger. To decide whether a recorded head belongs to
your history, ci_witness must ask git — `is_ancestor(sha, branch)` — which
needs the commit OBJECTS to still exist. After a force-push, the orphaned
commits survive only until GitHub garbage-collects them; once they are gone,
that check can no longer be made. The transition record needs no objects at
all. You verify it by string equality:

    head(push_i) == before(push_i+1)   for every consecutive pair

A fast-forward push satisfies this by construction. A force-push does not: it
records a `before` that is not the previous `head`, and that discontinuity is
metadata in GitHub's event store, not a git object, so collecting the orphaned
commits does not remove it. Detection and recovery have different fates —
what expires is the ability to read WHAT was rewritten, not the evidence THAT
something was.

AND THE CATCH, which is the reason this file also has a --harvest mode. This is
the shortest-lived of the three GitHub records, by a wide margin. Measured
against this repository on 2026-08-23, not quoted from documentation:

    per_page=100&page=1  ->  events
    per_page=100&page=2  ->  0
    per_page=100&page=3  ->  0
    per_page=100&page=4  ->  {"message": "In order to keep the API fast for
                              everyone, pagination is limited for this resource."}

So the ceiling is 300 events, and it is a count, not a duration: a busy day
evicts more history than a quiet month. This repository burned 22 events in its
first 29 hours. The strongest witness available has the shortest memory of the
three, and it expires by ACTIVITY — the log gets rewritten-proof exactly in
proportion to how little it is written to. That inverts the usual comfort that
a well-tended log is a well-witnessed one.

Which is why the only thing to do with this record is COPY IT OUT. `--harvest
PATH` appends the observed transitions to a JSON-lines file, deduplicated, so
repeated runs accumulate coverage past the 300-event window.

    A harvest file committed inside this repository is worth nothing as
    evidence.

That is not a caveat, it is the entire point: e000007 named this flaw in
workshop/observations.txt, which lives inside the repository it is meant to
witness, so a writer who re-chains the log amends the witness file in the same
commit. Write the harvest somewhere the writer of this log does not control, or
do not bother writing it.

Usage (no credentials; the events API is public for a public repo):

    python3 push_chain.py                          # infer repo from origin
    python3 push_chain.py --repo owner/name --branch main
    python3 push_chain.py --harvest ~/varve-harvest.jsonl
    python3 push_chain.py --no-git                 # linkage only, no clone needed
    python3 push_chain.py --json

Exit: 0 chain unbroken; 1 divergence (force-push evidence); 2 inconclusive
(a gap the event window cannot resolve).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HERE = __file__.rsplit("/", 1)[0] or "."
ZERO = "0" * 40


def git(*args):
    out = subprocess.run(("git",) + args, capture_output=True, text=True, cwd=HERE)
    return out.stdout.strip() if out.returncode == 0 else None


def infer_repo():
    url = git("remote", "get-url", "origin") or ""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def fetch_events(repo, token=None):
    """Every event GitHub still serves. Stops at the resource's pagination
    ceiling, which answers with a message object rather than a list -- that is
    the window closing, not an error, and it is reported as coverage."""
    out, page, capped = [], 1, False
    while page <= 10:
        url = "%s/repos/%s/events?per_page=100&page=%d" % (API, repo, page)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "varve-push-chain",
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


def transitions(events, branch):
    """PushEvents on `branch`, oldest first, as (before, head, when, id)."""
    ref = "refs/heads/%s" % branch
    ts = []
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        pay = e.get("payload") or {}
        if pay.get("ref") != ref:
            continue
        ts.append({"before": pay.get("before") or "", "head": pay.get("head") or "",
                   "when": e.get("created_at"), "event": e.get("id")})
    ts.sort(key=lambda t: (t["when"], t["event"]))
    return ts


def load_harvest(path, repo, branch):
    seen = {}
    if path and os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("repo") == repo and rec.get("branch") == branch:
                    seen[rec.get("event")] = rec
    return seen


def save_harvest(path, repo, branch, ts, seen):
    """Append-only, deduplicated by event id. Never rewrites existing lines."""
    new = [t for t in ts if t["event"] not in seen]
    if new:
        with open(path, "a") as f:
            for t in new:
                f.write(json.dumps({"repo": repo, "branch": branch, **t},
                                   sort_keys=True) + "\n")
    return len(new)


def link_check(ts, use_git):
    """Verify head(i) == before(i+1). A break is either a rewrite or an evicted
    event; ask git which, when git is available and still holds the objects."""
    breaks = []
    for a, b in zip(ts, ts[1:]):
        if a["head"] == b["before"]:
            continue
        cause = "UNRESOLVED"
        if use_git:
            if not (git("cat-file", "-e", a["head"] + "^{commit}") is not None and
                    git("cat-file", "-e", b["before"] + "^{commit}") is not None):
                cause = "OBJECT-MISSING"
            elif subprocess.run(("git", "merge-base", "--is-ancestor",
                                 a["head"], b["before"]), capture_output=True,
                                cwd=HERE).returncode == 0:
                # a fast-forward happened between them: an event was evicted,
                # nothing diverged.
                cause = "EVENT-GAP"
            else:
                cause = "DIVERGED"
        breaks.append({"from": a, "to": b, "cause": cause})
    return breaks


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--branch", default="main")
    p.add_argument("--harvest", metavar="PATH",
                   help="append transitions to a JSON-lines file. KEEP IT "
                        "OUTSIDE this repository or it witnesses nothing.")
    p.add_argument("--no-git", action="store_true",
                   help="linkage only; needs no clone and no commit objects")
    p.add_argument("--token", help="optional; the events API is public")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")
    use_git = not a.no_git and git("rev-parse", "--git-dir") is not None

    events, capped = fetch_events(repo, a.token)
    ts = transitions(events, a.branch)
    if not ts:
        sys.exit("no PushEvents on %r in the served window -- nothing to check "
                 "with. The window is a count, not a duration; it may simply "
                 "have rolled past." % a.branch)

    breaks = link_check(ts, use_git)
    harvested = total_harvest = None
    if a.harvest:
        seen = load_harvest(a.harvest, repo, a.branch)
        harvested = save_harvest(a.harvest, repo, a.branch, ts, seen)
        total_harvest = len(seen) + harvested

    diverged = [b for b in breaks if b["cause"] == "DIVERGED"]
    unresolved = [b for b in breaks if b["cause"] in ("UNRESOLVED", "OBJECT-MISSING")]
    code = 1 if diverged else (2 if unresolved else 0)

    if a.json:
        print(json.dumps({"repo": repo, "branch": a.branch, "events_served": len(events),
                          "window_capped": capped, "transitions": ts,
                          "breaks": breaks, "harvested_new": harvested,
                          "harvest_total": total_harvest}, indent=1))
        return code

    print("%s -- %d push transitions on %s, from %d events GitHub still serves%s\n"
          % (repo, len(ts), a.branch, len(events),
             " (window CAPPED -- older events evicted)" if capped else ""))
    print("  oldest state witnessed : %s  (as `before` of %s)"
          % (ts[0]["before"][:12] or "(empty repo)", ts[0]["when"]))
    print("  newest state witnessed : %s  (as `head` of %s)"
          % (ts[-1]["head"][:12], ts[-1]["when"]))
    print("  %d transitions witness %d distinct states, using no git objects."
          % (len(ts), len(ts) + 1))
    print()

    for b in breaks:
        print("! BREAK [%s] %s -> recorded next `before` %s (%s)"
              % (b["cause"], b["from"]["head"][:12], b["to"]["before"][:12],
                 b["to"]["when"]))
    if diverged:
        print("\n%d transition(s) DIVERGED: GitHub recorded a push whose parent state\n"
              "is not the state it previously recorded. That is a force-push, and it\n"
              "is visible here without reading a single commit object." % len(diverged))
    elif unresolved:
        print("\nINCONCLUSIVE: %d break(s) could not be classified. Either an event\n"
              "was evicted by the 300-event ceiling (harmless) or history diverged\n"
              "and the objects are already collected (not harmless). A harvest kept\n"
              "outside this repository is what closes that gap." % len(unresolved))
    else:
        print("Push chain UNBROKEN: every recorded push names the previous recorded\n"
              "push as its parent state, across the whole served window.")

    if a.harvest:
        print("\nharvest: +%d new, %d total in %s" % (harvested, total_harvest, a.harvest))
        inside = os.path.abspath(a.harvest).startswith(os.path.abspath(HERE + "/.."))
        if inside:
            print("  ! That path is INSIDE this repository. A writer who rewrites the\n"
                  "    log amends this file in the same commit. It witnesses nothing\n"
                  "    where it is. Copy it somewhere else.")
    if capped:
        print("\nCoverage is bounded by an event COUNT, not a date. Nothing before\n"
              "%s is witnessed by this record any more." % ts[0]["when"])
    return code


if __name__ == "__main__":
    sys.exit(main())
