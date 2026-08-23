"""The gate: an entry that breaks the constitution is not written.

Every append — human-typed or model-authored — passes through check(). The
rules are deliberately kind-based rather than content-sniffing: an entry
asserting facts must *choose* a kind that demands anchors, or wear the hunch
label. That trade keeps the gate honest — it enforces what it can actually
verify, instead of pretending to detect claims by regex.
"""

import json
import os
import re

ANCHOR_TYPES = {"url", "file", "query", "sha256", "entry"}

# The one timestamp shape this log speaks, shared with store.verify so the
# gate and the verifier cannot drift apart on what a valid ts looks like.
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

# 'varve/store.py:170' and 'varve/store.py:170-190' are normal ways to cite a
# line, so a trailing line reference is stripped before testing existence.
_LINE_SUFFIX_RE = re.compile(r":\d+(-\d+)?$")


def _file_anchor_resolves(root, ref):
    """Whether a repo-relative file anchor points at something that exists.

    Absolute paths pass unchecked: they name a filesystem the reader does not
    have, so their existence here proves nothing about theirs.
    """
    if os.path.isabs(ref):
        return True
    candidates = [ref]
    stripped = _LINE_SUFFIX_RE.sub("", ref)
    if stripped != ref:
        candidates.append(stripped)
    base = os.path.abspath(root)
    for c in candidates:
        full = os.path.abspath(os.path.join(base, c))
        # A '../' ref can resolve on this disk while naming nothing a reader
        # who cloned the repository would have — the same emptiness the
        # existence check exists to catch.
        if os.path.commonpath([base, full]) == base and os.path.exists(full):
            return True
    return False

# kinds that assert something about the world -> anchors required
ANCHORED_KINDS = {"observation", "resolution"}
# kinds explicitly labeled as unverified -> anchors optional
LABELED_KINDS = {"hunch", "hypothesis"}
OTHER_KINDS = {"meta", "errata", "prediction"}
KINDS = ANCHORED_KINDS | LABELED_KINDS | OTHER_KINDS


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def check(entry, existing, root=None):
    """Return a list of problems with a candidate entry (empty = passes).

    `existing` is the current log, used for reference checks (errata must
    correct a real entry, resolutions must resolve a real prediction) and for
    the founded-empty rule (no timestamp before the founding).

    `root` is the log root when the caller has one. It is what lets a `file`
    anchor be checked for existence the way an `entry` anchor already is;
    without it, file anchors pass unchecked rather than being wrongly rejected.
    """
    problems = []

    # An entry that cannot be serialised cannot be hashed, and entry_hash runs
    # AFTER this gate — so a non-JSON value used to surface as TypeError out of
    # canonical(), which worker.py does not catch. Nothing reached the disk, but
    # the run died instead of the entry being rejected (second review,
    # 2026-08-23).
    try:
        json.dumps(entry)
    except (TypeError, ValueError) as exc:
        problems.append("entry must be JSON-serialisable: %s" % exc)
        return problems  # every check below reads fields this one just distrusted

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
    tags = entry.get("tags") or []
    if not (isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
        problems.append("tags must be a list of strings")
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
        elif t == "file" and root is not None and not _file_anchor_resolves(root, ref):
            # 'entry' anchors were existence-checked from the start while 'file'
            # anchors were only required to be non-empty, though both are
            # checkable locally at write time. Same standard for both now: an
            # anchor a reader cannot follow is decoration, not provenance
            # (second review, 2026-08-23).
            problems.append("file anchor does not resolve under the log root: %r" % ref)
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
        # Type-check before reaching in. A model returning "prediction": "..."
        # used to raise AttributeError here, and worker.py catches only
        # ValueError — so a malformed field crashed the run instead of coming
        # back as gate feedback the author could fix (second review,
        # 2026-08-23).
        if not isinstance(p, dict):
            problems.append("prediction must be an object with statement/p/resolve_by, not %s"
                            % type(p).__name__)
            p = {}
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
        # Shape first. store.append now assigns ts itself, so a malformed one
        # should be unreachable from there — but the gate is also the last word
        # for any other caller, and a non-string ts used to slip through here
        # (the checks below simply skipped it) and then crash the verifier on
        # its first comparison (second review, 2026-08-23).
        if not (isinstance(ts, str) and TS_RE.fullmatch(ts)):
            problems.append("ts must look like YYYY-MM-DDThh:mm:ssZ: %r" % (ts,))
        elif ts < existing[0].get("ts", ""):
            problems.append("timestamp predates the founding — backdating is impossible by construction")
        elif ts < existing[-1].get("ts", ""):
            problems.append("timestamp earlier than the previous entry — the chain only moves forward")

    return problems
