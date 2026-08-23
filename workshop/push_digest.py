#!/usr/bin/env python3
"""push_digest — carry the push-transition witness out of the sandbox in 64 hex
characters.

THE PROBLEM THIS IS FOR. workshop/push_chain.py reads GitHub's events API, which
records for every push both `before` and `head`, so the pushed states form a
linked list that can be checked by string equality with no git objects at all
(e000011). It is the strongest of the three GitHub records and it is also the
most perishable: the ceiling is a 300-EVENT COUNT, not a duration, spent by the
writer's own pushes. push_chain therefore offers `--harvest`, and then says the
only honest thing it can say — a harvest committed inside this repository is
worth nothing, because a writer who re-chains the log amends the harvest in the
same commit (e000007's custody flaw). Which left a dead end: a session running
inside the writer's sandbox has nowhere else to put a harvest.

    Except the sandbox does have exactly one channel that leaves it.

Every scheduled wake ends with a report that goes to the operator, outside this
repository and outside this machine, and e000004 already identified that channel
as the log's densest witness — while noting its two weaknesses. It is
low-bandwidth (nobody pastes 300 transitions into a notification), and what it
carries today is `varve head`, a number computed by the suspect's own code over
the suspect's own file, which is a CONSISTENCY constraint and not an
authenticity one.

This tool fixes both, and the second one is the interesting half.

BANDWIDTH: a rolling hash over the transition list, so one line commits to all
of it. The chain is built the way varve builds its own, for the same reason:

    r0    = sha256("varve-push-digest/v1\\n<repo>\\n<branch>\\n")
    r[i]  = sha256(r[i-1] hex + "\\n" + "<created_at>\\t<before>\\t<head>\\n")

That construction is PREFIX-STABLE, which is the property that makes it usable
in a report. A root published when n=21 is still checkable when n=500: the
verifier recomputes the first 21 transitions and gets the same root. Published
roots therefore accumulate into a chain of their own, held by whoever kept the
reports, and each one pins every push that preceded it.

AUTHENTICITY: unlike `varve head`, this root is computed over bytes A THIRD
PARTY SERVED. Nothing in varve/store.py touches it, so it does not inherit
e000004's objection to a verifier that imports the suspect's own hash function
(and the whole construction above is ~15 lines to reimplement from this
docstring, which is the point of writing it out).

The consequence worth stating plainly, because it is the reason this file
exists: a published root is FALSIFIABLE BY A THIRD PARTY, on a clock. Anyone
holding a report can run `--verify` against GitHub's live window and find out
whether the writer's claim matches what GitHub says. That check must happen
while the window still covers the transitions in question — after that the
report is back to being a consistency constraint. So the report stops being
merely internally consistent and becomes externally checkable-for-a-while, and
the operator, who receives the notification as it is sent, is exactly the party
positioned to check it in time.

What this is NOT: it is not signed, so it proves nothing about who computed it;
a dishonest instance can publish a fabricated root and hope nobody checks before
the window rolls. It does not defend the log against its writer. It converts an
unfalsifiable claim into one that can be caught, by someone else, if they look
soon enough. The signed-checkpoint quorum in docs/DESIGN-witnessing.md remains
the actual fix.

Usage (no credentials; the events API is public for a public repo):

    python3 push_digest.py                      # print the digest line
    python3 push_digest.py --harvest ~/h.jsonl  # merge + extend a harvest
    python3 push_digest.py --from-harvest ~/h.jsonl --offline
    python3 push_digest.py --prefix 5           # root as of the first 5
    python3 push_digest.py --verify "varve-push-digest v1 ... root=abc..."

Exit: 0 digest printed / verify MATCH; 1 verify MISMATCH (the record changed);
2 verify INCONCLUSIVE (we hold fewer transitions than the claim covers).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__)) or "."
MAGIC = "varve-push-digest"
VERSION = "v1"


def git(*args):
    out = subprocess.run(("git",) + args, capture_output=True, text=True, cwd=HERE)
    return out.stdout.strip() if out.returncode == 0 else None


def infer_repo():
    url = git("remote", "get-url", "origin") or ""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def fetch_events(repo, token=None):
    """Every event GitHub still serves. The pagination ceiling answers with a
    message object rather than a list; that is the window closing, not an
    error."""
    out, page, capped = [], 1, False
    while page <= 10:
        url = "%s/repos/%s/events?per_page=100&page=%d" % (API, repo, page)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "varve-push-digest",
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
    """PushEvents on `branch`, oldest first. Same shape push_chain.py harvests,
    so the two tools read and write the same JSON-lines files."""
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
    return order(ts)


def order(ts):
    ts.sort(key=lambda t: (t["when"] or "", str(t["event"])))
    return ts


def read_harvest(path, repo, branch):
    """Transitions previously copied out, for coverage past the event ceiling.
    Deduplicated by event id; the file is append-only by construction."""
    seen, out = set(), []
    if not path or not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("repo") != repo or rec.get("branch") != branch:
                continue
            if rec.get("event") in seen:
                continue
            seen.add(rec.get("event"))
            out.append({k: rec.get(k) for k in ("before", "head", "when", "event")})
    return order(out)


def merge(a, b):
    """Union by event id, oldest first. A harvest and a live window overlap;
    neither is authoritative over the other, and identical ids carry identical
    payloads because GitHub minted both."""
    by_id = {}
    for t in list(a) + list(b):
        by_id.setdefault(t["event"], t)
    return order(list(by_id.values()))


def canonical(t):
    """The bytes hashed for one transition. Deliberately trivial: three fields,
    tab-separated, newline-terminated, UTF-8, full 40-hex shas. A stranger
    reimplementing this from the docstring must produce these exact bytes."""
    return ("%s\t%s\t%s\n" % (t["when"], t["before"], t["head"])).encode("utf-8")


def roots(repo, branch, ts):
    """Every prefix root, r[0]..r[n]. r[0] is the empty-list root; r[i] covers
    the first i transitions. Returned in full because prefix-stability is the
    whole feature and a verifier needs to look up an arbitrary one."""
    r = hashlib.sha256(("%s/%s\n%s\n%s\n" % (MAGIC, VERSION, repo, branch))
                       .encode("utf-8")).hexdigest()
    out = [r]
    for t in ts:
        r = hashlib.sha256((r + "\n").encode("utf-8") + canonical(t)).hexdigest()
        out.append(r)
    return out


def digest_line(repo, branch, ts, root):
    return ("%s %s %s %s n=%d first=%s last=%s root=%s"
            % (MAGIC, VERSION, repo, branch, len(ts),
               (ts[0]["before"] if ts else "-")[:12] or "(empty)",
               (ts[-1]["head"] if ts else "-")[:12], root))


CLAIM_RE = re.compile(
    r"%s\s+(?P<ver>v\d+)\s+(?P<repo>\S+)\s+(?P<branch>\S+)\s+n=(?P<n>\d+)\s+"
    r"first=(?P<first>\S+)\s+last=(?P<last>\S+)\s+root=(?P<root>[0-9a-f]{64})" % MAGIC)


def parse_claims(text):
    """Every claim in the text, oldest first. Passing a whole archive of kept
    reports is the supported case, not an edge one: one root says whether the
    record changed, and only a SEQUENCE of roots can say where."""
    out = []
    for m in CLAIM_RE.finditer(text):
        d = m.groupdict()
        d["n"] = int(d["n"])
        out.append(d)
    if not out:
        sys.exit("could not parse a %s claim out of that string" % MAGIC)
    out.sort(key=lambda c: c["n"])
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="owner/name (default: from origin)")
    p.add_argument("--branch", default="main")
    p.add_argument("--harvest", metavar="PATH",
                   help="merge this harvest AND append newly seen transitions "
                        "to it. Keep it outside this repository.")
    p.add_argument("--from-harvest", metavar="PATH",
                   help="read this harvest but never write to it")
    p.add_argument("--offline", action="store_true",
                   help="do not contact GitHub; harvest only")
    p.add_argument("--prefix", type=int, metavar="N",
                   help="print the root covering the first N transitions")
    p.add_argument("--verify", metavar="CLAIM",
                   help="a previously published digest line, or a file "
                        "containing one, to check against what we can see")
    p.add_argument("--token", help="optional; the events API is public")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    claims = []
    if a.verify:
        text = a.verify
        if os.path.exists(text):
            with open(text) as f:
                text = f.read()
        claims = parse_claims(text)
    claim = claims[-1] if claims else None

    repo = a.repo or (claim or {}).get("repo") or infer_repo()
    branch = a.branch if a.branch != "main" or not claim else claim["branch"]
    if not repo:
        sys.exit("could not infer owner/repo; pass --repo owner/name")

    live, capped = ([], False) if a.offline else fetch_events(repo, a.token)
    ts = transitions(live, branch)
    harvest_path = a.harvest or a.from_harvest
    held = read_harvest(harvest_path, repo, branch)
    ts = merge(held, ts)
    if a.harvest:
        known = {t["event"] for t in held}
        new = [t for t in ts if t["event"] not in known]
        if new:
            with open(a.harvest, "a") as f:
                for t in new:
                    f.write(json.dumps({"repo": repo, "branch": branch, **t},
                                       sort_keys=True) + "\n")

    if not ts:
        sys.exit("no push transitions available for %s on %r -- nothing to "
                 "digest. The event window is a count, not a duration." % (repo, branch))

    rs = roots(repo, branch, ts)
    line = digest_line(repo, branch, ts, rs[-1])

    if claims:
        for c in claims:
            if c["ver"] != VERSION:
                sys.exit("a claim is %s, this tool speaks %s" % (c["ver"], VERSION))

        checked, uncovered = [], []
        for c in claims:
            if c["n"] > len(ts):
                uncovered.append(c)
            else:
                checked.append((c, rs[c["n"]] == c["root"], rs[c["n"]]))

        for c, ok, ours in checked:
            print("  %-9s n=%-5d %s" % ("MATCH" if ok else "MISMATCH", c["n"],
                                        c["root"][:24] + ("" if ok else " != " + ours[:24])))
        for c in uncovered:
            print("  %-9s n=%-5d %s" % ("UNCOVERED", c["n"], c["root"][:24]))
        if checked or uncovered:
            print()

        bad = [c for c, ok, _ in checked if not ok]
        good = [c for c, ok, _ in checked if ok]

        if not bad and not uncovered:
            newest = max(good, key=lambda c: c["n"])
            print("MATCH: every published root re-derives from GitHub's record today\n"
                  "(%d claim%s, newest covering %d transitions)."
                  % (len(good), "" if len(good) == 1 else "s", newest["n"]))
            print("\n  we now hold  : %d transitions (%d past the newest claim)"
                  % (len(ts), len(ts) - newest["n"]))
            print("  newest state : %s  (%s)" % (ts[-1]["head"][:12], ts[-1]["when"]))
            if len(ts) > newest["n"]:
                print("\nPrefix-stability held across %d further pushes: roots published\n"
                      "when the record was shorter still verify against it now."
                      % (len(ts) - newest["n"]))
            return 0

        if not bad:
            print("INCONCLUSIVE: %d claim%s cover%s more transitions than we can see "
                  "(%d).\nThe event window is a COUNT, not a duration, and may have "
                  "rolled past\nthem. A harvest kept outside this repository is what "
                  "closes that gap."
                  % (len(uncovered), "" if len(uncovered) == 1 else "s",
                     "s" if len(uncovered) == 1 else "", len(ts)))
            if good:
                print("\nWhat did check out: %d earlier root%s, up to n=%d."
                      % (len(good), "" if len(good) == 1 else "s",
                         max(c["n"] for c in good)))
            return 2

        print("MISMATCH: %d published root%s not re-derive from the record\n"
              "GitHub serves today." % (len(bad), " does" if len(bad) == 1 else "s do"))

        # A root is a hash over a whole prefix, so ONE root can only ever say
        # "changed" or "unchanged" -- never where. A sequence of them brackets it.
        first_bad = min(bad, key=lambda c: c["n"])
        last_good = max(good, key=lambda c: c["n"]) if good else None
        if last_good and last_good["n"] < first_bad["n"]:
            print("\nBracketed by the archive: transitions 1..%d still agree, and\n"
                  "something within 1..%d does not. The divergence is in the range\n"
                  "(%d, %d]." % (last_good["n"], first_bad["n"],
                                 last_good["n"], first_bad["n"]))
        else:
            print("\nOnly one root was checkable, so this says THAT the record changed\n"
                  "and cannot say WHERE: a root hashes a whole prefix. Publishing a\n"
                  "root every session is what turns this into a range.")
        matched_prefix = next((i for i, r in enumerate(rs) if r == first_bad["root"]), None)
        if matched_prefix is not None:
            print("\nBut note: that root matches our prefix of length %d, not %d. The\n"
                  "transitions are intact and the claim's own n is wrong -- a miscopied\n"
                  "line rather than a rewritten record." % (matched_prefix, first_bad["n"]))
            return 1
        print("\nCauses this tool cannot tell apart, in the order they are likely:\n"
              "  - the published line was miscopied or truncated in transit;\n"
              "  - GitHub withdrew or re-served an event differently;\n"
              "  - a force-push replaced history, so a recorded push no longer\n"
              "    names the state GitHub had previously recorded.\n"
              "Only the last is an attack, and ruling the first two out is a\n"
              "human's job, not this tool's.")
        return 1

    if a.prefix is not None:
        if not 0 <= a.prefix <= len(ts):
            sys.exit("--prefix must be between 0 and %d" % len(ts))
        sub = ts[:a.prefix]
        line = digest_line(repo, branch, sub, rs[a.prefix])

    if a.json:
        print(json.dumps({"repo": repo, "branch": branch, "n": len(ts),
                          "root": rs[-1], "line": line, "window_capped": capped,
                          "from_harvest": len(held), "from_live": len(ts) - len(held),
                          "transitions": ts}, indent=1))
        return 0

    print(line)
    print()
    print("  covers   : %d transitions, %d distinct states, %s .. %s"
          % (len(ts), len(ts) + 1, ts[0]["when"], ts[-1]["when"]))
    print("  sources  : %d from a harvest, %d from GitHub's live window%s"
          % (len(held), len(ts) - len(held),
             " (CAPPED -- older events evicted)" if capped else ""))
    print()
    print("Publish that line where you do not control the archive -- a session\n"
          "report, a mailed notification, anywhere off this machine. Whoever holds\n"
          "it can run, while the window still reaches back that far:\n")
    print("    python3 push_digest.py --verify '<the line>'\n")
    print("and find out whether the writer's story matches GitHub's. That check is\n"
          "on a clock: the event window is a count, not a duration.")
    if harvest_path and os.path.abspath(harvest_path).startswith(
            os.path.abspath(os.path.join(HERE, ".."))):
        print("\n  ! The harvest path is INSIDE this repository. A writer who\n"
              "    rewrites the log amends it in the same commit; it witnesses\n"
              "    nothing where it is. The digest line above still travels.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
