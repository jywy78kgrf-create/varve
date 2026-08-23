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


def digest(root, days=7):
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
        body = str(e.get("body", "")).strip().replace("\n", " ")
        lines.append("  " + (body[:200] + "…" if len(body) > 200 else body))
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
        if e.get("kind") in ("observation", "hypothesis", "hunch", "prediction"):
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
