#!/usr/bin/env python3
"""Find confident sentences with nothing underneath them.

Every defect the three external reviews of this repository found lived in a
COMMENT, never in code. verify.yml boasted that a rewrite "goes red here for
anyone to see" (it goes green). pages.yml "refuses to publish a broken chain"
(it publishes a re-chained forgery with a passing badge). settings.json
promised "no credential-touching or destructive command is listed" while
allowing `git push --force`. The code was right every time; the prose
describing it was describing a world nobody had tested.

That is not an accident of this repo. A comment is where an author states what
they believe the code does, and belief is the one thing no test covers. Where
code and prose are written by the same confident author in the same sitting,
the two layers fail together.

So: extract every modal claim — guarantees, prevents, detects, refuses, never,
cannot, impossible — and ask what it resolves to. A claim with a test name, a
path, a command, an entry id, or an explicit limit in the same paragraph is
anchored. A claim with nothing beside it is a promise, and promises are where
the rot was.

    python tools/claimcheck.py                # this repo
    python tools/claimcheck.py path [path..]  # anywhere
    python tools/claimcheck.py --all          # show the anchored ones too

WHAT THIS IS NOT, said plainly, because a tool that overclaims about
overclaiming would be an unusually stupid thing to ship. It is a regex over
prose. It cannot tell whether a cited test actually tests the claim, it does
not parse English, it will flag a rhetorical "never" and miss a lie stated
calmly. Treat the output as a WORKLIST of sentences deserving a human's eye —
not a verdict, not a gate, never a badge. Exit status is 0 whether or not it
finds anything, for exactly that reason.
"""

import os
import re
import sys

CLAIM = re.compile(
    r"\b(guarantee[sd]?|prevent[s]?|ensure[sd]?|detect[s]?|refuse[sd]?|"
    r"protect[s]?|block[s]?|prov(?:e[sd]?|en)|impossible|cannot|"
    r"never|always|no way to)\b", re.I)

# Something a reader can go and check. Deliberately the same word the
# constitution uses, and deliberately a WEAKER test than the constitution's:
# nothing here verifies that the cited thing supports the claim beside it.
ANCHOR = re.compile(
    r"(test_\w+|\b[\w.-]+[/\\][\w./\\-]+\.(?:py|md|yml|yaml|json|txt)\b|`[^`]+`|"
    r"\be\d{6}\b|https?://|\b[0-9a-f]{12,}\b|\bsee\s+[\w`.]|\breview\b)", re.I)

# A sentence that says what it does NOT do is doing the thing this tool exists
# to encourage, so it counts as backing rather than as a claim to chase.
LIMIT = re.compile(
    r"\b(only|not guaranteed|does not|do not|cannot detect|partial|but not|"
    r"except|limit|honest|overclaim|not a substitute|weaker|is not|"
    r"false|wrong|instead)\b", re.I)

COMMENT = re.compile(r"^\s*(#|//|--)\s?|^\s*\*\s")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "site", "node_modules", ".venv"}
EXTS = {".md", ".py", ".yml", ".yaml", ".json", ".txt", ".sh", ".toml", ".cfg"}
TRIPLE = ('"""', "'''")


def _prose_lines(path, ext):
    """The lines of a file that make claims in words: all of a markdown file,
    and only comments and docstrings in code."""
    try:
        raw = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return
    in_doc, fence = False, None
    for i, line in enumerate(raw, 1):
        if ext in (".md", ".txt"):
            yield i, line
            continue
        if ext == ".py":
            # A ONE-LINE docstring carries two triple quotes, so an odd-count
            # test toggled nothing and skipped the line — and a one-liner is the
            # commonest docstring there is. Any triple quote means prose on this
            # line; only an ODD count opens or closes a multi-line block.
            touches = any(q in line for q in TRIPLE)
            for q in TRIPLE:
                if line.count(q) % 2 == 1:
                    if in_doc and fence == q:
                        in_doc, fence = False, None
                    elif not in_doc:
                        in_doc, fence = True, q
                    break
            if touches or in_doc or COMMENT.match(line):
                yield i, re.sub(r"^\s*#\s?", "", line)
            continue
        if COMMENT.match(line) or (ext == ".json" and "_comment" in line):
            yield i, re.sub(r"^\s*(#|//)\s?", "", line)


def prose_blocks(path):
    """Yield (first_lineno, paragraph_text).

    Blocks, not lines. The first version of this scanned line by line and
    flagged 96 claims here — mostly sentence fragments, many of them anchored
    by a citation sitting one line below. A checker that fires ninety-six times
    is the crying-wolf failure e000010 recorded: the second time a reader sees
    a false alarm the alarm stops meaning anything. A claim and its evidence
    live in the same paragraph, so the paragraph is the unit.
    """
    ext = os.path.splitext(path)[1]
    block, start, prev = [], None, None
    for i, line in _prose_lines(path, ext):
        gap = prev is not None and i > prev + 1
        if not line.strip() or gap:
            if block:
                yield start, " ".join(block)
            block, start = [], None
        if line.strip():
            if start is None:
                start = i
            block.append(line.strip())
        prev = i
    if block:
        yield start, " ".join(block)


def sentences(text):
    for part in re.split(r"(?<=[.!?])\s+", text.strip()):
        part = part.strip(" #*-/`|")
        if part:
            yield part


def scan(paths, show_all=False):
    flagged, anchored = [], 0
    for base in paths:
        walk = ([(os.path.dirname(base) or ".", [], [os.path.basename(base)])]
                if os.path.isfile(base) else os.walk(base))
        for root, dirs, files in walk:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if os.path.splitext(name)[1] not in EXTS:
                    continue
                path = os.path.join(root, name)
                for lineno, block in prose_blocks(path):
                    claims = [s for s in sentences(block) if CLAIM.search(s)]
                    if not claims:
                        continue
                    # Evidence anywhere in the paragraph counts: a claim whose
                    # proof is the next sentence is anchored, not naked.
                    if ANCHOR.search(block) or LIMIT.search(block):
                        anchored += len(claims)
                        if show_all:
                            flagged.append(("ok", path, lineno, claims[0]))
                    else:
                        flagged.append(("CLAIM", path, lineno, claims[0]))
    return flagged, anchored


def main(argv):
    show_all = "--all" in argv
    paths = [a for a in argv if not a.startswith("--")] or ["."]
    flagged, anchored = scan(paths, show_all)
    unbacked = [f for f in flagged if f[0] == "CLAIM"]
    for tag, path, lineno, sent in flagged:
        print("%-5s %s:%d\n      %s" % (tag, path, lineno, sent[:170]))
    print("\n%d claim(s) with nothing checkable in the same paragraph; "
          "%d anchored." % (len(unbacked), anchored))
    print("A worklist, not a verdict. This is a regex over prose: it cannot tell\n"
          "whether a cited test actually tests the claim. Read them yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
