"""The gate: an entry that breaks the constitution is not written.

Every append — human-typed or model-authored — passes through check(). The
rules are deliberately kind-based rather than content-sniffing: an entry
asserting facts must *choose* a kind that demands anchors, or wear the hunch
label. That trade keeps the gate honest — it enforces what it can actually
verify, instead of pretending to detect claims by regex.
"""

import re

ANCHOR_TYPES = {"url", "file", "query", "sha256", "entry"}

# kinds that assert something about the world -> anchors required
ANCHORED_KINDS = {"observation", "resolution"}
# kinds explicitly labeled as unverified -> anchors optional
LABELED_KINDS = {"hunch", "hypothesis"}
OTHER_KINDS = {"meta", "errata", "prediction"}
KINDS = ANCHORED_KINDS | LABELED_KINDS | OTHER_KINDS


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def check(entry, existing):
    """Return a list of problems with a candidate entry (empty = passes).

    `existing` is the current log, used for reference checks (errata must
    correct a real entry, resolutions must resolve a real prediction) and for
    the founded-empty rule (no timestamp before the founding).
    """
    problems = []
    kind = entry.get("kind")
    if kind not in KINDS:
        problems.append("kind must be one of: %s" % ", ".join(sorted(KINDS)))
        return problems  # nothing below is meaningful without a kind

    if not _nonempty_str(entry.get("title")):
        problems.append("title is required")
    if not _nonempty_str(entry.get("body")):
        problems.append("body is required — an entry must be usable by a reader with no memory of its author")

    ids = {e["id"] for e in existing}

    anchors = entry.get("anchors") or []
    if not isinstance(anchors, list):
        problems.append("anchors must be a list")
        anchors = []
    for a in anchors:
        if not (isinstance(a, dict) and a.get("type") in ANCHOR_TYPES and _nonempty_str(a.get("ref"))):
            problems.append("each anchor needs a type (%s) and a ref" % "/".join(sorted(ANCHOR_TYPES)))
            break
        # syntactic lint: an anchor that can't possibly resolve is not provenance,
        # it's decoration — reject it at the gate rather than discover it later
        t, ref = a["type"], a["ref"].strip()
        if t == "url" and not (ref.startswith("http://") or ref.startswith("https://")):
            problems.append("url anchor must start with http(s)://: %r" % ref)
        elif t == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", ref):
            problems.append("sha256 anchor must be 64 lowercase hex chars: %r" % ref)
        elif t == "entry":
            if not re.fullmatch(r"e\d{6,}", ref):
                problems.append("entry anchor must look like e000123: %r" % ref)
            elif ref not in ids:
                problems.append("entry anchor %s does not exist in this log" % ref)
    if kind in ANCHORED_KINDS and not anchors:
        problems.append(
            "kind '%s' asserts facts and must carry at least one anchor a stranger "
            "could check — or be relabeled as a hunch/hypothesis" % kind
        )
    if kind == "errata":
        target = entry.get("corrects")
        if target not in ids:
            problems.append("errata must name an existing entry id in 'corrects'")
    elif "corrects" in entry:
        problems.append("'corrects' is reserved for kind errata")

    if kind == "prediction":
        p = entry.get("prediction") or {}
        if not _nonempty_str(p.get("statement")):
            problems.append("prediction needs prediction.statement — the falsifiable claim itself")
        conf = p.get("p")
        if not (isinstance(conf, (int, float)) and 0.0 < conf < 1.0):
            problems.append("prediction needs prediction.p strictly between 0 and 1 (0 and 1 are not forecasts)")
        if not _nonempty_str(p.get("resolve_by")):
            problems.append("prediction needs prediction.resolve_by (ISO date) — unfalsifiable forecasts don't calibrate")

    if kind == "resolution":
        target = entry.get("resolves")
        matched = next((e for e in existing if e["id"] == target), None)
        if matched is None or matched.get("kind") != "prediction":
            problems.append("resolution must name an existing prediction entry id in 'resolves'")
        elif any(e.get("kind") == "resolution" and e.get("resolves") == target for e in existing):
            problems.append("prediction %s is already resolved; a dispute is an errata, not a second resolution" % target)
        if not isinstance(entry.get("outcome"), bool):
            problems.append("resolution needs outcome: true or false")

    # founded-empty + monotonic time, enforced AT THE GATE, not just in verify:
    # rule 4's write-time half used to check only "not before the founding",
    # which let a backdated-but-post-founding entry through until verify()
    # noticed after the fact (first external review, 2026-08-22). The chain
    # only moves forward, so the predecessor's timestamp is the floor.
    if existing:
        ts = entry.get("ts", "")
        if _nonempty_str(ts):
            if ts < existing[0].get("ts", ""):
                problems.append("timestamp predates the founding — backdating is impossible by construction")
            elif ts < existing[-1].get("ts", ""):
                problems.append("timestamp earlier than the previous entry — the chain only moves forward")

    return problems
