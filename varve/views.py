"""Derived views over the log. Views are disposable; the log is the truth.

Nothing here writes anything. A digest or a Brier score you disagree with is
recomputed, never stored — storing a view would create a second, editable
version of the record.
"""

from datetime import datetime, timedelta, timezone

from . import store


def _parse_ts(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def digest(root, days=7):
    """A 'while you were away' summary of the recent window, as plain text."""
    entries = store.read_log(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [e for e in entries if _parse_ts(e["ts"]) >= cutoff]
    lines = ["varve digest — last %d day(s), %d entr%s (log ends at %s)" % (
        days, len(recent), "y" if len(recent) == 1 else "ies", entries[-1]["id"])]
    for e in recent:
        lines.append("")
        lines.append("%s %s [%s] %s" % (e["id"], e["ts"][:10], e.get("kind", "?"), e.get("title", "")))
        body = str(e.get("body", "")).strip().replace("\n", " ")
        lines.append("  " + (body[:200] + "…" if len(body) > 200 else body))
        if e.get("anchors"):
            lines.append("  anchors: " + "; ".join("%(type)s:%(ref)s" % a for a in e["anchors"]))
    open_preds = unresolved_predictions(entries)
    if open_preds:
        lines.append("")
        lines.append("Open predictions:")
        for e in open_preds:
            p = e["prediction"]
            lines.append("  %s p=%.2f resolve by %s — %s" % (e["id"], p["p"], p["resolve_by"], p["statement"]))
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
        p = pred["prediction"]["p"]
        outcome = 1.0 if r["outcome"] else 0.0
        rows.append((pred, r, (p - outcome) ** 2))
    if not rows:
        return None, 0, []
    score = sum(s for _, _, s in rows) / len(rows)
    return score, len(rows), rows
