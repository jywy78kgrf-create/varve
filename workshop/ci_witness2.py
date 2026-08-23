#!/usr/bin/env python3
"""ci_witness2 — supersedes ci_witness.py, which cries wolf on a stale ref.

WHY THIS FILE EXISTS. workshop/ci_witness.py is anchored by e000006 and e000007
so it is not rewritten; this supersedes it. Running the original in an ordinary
session checkout produced:

    REWRITTEN  run 21  cdc7e1ccd0f2  2026-08-23T05:04:09Z  success
    8 commits GitHub recorded as pushed are NOT in this history.
    This history was rewritten.

on a repository that had not been rewritten at all. The clone's `origin/main`
was 9 commits behind (d0623e2) while the checkout was at cdc7e1c — run 21's own
head_sha. The tool defaults to `--branch origin/main` and asks git only one
question, `is sha an ancestor of branch?`, so every commit newer than the stale
ref answered no and got the loudest verdict the tool owns.

That is the worst failure mode a witness can have. A witness earns its keep by
being believed when it fires, and this one fires on the single most ordinary
condition in git: a ref that has not been fetched. Nobody who saw it cry
rewrite on a healthy repo twice would read the third alarm.

THE FIX is one more question to git. "Not an ancestor" has two causes and they
point in opposite directions:

    is_ancestor(sha, branch)   -> PRESENT   recorded push is in your history
    is_ancestor(branch, sha)   -> BEHIND    recorded push DESCENDS from your
                                            branch tip. Your clone is stale.
                                            A rewrite cannot produce this: a
                                            forger replacing history makes the
                                            record UNRELATED to your tip, not a
                                            descendant of it.
    neither                    -> REWRITTEN histories diverged. The real alarm.

BEHIND is not a pass and not a failure; it is INCONCLUSIVE (exit 2), and the
tool says `git fetch` and stop. Distinguishing "I cannot see" from "I see a
crime" is the whole point — the original collapsed them into the crime.

Everything ci_witness.py claimed about the witness itself still holds and is not
restated here; read its docstring for the mechanism and its honest limits.

Usage (no credentials; the run index is public for a public repo):

    python3 ci_witness2.py
    python3 ci_witness2.py --repo owner/name --workflow verify.yml --json

Exit: 0 all recorded pushes present and numbering unbroken; 1 rewrite or run
gap detected; 2 inconclusive (stale clone / unfetched objects).
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HERE = __file__.rsplit("/", 1)[0] or "."


def git(*args):
    out = subprocess.run(("git",) + args, capture_output=True, text=True, cwd=HERE)
    return out.stdout.strip() if out.returncode == 0 else None


def infer_repo():
    url = git("remote", "get-url", "origin") or ""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def fetch_runs(repo, workflow, token=None):
    """Every workflow run GitHub still lists, oldest first. Public read."""
    runs, page = [], 1
    while True:
        url = "%s/repos/%s/actions/workflows/%s/runs?per_page=100&page=%d" % (
            API, repo, workflow, page)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "varve-ci-witness2",
            **({"Authorization": "Bearer %s" % token} if token else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit("GitHub API %s for %s (workflow %s)" % (e.code, repo, workflow))
        batch = data.get("workflow_runs", [])
        runs.extend(batch)
        if len(runs) >= data.get("total_count", 0) or not batch:
            break
        page += 1
    return sorted(runs, key=lambda r: r.get("run_number", 0))


def is_ancestor(a, b):
    """Is commit `a` an ancestor of commit-ish `b`? Both directions get asked."""
    out = subprocess.run(("git", "merge-base", "--is-ancestor", a, b),
                         capture_output=True, cwd=HERE)
    return out.returncode == 0


def known(sha):
    """Does this clone hold the object at all? A shallow clone is not evidence
    of tampering, and must not be reported as if it were."""
    return git("cat-file", "-e", sha + "^{commit}") is not None


def audit(runs, branch, on_branch):
    rows, gaps = [], []
    expected = None
    for r in runs:
        n = r.get("run_number")
        if expected is not None and n != expected:
            gaps.append((expected, n - 1))
        expected = n + 1
        sha = r.get("head_sha", "")
        if r.get("head_branch") != on_branch:
            verdict = "OTHER-BRANCH"
        elif not known(sha):
            verdict = "UNKNOWN"
        elif is_ancestor(sha, branch):
            verdict = "PRESENT"
        elif is_ancestor(branch, sha):
            # The recorded push descends from our branch tip: we are behind,
            # not forged. This is the branch the original tool never took.
            verdict = "BEHIND"
        else:
            verdict = "REWRITTEN"
        rows.append({"run": n, "sha": sha, "when": r.get("run_started_at"),
                     "conclusion": r.get("conclusion"), "verdict": verdict,
                     "url": r.get("html_url")})
    return rows, gaps


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--workflow", default="verify.yml")
    p.add_argument("--branch", default="origin/main",
                   help="history to check the recorded pushes against")
    p.add_argument("--on-branch", default="main",
                   help="only audit runs GitHub recorded against this branch")
    p.add_argument("--token", help="optional; the run index is public")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    repo = a.repo or infer_repo()
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")

    runs = fetch_runs(repo, a.workflow, a.token)
    if not runs:
        sys.exit("GitHub lists no runs of %s -- nothing to witness with" % a.workflow)
    rows, gaps = audit(runs, a.branch, a.on_branch)

    bad = [r for r in rows if r["verdict"] == "REWRITTEN"]
    behind = [r for r in rows if r["verdict"] == "BEHIND"]
    unknown = [r for r in rows if r["verdict"] == "UNKNOWN"]
    checked = [r for r in rows if r["verdict"] != "OTHER-BRANCH"]

    if a.json:
        print(json.dumps({"repo": repo, "workflow": a.workflow, "branch": a.branch,
                          "runs": rows, "gaps": gaps, "rewritten": len(bad),
                          "behind": len(behind), "unknown": len(unknown)}, indent=1))
        return 1 if (bad or gaps) else (2 if (behind or unknown) else 0)

    print("%s -- %d runs of %s recorded by GitHub, checked against %s\n"
          % (repo, len(rows), a.workflow, a.branch))
    for r in rows:
        print("  %-12s run %-4s %s  %s  %s"
              % (r["verdict"], r["run"], r["sha"][:12], r["when"], r["conclusion"]))
        if r["verdict"] == "REWRITTEN":
            print("               %s" % r["url"])
    print()

    for lo, hi in gaps:
        print("! RUN GAP: run_number %d..%d missing -- runs were deleted" % (lo, hi))

    if bad:
        print("%d commits GitHub recorded as pushed are NOT in this history and are\n"
              "not descendants of it. The histories diverged: someone force-pushed.\n"
              "`varve verify` cannot see this and will report the chain intact." % len(bad))
        return 1
    if behind:
        newest = behind[-1]
        print("INCONCLUSIVE -- nothing was checked past your ref.\n"
              "%d recorded pushes DESCEND from %s, so this clone is behind, not\n"
              "forged (a rewrite makes records unrelated to your tip, not children\n"
              "of it). Newest unseen: run %s %s.\n"
              "  git fetch origin %s   then run this again."
              % (len(behind), a.branch, newest["run"], newest["sha"][:12], a.on_branch))
        return 2
    if unknown:
        print("INCONCLUSIVE -- %d recorded commits are not objects in this clone.\n"
              "Expected from a shallow or partial clone; from a full clone it means\n"
              "what REWRITTEN means. Re-clone with full history before believing it."
              % len(unknown))
        return 2
    if not checked:
        print("No recorded run targeted branch %r, so NOTHING was checked.\n"
              "This is not a pass." % a.on_branch)
        return 1
    if gaps:
        return 1
    print("All %d recorded pushes to %s are present in this history, run numbering\n"
          "unbroken. Coverage begins at run %s (%s) -- nothing before that is\n"
          "witnessed here." % (len(checked), a.on_branch, checked[0]["run"],
                               checked[0]["when"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
