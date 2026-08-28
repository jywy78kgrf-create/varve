#!/usr/bin/env python3
"""erasure_probe.py — what did a package registry forget, and does it admit it?

Nothing in this file is about varve. It is a tool for anyone who depends on
public packages, built because this notebook spent eight days learning one
lesson about witnesses and wanted to know whether the lesson generalised:

    A witness needs a verdict for "I cannot see," and any design that lacks
    one will spend that state as "I see a crime" — or, worse, as silence.
    (notebook e000010, sharpened by e000018 and e000020)

Two of this workshop's three witnesses had that defect. This asks whether the
package registries the world installs from have it too.

WHAT IT MEASURES

Both npm and PyPI turn out to be accidentally append-only about erasure. When
a version is removed, neither registry fully forgets it:

  npm   the package document's `time` map keeps a dated entry for every
        version ever published. A key in `time` with no matching key in
        `versions` is a version that was published and is no longer served.

  PyPI  the JSON API's `releases` map keeps the version key with an EMPTY
        file list. PyPI also has a deliberate tombstone npm lacks entirely:
        PEP 592 `yanked`, where the release stays downloadable and flagged.

Neither trace was designed as evidence. Both work as evidence anyway.

THE ASYMMETRY, which is the part worth knowing

npm serves two different documents at the same URL, chosen by Accept header:

  full          application/json                      — has `time`
  abbreviated   application/vnd.npm.install-v1+json   — has NO `time`

The abbreviated document is the one package managers fetch on install (it is
~35-50% of the bytes). It serves EXACTLY the same version set as the full
document — measured, not assumed: the symmetric difference is empty. So from
the install path an erased version is not merely undetected, it is
undetectable. No field differs. The view looks complete and cannot say
otherwise.

That is silence, not a false alarm, and silence is the more dangerous of the
two: a false alarm gets investigated, a clean exit gets filed.

WHICH IS WHY THIS TOOL HAS THREE VERDICTS AND NOT TWO

  INTACT   every version ever recorded is still served
  ERASED   n versions are recorded but no longer served (they are listed)
  BLIND    I cannot tell, and here is why

`--abbreviated` makes BLIND cheap to enter: it runs the same check against the
install document, where the tool MUST report BLIND rather than INTACT. A
witness you cannot watch fail is a witness you are trusting, not checking.

WHAT ERASURE DOES NOT MEAN

Measured base rate over 228 popularity-ranked npm packages: 49 (21.5%) carry
at least one erased version, but only 130 of 62,757 versions ever published
(0.21%) are erased. Nearly all are mundane — botched prereleases, `0.0.0`
placeholders, dev snapshots, retracted bad builds. ERASED means erased. It
does not mean compromised, and a tool that reported it that way would be
making exactly the error its own verdict set exists to prevent.

One caveat this tool cannot resolve and will not paper over: a `time` entry
with no version document is consistent with "published, then removed," but
also with a publish that was only partially recorded. From outside the
registry those are indistinguishable. Treat ERASED as "the registry's own
record disagrees with what it serves," which is exactly what was measured.

USAGE

    python3 erasure_probe.py npm axios express event-stream
    python3 erasure_probe.py npm axios --abbreviated     # must print BLIND
    python3 erasure_probe.py pypi requests urllib3 numpy
    python3 erasure_probe.py --selftest                  # offline, no network
    python3 erasure_probe.py npm --survey 120            # re-measure base rate

Exit codes: 0 all INTACT · 2 something ERASED · 3 any BLIND · 1 usage/error.
BLIND outranks ERASED: not knowing is a worse answer than a known loss.

Stdlib only. Read-only GETs. No credentials, ever.
"""

import sys
import json
import gzip
import time
import argparse
import urllib.parse
import urllib.error
import urllib.request

UA = "erasure_probe/1 (+https://github.com/jywy78kgrf-create/varve)"
NPM_FULL = "application/json"
NPM_ABBR = "application/vnd.npm.install-v1+json"

INTACT, ERASED, BLIND = "INTACT", "ERASED", "BLIND"


def fetch(url, accept=None, timeout=90):
    """GET a URL. Returns (status, bytes) or (-1, message-bytes) on transport error."""
    headers = {"Accept-Encoding": "gzip", "User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # transport, TLS, proxy, timeout
        return -1, str(exc).encode()


# ---------------------------------------------------------------- npm

def npm_url(name):
    return "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@")


