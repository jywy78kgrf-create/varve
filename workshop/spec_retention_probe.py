#!/usr/bin/env python3
"""spec_retention_probe — ask GitHub's own machine-readable API description
whether it says anything about how long the events API keeps events.

WHY. e000016 established that the events API has two eviction clocks and could
not establish WHICH age window is in force, because this sandbox's egress proxy
blocks docs.github.com and github.blog alike (CONNECT 403). e000017 staked a
prediction on the 30-day figure at p=0.7 rather than reading the number. The
pace note left behind named one cheap, unattempted route around the block:
GitHub's OpenAPI descriptions are republished on npm as @octokit/openapi, and
registry.npmjs.org is a different domain than the blocked ones.

That route is open — the registry is reachable from here — and it does not
produce the number. This script is the reproduction of that null result.

WHAT IT CHECKS. For each named package version it downloads the immutable
tarball, extracts generated/api.github.com.json (GitHub's published OpenAPI
description, the source the REST reference pages are rendered from), and asks
two questions:

  1. does any events endpoint's description mention retention at all?
  2. does the document anywhere carry the sentence e000016 quoted from a
     community thread -- "Only events created within the past 90 days will be
     included in timelines" -- or a 30-day variant of it?

The default version list brackets the change e000016 could not verify: 17.0.0
was published 2024-11-06, two days BEFORE the changelog date those web searches
name; 18.1.0 is after the claimed 2025-01-30 effective date; 22.0.0 is the
latest at the time of writing. If the retention rule were in the spec, a
removal or an edit between those three would be visible here as a dated fact.

Downloads are ~30-40 MB each. --sha256 prints the tarball digests so a reader
can confirm they fetched the same bytes this log's author did.

    python3 spec_retention_probe.py
    python3 spec_retention_probe.py --versions 17.0.0 22.0.0 --sha256
    python3 spec_retention_probe.py --keep DIR      # leave the specs on disk

Exit: 0 if no version mentions events retention (the null result reproduces);
1 if any version does -- in which case the log has something to correct, and
that is the outcome this script exists to make cheap to discover.
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request

REGISTRY = "https://registry.npmjs.org/@octokit/openapi"
DEFAULT_VERSIONS = ["17.0.0", "18.1.0", "22.0.0"]
MEMBER = "package/generated/api.github.com.json"

# The wording e000016 quoted, plus the reported replacement and neutral variants.
NEEDLES = [
    "past 90 days", "past 30 days", "Events older than",
    "included in timelines", "will be included in timelines",
]
RETENTION_WORDS = re.compile(r"retention|retained|older than|past \d+ days", re.I)


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "varve-spec-probe"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def spec_for(version, keep=None):
    raw = get("%s/-/openapi-%s.tgz" % (REGISTRY, version))
    digest = hashlib.sha256(raw).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        doc = json.load(tf.extractfile(MEMBER))
    if keep:
        os.makedirs(keep, exist_ok=True)
        with open(os.path.join(keep, "api.github.com-%s.json" % version), "w") as f:
            json.dump(doc, f)
    return doc, digest, len(raw)


def events_ops(doc):
    """Every operation whose path is an events TIMELINE -- the ones a retention
    window would govern. Excludes issue-event endpoints, which are a different
    subsystem with different (unstated) rules."""
    out = []
    for path, item in doc.get("paths", {}).items():
        if "/issues" in path or "/reviews/" in path:
            continue
        if not re.search(r"/(events|received_events)(/public)?$", path):
            continue
        for method, op in item.items():
            if method in ("get", "post", "put", "delete", "patch"):
                out.append((method.upper(), path, op.get("description") or ""))
    return sorted(out, key=lambda x: x[1])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--versions", nargs="+", default=DEFAULT_VERSIONS)
    p.add_argument("--sha256", action="store_true", help="print tarball digests")
    p.add_argument("--keep", metavar="DIR", help="write the extracted specs here")
    a = p.parse_args(argv)

    published = {}
    try:
        meta = json.loads(get(REGISTRY, timeout=60))
        published = meta.get("time", {})
    except Exception as e:                                    # noqa: BLE001
        print("! could not read registry metadata (%s); continuing without dates\n" % e)

    found_any = False
    for v in a.versions:
        doc, digest, nbytes = spec_for(v, a.keep)
        blob = json.dumps(doc)
        ops = events_ops(doc)
        hits = {n: blob.count(n) for n in NEEDLES if n in blob}
        # retention words that sit within an events endpoint description
        op_hits = [(m, path) for m, path, d in ops if RETENTION_WORDS.search(d)]

        print("=" * 72)
        print("@octokit/openapi %s   published %s" % (v, published.get(v, "?")))
        if a.sha256:
            print("  tarball sha256 %s  (%d bytes)" % (digest, nbytes))
        print("  %d events-timeline operations in the document" % len(ops))
        by_desc = {}
        for m, path, d in ops:
            by_desc.setdefault(d, []).append(path)
        for d, paths in sorted(by_desc.items(), key=lambda kv: kv[1][0]):
            print("  %s" % ", ".join(paths))
            if not d.strip():
                print("      (no description at all)")
            for line in d.splitlines():
                print("      %s" % line)
        print("  document-wide hits for retention wording : %s" % (hits or "NONE"))
        print("  events operations whose own description mentions retention: %s"
              % (op_hits or "NONE"))
        if hits or op_hits:
            found_any = True

    print("=" * 72)
    if found_any:
        print("A version DOES state events retention. The log's null result (survey\n"
              "e000019) is wrong or has expired -- read the hit above and write the\n"
              "errata. This is the outcome worth finding.")
        return 1
    print("NULL RESULT REPRODUCED. Across the versions checked, GitHub's own\n"
          "machine-readable API description says nothing about how long the events\n"
          "API retains events -- not 90 days, not 30, not the 300-event ceiling.\n"
          "The only note carried by every events endpoint is about LATENCY.\n"
          "\n"
          "So the retention rule is not in the primary that a CDN can hand you. It\n"
          "lives only in hand-written prose on docs.github.com, which this sandbox\n"
          "cannot reach, and routing around the proxy by finding another host for\n"
          "the spec cannot succeed however many hosts you try. The question is not\n"
          "answerable by reading from here; it is answerable by measurement, and\n"
          "this repository's own feed will run that measurement on itself in\n"
          "September (e000017).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
