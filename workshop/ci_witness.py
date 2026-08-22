#!/usr/bin/env python3
"""ci_witness — check this repository's git history against GitHub's record of it.

workshop/witness_replay.py replays head observations that someone kept. Its
weakness is custody: the only observations file that exists lives at
workshop/observations.txt, committed inside the very repository it is meant to
witness. A writer who re-chains the log amends that file in the same commit and
the replay reports MATCH on every line. An archive the suspect can edit is not
an archive.

This tool uses a witness that already exists and that the writer cannot edit:
**GitHub's workflow-run index.** Every push to main since founding has started a
`verify` run, and GitHub retains, per run, a monotonically increasing
`run_number`, the `head_sha` that was pushed, and its own timestamp. That record
lives on GitHub's infrastructure, not in the repository tree, so rewriting
notebook/log/*.json does not touch it.

It witnesses at the GIT layer, which is strictly stronger than witnessing a
printed head:

    a commit SHA is a hash over the whole tree, including every log entry,
    computed by git rather than by varve/store.py

so a verifier using it never has to trust the suspect's own hash function --
the objection e000004 raised against importing store.py. Two checks:

    REWRITTEN   GitHub recorded a push of commit X; commit X is not an
                ancestor of this branch. Someone force-pushed a different
                history. `varve verify` passes clean through exactly this.
    RUN GAP     run_number skips. Workflow runs were deleted -- the one way
                to attack this witness, and it leaves a hole you can count.

Usage (no credentials needed; the run index is public for a public repo):

    python3 ci_witness.py                        # infers owner/repo from origin
    python3 ci_witness.py --repo owner/name --workflow verify.yml
    python3 ci_witness.py --json                 # machine-readable

Honest limits, in the order they matter:

  * GitHub is ONE party, and it is the operator's account. A repo admin can
    delete workflow runs (`actions: write`). This is not adversary-proof
    witnessing; it is a second, independently-stored record that a log rewrite
    alone does not reach. That is a real step past "the writer is the only
    auditor", and it is not the signed-checkpoint quorum in
    docs/DESIGN-witnessing.md, which remains the actual fix.
  * Coverage starts at the oldest run still listed, not at the founding entry.
  * Run LOG TEXT (which contains the head that `varve verify` printed) is
    retained 90 days, and a public repository cannot raise that ceiling. The
    run INDEX this tool reads may outlive the log text -- untested here.
    Either way, harvesting matters: an external record you copied somewhere
    else before the window closed is worth more than one you assumed was
    permanent.
  * A stranger should run this from a fresh `git clone`, not from a working
    copy the writer handed them.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def git(*args):
    """Run git in the repository containing this file; return stdout stripped."""
    out = subprocess.run(
        ("git",) + args, capture_output=True, text=True,
        cwd=__file__.rsplit("/", 1)[0] or ".",
    )
    return out.stdout.strip() if out.returncode == 0 else None


def infer_repo():
    """owner/name from the origin remote, https or ssh form."""
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
            "User-Agent": "varve-ci-witness",
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


def is_ancestor(sha, branch):
    """Is this commit still in the branch's history? The whole check."""
    out = subprocess.run(
        ("git", "merge-base", "--is-ancestor", sha, branch),
        capture_output=True, cwd=__file__.rsplit("/", 1)[0] or ".",
    )
    return out.returncode == 0


def known(sha):
    """Does this clone have the object at all? Distinguishes 'rewritten away'
    from 'never fetched' -- a shallow clone is not evidence of tampering."""
    return git("cat-file", "-e", sha + "^{commit}") is not None


def audit(runs, branch, on_branch):
    """`branch` is the history to check against; `on_branch` selects which
    recorded runs are in scope. They are different things -- `branch` may be a
    bare SHA -- and conflating them silently skips every row."""
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
        else:
            verdict = "REWRITTEN"
        rows.append({
            "run": n, "sha": sha, "when": r.get("run_started_at"),
            "conclusion": r.get("conclusion"), "verdict": verdict,
            "url": r.get("html_url"),
        })
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
    unknown = [r for r in rows if r["verdict"] == "UNKNOWN"]
    checked = [r for r in rows if r["verdict"] != "OTHER-BRANCH"]

    if a.json:
        print(json.dumps({"repo": repo, "workflow": a.workflow,
                          "branch": a.branch, "runs": rows, "gaps": gaps,
                          "rewritten": len(bad)}, indent=1))
        return 1 if bad or gaps else 0

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
    if unknown:
        print("? %d recorded commits are not objects in this clone. If this is a "
              "shallow or partial clone that is expected; from a full clone it "
              "means the same thing REWRITTEN does." % len(unknown))
    if bad:
        print("\n%d commits GitHub recorded as pushed are NOT in this history.\n"
              "This history was rewritten. `varve verify` cannot see this and "
              "will report the chain intact." % len(bad))
        return 1
    if not checked:
        print("No recorded run targeted branch %r, so NOTHING was checked. "
              "This is not a pass." % a.on_branch)
        return 1
    if not gaps:
        print("All %d recorded pushes to %s are present in this history, run "
              "numbering unbroken.\nCoverage begins at run %s (%s) -- nothing "
              "before that is witnessed here."
              % (len(checked), a.on_branch, checked[0]["run"], checked[0]["when"]))
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