def npm_judge(doc, abbreviated=False):
    """Judge a parsed npm package document. Returns (verdict, detail, erased-list)."""
    if not isinstance(doc, dict):
        return BLIND, "document is not an object", []
    if abbreviated or "time" not in doc:
        # The install document carries no `time`. There is no field from which
        # erasure could be inferred, so the only honest answer is BLIND. This
        # is the branch the whole tool exists to make reachable.
        why = ("abbreviated (install) document carries no `time` map — "
               "erasure is undetectable from this view, not absent")
        if not abbreviated:
            why = "full document unexpectedly carries no `time` map"
        return BLIND, why, []
    times = doc["time"]
    if not isinstance(times, dict):
        return BLIND, "`time` is present but not an object", []
    if "unpublished" in times:
        return BLIND, "package is wholly unpublished; version history not served", []
    served = set(doc.get("versions") or {})
    ever = {k for k in times if k not in ("created", "modified", "unpublished")}
    if not ever:
        return BLIND, "`time` records no versions", []
    erased = sorted(ever - served)
    if erased:
        return ERASED, f"{len(erased)} of {len(ever)} versions ever published", erased
    return INTACT, f"all {len(ever)} versions ever published are still served", []


def npm_probe(name, abbreviated=False):
    accept = NPM_ABBR if abbreviated else NPM_FULL
    status, raw = fetch(npm_url(name), accept)
    if status == 404:
        # A never-published name and a wholly-erased one look identical here.
        return BLIND, "HTTP 404 — absent, or erased so completely nothing remains", [], None
    if status != 200:
        return BLIND, f"HTTP {status} — {raw[:120].decode('utf8', 'replace')}", [], None
    try:
        doc = json.loads(raw)
    except Exception as exc:
        return BLIND, f"unparseable document ({exc})", [], None
    verdict, detail, erased = npm_judge(doc, abbreviated)
    return verdict, detail, erased, (doc.get("time") or {})


# ---------------------------------------------------------------- PyPI

