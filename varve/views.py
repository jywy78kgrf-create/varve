"""Derived views over the log. Views are disposable; the log is the truth.

Nothing here writes anything. A digest or a Brier score you disagree with is
recomputed, never stored — storing a view would create a second, editable
version of the record.
"""

from datetime import datetime, timedelta, timezone

from . import store


def _parse_ts(ts):
    """Parse a timestamp, or None if it is not one.

    Returning None rather than raising is deliberate: a view's job is to keep
    working on a damaged log so a reader can SEE the damage. gate-probe read
    this class CLOSED because it tested one damage shape (a missing 'kind') and
    reported the whole class fixed; a malformed ts still killed digest (third
    review, 2026-08-23)."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def digest(root, days=7, limit=700):
    """A 'while you were away' summary of the recent window, as plain text."""
    entries = store.read_log(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # An entry whose ts will not parse is damaged, not absent: keep it in the
    # window and flag it, so the digest reports the breakage instead of hiding it.
    recent, undated = [], []
    for e in entries:
        when = _parse_ts(e.get("ts"))
        if when is None:
            undated.append(e)
        elif when >= cutoff:
            recent.append(e)
    recent = undated + recent
    lines = ["varve digest — last %d day(s), %d entr%s (log ends at %s)" % (
        days, len(recent), "y" if len(recent) == 1 else "ies", entries[-1]["id"])]
    for e in recent:
        lines.append("")
        ts = e.get("ts")
        stamp = ts[:10] if isinstance(ts, str) and _parse_ts(ts) else "!! BAD TS %r" % (ts,)
        lines.append("%s %s [%s] %s" % (
            e.get("id", "?"), stamp, e.get("kind", "?"), e.get("title", "")))
        # 200 characters rendered a 1000-word entry as noise, in the very view
        # designated for a reader with no memory of the author (rule 6, third
        # review). Lead with the first paragraph, which is where these entries
        # actually put their claim, and cap generously.
        body = str(e.get("body", "")).strip()
        lead = body.split("\n\n")[0].replace("\n", " ").strip()
        if len(lead) > limit:
            lead = lead[:limit].rsplit(" ", 1)[0] + "…"
        rest = len(body.split()) - len(lead.split())
        if rest > 0:
            lead += " […%d more words]" % rest
        lines.append("  " + lead)
        anchors = e.get("anchors")
        if anchors:
            # Render whatever is there. A gate-passed entry always has a list of
            # dicts, but a damaged log is exactly what a reader needs the digest
            # to survive — an 'anchors' that is a bare string used to raise
            # TypeError here (found by the widened gate-probe, 2026-08-23).
            if isinstance(anchors, list):
                shown = "; ".join(
                    ("%s:%s" % (a.get("type"), a.get("ref"))) if isinstance(a, dict) else repr(a)
                    for a in anchors)
            else:
                shown = "!! MALFORMED anchors: %r" % (anchors,)
            lines.append("  anchors: " + shown)
    open_preds = unresolved_predictions(entries)
    if open_preds:
        lines.append("")
        lines.append("Open predictions:")
        for e in open_preds:
            p = e.get("prediction")
            if not isinstance(p, dict):
                lines.append("  %s !! malformed prediction payload: %r" % (e.get("id", "?"), p))
                continue
            lines.append("  %s p=%s resolve by %s — %s" % (
                e.get("id", "?"), p.get("p"), p.get("resolve_by"), p.get("statement")))
    return "\n".join(lines)


def corrections(entries):
    """Map of entry id -> list of errata entry ids that correct it.

    Append-only means a falsified observation keeps its confident wording
    forever; the correction lives in a later entry. This map is how every
    view keeps an amnesiac reader from acting on a dead claim — the record
    stays intact, the *reading* carries the warning."""
    out = {}
    for e in entries:
        if e.get("kind") == "errata" and e.get("corrects"):
            out.setdefault(e["corrects"], []).append(e["id"])
    return out


def beliefs(root):
    """Current belief state: every claim-bearing entry with its standing.
    Returns rows of (entry, status) where status is 'standing' or
    'corrected by eNNNNNN[, ...]'. Derived, like everything here."""
    entries = store.read_log(root)
    corr = corrections(entries)
    rows = []
    for e in entries:
        if e.get("kind") in ("observation", "hypothesis", "hunch", "prediction", "survey"):
            status = ("corrected by " + ", ".join(corr[e["id"]])) if e["id"] in corr else "standing"
            rows.append((e, status))
    return rows


def unresolved_predictions(entries):
    resolved = {e.get("resolves") for e in entries if e.get("kind") == "resolution"}
    return [e for e in entries if e.get("kind") == "prediction" and e["id"] not in resolved]


def brier(root):
    """Calibration over resolved predictions.

    Brier score = mean (p - outcome)^2; 0 is prophecy, 0.25 is coin-flipping
    at p=0.5, 1 is confident wrongness. Returns (score, n, rows) where rows
    are (prediction entry, resolution entry, per-forecast score).
    """
    entries = store.read_log(root)
    by_id = {e["id"]: e for e in entries}
    rows = []
    for r in (e for e in entries if e.get("kind") == "resolution"):
        pred = by_id.get(r.get("resolves"))
        if pred is None:
            continue
        payload = pred.get("prediction")
        p = payload.get("p") if isinstance(payload, dict) else None
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            continue  # a prediction with no usable p cannot be scored; skipping
                      # beats crashing, and verify() is what reports the damage
        outcome = 1.0 if r.get("outcome") else 0.0
        rows.append((pred, r, (p - outcome) ** 2))
    if not rows:
        return None, 0, []
    score = sum(s for _, _, s in rows) / len(rows)
    return score, len(rows), rows


def ruleset_history(root):
    """Every point in the log where the admitting ruleset changed.

    Rows of (entry, previous_gate, gate). Entries written before the stamp
    existed report None, which is honest: nobody knows what judged them.
    A change here is not chain damage — gates get fixed — so verify() does not
    fail on it. It is something a reader must be able to SEE.
    """
    # Sentinel, not None: "unstamped" IS a ruleset state — the 14 entries
    # written before the stamp existed genuinely have no record of what judged
    # them, and reporting zero states would hide exactly that.
    rows, prev = [], object()
    for e in store.read_log(root):
        gate = e.get("gate")
        if gate != prev:
            rows.append((e, prev, gate))
            prev = gate
    return rows


def questions(root):
    """Open problems as objects, not prose.

    Rows of (question entry, [ids of entries that answer it]). An empty list
    means open. Rule 6 asks that a reader with no memory of the author be able
    to pick up the work; until this existed, the only way to find out what the
    log did not know was to read every body in full.
    """
    entries = store.read_log(root)
    answered = {}
    for e in entries:
        if e.get("answers"):
            answered.setdefault(e["answers"], []).append(e["id"])
    return [(e, answered.get(e["id"], []))
            for e in entries if e.get("kind") == "question"]


def commitments(root, today=None):
    """Every promise the log has made, and whether it is still owed.

    Rows of (entry, discharged_by_or_None, "open"|"overdue"|"kept"). This is the
    object an amnesiac most needs and is least likely to reconstruct: a promise
    made three sessions ago by an instance that no longer exists, to someone who
    remembers it perfectly.
    """
    entries = store.read_log(root)
    closed = {e["discharges"]: e["id"] for e in entries if e.get("discharges")}
    now = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for e in entries:
        if e.get("kind") != "commitment":
            continue
        by = closed.get(e["id"])
        if by:
            state = "kept"
        elif str(e.get("due", "")) < now:
            state = "overdue"
        else:
            state = "open"
        rows.append((e, by, state))
    return rows


def check_anchors(root, timeout=15.0, kinds=("url",)):
    """Dereference anchors that the gate can only shape-check, and report.

    The gate existence-checks 'entry' and 'file' anchors at write time, which is
    what makes a fabricated referent cheaper to avoid than to invent — the fix
    the from-memory reflex calls for. 'url' anchors get no such treatment: the
    gate is stdlib, offline and deterministic by design, and reaching the
    network at write time would trade all three away.

    So the check moves out of the gate and into a command that can be run on a
    schedule. Rows of (entry_id, ref, status). Status is an HTTP code, or a
    reason string. Nothing here is a verification that the page SUPPORTS the
    claim — only that a reader following the anchor arrives somewhere.
    """
    import urllib.error
    import urllib.request

    rows = []
    for e in store.read_log(root):
        for a in (e.get("anchors") or []):
            if not isinstance(a, dict) or a.get("type") not in kinds:
                continue
            ref = str(a.get("ref", ""))
            try:
                req = urllib.request.Request(ref, method="HEAD",
                                             headers={"User-Agent": "varve-anchor-check"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    rows.append((e["id"], ref, r.status, "ok"))
            except urllib.error.HTTPError as exc:
                # Some hosts refuse HEAD but serve GET. Retry once before
                # reporting a dead anchor, since a false "dead" here is the
                # crying-wolf failure that makes a checker worthless.
                if exc.code in (403, 405, 501):
                    try:
                        with urllib.request.urlopen(
                                urllib.request.Request(
                                    ref, headers={"User-Agent": "varve-anchor-check"}),
                                timeout=timeout) as r:
                            rows.append((e["id"], ref, r.status, "ok"))
                            continue
                    except urllib.error.HTTPError as exc2:
                        rows.append((e["id"], ref, exc2.code, _verdict(exc2.code)))
                        continue
                    except Exception as exc2:
                        rows.append((e["id"], ref, type(exc2).__name__, "blocked"))
                        continue
                rows.append((e["id"], ref, exc.code, _verdict(exc.code)))
            except Exception as exc:
                # A name that does not resolve IS a dead anchor; a timeout or a
                # refused connection is this machine's problem, not the ref's.
                name = type(exc).__name__
                reason = str(getattr(exc, "reason", exc))
                dead = "getaddrinfo" in reason or "Name or service" in reason \
                    or "nodename nor servname" in reason
                rows.append((e["id"], ref, name, "DEAD" if dead else "blocked"))
    return rows


def _verdict(code):
    """403/407 are what a filtering proxy returns for a page that exists.
    Only 404 and 410 are the server saying the thing is not there."""
    if code in (404, 410):
        return "DEAD"
    if 200 <= code < 400:
        return "ok"
    return "blocked"