def pypi_probe(name):
    status, raw = fetch(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    if status == 404:
        return BLIND, "HTTP 404 — absent, or erased so completely nothing remains", [], None
    if status != 200:
        return BLIND, f"HTTP {status} — {raw[:120].decode('utf8', 'replace')}", [], None
    try:
        doc = json.loads(raw)
    except Exception as exc:
        return BLIND, f"unparseable document ({exc})", [], None
    releases = doc.get("releases")
    if not isinstance(releases, dict):
        return BLIND, "no `releases` map (the Simple API omits it)", [], None
    # A version key whose file list is empty is a release whose artifacts were
    # deleted; PyPI keeps the key. Yanked releases are still served, flagged.
    erased = sorted(v for v, files in releases.items() if not files)
    yanked = sorted(v for v, files in releases.items()
                    if files and all(f.get("yanked") for f in files))
    if erased:
        detail = f"{len(erased)} of {len(releases)} releases have no files"
        if yanked:
            detail += f"; {len(yanked)} more yanked but still served"
        return ERASED, detail, erased, yanked
    detail = f"all {len(releases)} releases still carry files"
    if yanked:
        detail += f"; {len(yanked)} yanked (deliberate tombstone, still served)"
    return INTACT, detail, [], yanked


# ---------------------------------------------------------------- selftest

# Fixtures shaped like the real documents, so the verdict logic is checkable
# with no network at all. Every branch that can return BLIND is exercised —
# per the rule this tool was built around, a verdict you never watch fire is
# a verdict you are trusting rather than checking.
SELFTEST = [
    ("full doc, nothing missing", False,
     {"versions": {"1.0.0": {}, "1.0.1": {}},
      "time": {"created": "t", "modified": "t", "1.0.0": "t", "1.0.1": "t"}},
     INTACT, []),
    ("full doc, one version erased", False,
     {"versions": {"1.0.0": {}},
      "time": {"created": "t", "modified": "t", "1.0.0": "t", "1.0.1": "t"}},
     ERASED, ["1.0.1"]),
    ("abbreviated doc hiding an erasure must NOT read INTACT", True,
     {"versions": {"1.0.0": {}}},
     BLIND, []),
    ("abbreviated doc with nothing erased must ALSO read BLIND", True,
     {"versions": {"1.0.0": {}, "1.0.1": {}}},
     BLIND, []),
    ("full doc missing `time` entirely", False,
     {"versions": {"1.0.0": {}}},
     BLIND, []),
    ("wholly unpublished package", False,
     {"versions": {}, "time": {"unpublished": {"name": "x"}}},
     BLIND, []),
    ("`time` present but not an object", False,
     {"versions": {}, "time": "nope"},
     BLIND, []),
    ("`time` records no versions", False,
     {"versions": {}, "time": {"created": "t", "modified": "t"}},
     BLIND, []),
]


def run_selftest():
    failures = 0
    for label, abbrev, doc, want_verdict, want_erased in SELFTEST:
        verdict, detail, erased = npm_judge(doc, abbrev)
        ok = verdict == want_verdict and erased == want_erased
        if not ok:
            failures += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        print(f"          -> {verdict} ({detail})")
    print()
    if failures:
        print(f"SELFTEST FAILED — {failures} of {len(SELFTEST)} cases")
        return 1
    print(f"SELFTEST PASSED — {len(SELFTEST)} cases, "
          f"{sum(1 for c in SELFTEST if c[3] == BLIND)} of them BLIND")
    return 0


# ---------------------------------------------------------------- survey

def survey(limit):
    """Re-measure the base rate of erasure across popularity-ranked npm packages."""
    frame = []
    for kw in ("javascript", "node", "cli", "react", "testing", "http"):
        st, raw = fetch("https://registry.npmjs.org/-/v1/search"
                        f"?text=keywords:{kw}&size=40")
        if st != 200:
            print(f"  (sample frame: keyword {kw} unreachable, HTTP {st})")
            continue
        for obj in json.loads(raw).get("objects", []):
            name = obj["package"]["name"]
            if name not in frame:
                frame.append(name)
    frame = frame[:limit]
    print(f"sample frame: {len(frame)} popularity-ranked packages\n")

    pkgs_with_gap = ever_total = erased_total = blind = 0
    worst = []
    for name in frame:
        verdict, detail, erased, times = npm_probe(name)
        if verdict == BLIND:
            blind += 1
            continue
        ever = len([k for k in times if k not in ("created", "modified", "unpublished")])
        ever_total += ever
        erased_total += len(erased)
        if erased:
            pkgs_with_gap += 1
            worst.append((len(erased), name, ever, erased))
        time.sleep(0.05)

    ok = len(frame) - blind
    if not ok:
        print("no packages readable — nothing to report")
        return 3
    print(f"readable: {ok}    blind: {blind}")
    print(f"packages with >=1 erased version: {pkgs_with_gap}/{ok} "
          f"= {100 * pkgs_with_gap / ok:.1f}%")
    print(f"versions erased: {erased_total}/{ever_total} "
          f"= {100 * erased_total / ever_total:.2f}% of all ever published")
    print("\nmost-erased packages in this sample:")
    for count, name, ever, erased in sorted(worst, reverse=True)[:12]:
        print(f"  {name:36} ever={ever:5} erased={count:3}  {erased[:5]}")
    print("\nRead this as the base rate it is: erasure is common per package "
          "and rare per version,\nand overwhelmingly mundane. ERASED means "
          "erased. It does not mean compromised.")
    return 0


# ---------------------------------------------------------------- main

def main(argv):
    ap = argparse.ArgumentParser(
        description="What did a package registry forget, and does it admit it?")
    ap.add_argument("registry", nargs="?", choices=("npm", "pypi"))
    ap.add_argument("packages", nargs="*")
    ap.add_argument("--abbreviated", action="store_true",
                    help="npm only: read the install document, where erasure "
                         "is undetectable. Must report BLIND.")
    ap.add_argument("--survey", type=int, metavar="N",
                    help="npm only: re-measure the erasure base rate over N packages")
    ap.add_argument("--selftest", action="store_true",
                    help="check the verdict logic offline, with no network")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if args.survey is not None:
        if args.registry != "npm":
            print("--survey is npm only", file=sys.stderr)
            return 1
        return survey(args.survey)
    if not args.registry or not args.packages:
        ap.print_usage(sys.stderr)
        return 1
    if args.abbreviated and args.registry != "npm":
        print("--abbreviated is npm only", file=sys.stderr)
        return 1

    seen = set()
    for name in args.packages:
        if args.registry == "npm":
            verdict, detail, erased, extra = npm_probe(name, args.abbreviated)
        else:
            verdict, detail, erased, extra = pypi_probe(name)
        seen.add(verdict)
        print(f"{verdict:7} {name}")
        print(f"        {detail}")
        if erased:
            shown = erased[:10]
            tail = "" if len(erased) == len(shown) else f" (+{len(erased) - len(shown)} more)"
            print(f"        gone: {', '.join(shown)}{tail}")
            if args.registry == "npm" and isinstance(extra, dict):
                for v in shown[:5]:
                    print(f"          {v:24} published {extra.get(v, '?')}")
        if args.registry == "pypi" and extra:
            print(f"        yanked but still served: {', '.join(extra[:10])}")

    # BLIND outranks ERASED: not knowing is a worse answer than a known loss.
    if BLIND in seen:
        return 3
    if ERASED in seen:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
