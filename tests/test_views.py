"""Views: brier calibration and the digest are derived, and correct."""

import pytest

from varve import store, views, web


@pytest.fixture
def log(tmp_path):
    root = str(tmp_path / "log1")
    store.init(root)
    return root


def _predict(log, p, statement):
    return store.append(log, {"kind": "prediction", "title": statement, "body": "why",
                              "prediction": {"statement": statement, "p": p,
                                             "resolve_by": "2027-01-01"}})


def _resolve(log, pred, outcome):
    return store.append(log, {"kind": "resolution", "title": "resolved", "body": "how",
                              "resolves": pred["id"], "outcome": outcome,
                              "anchors": [{"type": "url", "ref": "https://x"}]})


def test_brier(log):
    a = _predict(log, 0.9, "a happens")
    b = _predict(log, 0.4, "b happens")
    _resolve(log, a, True)    # (0.9-1)^2 = 0.01
    _resolve(log, b, False)   # (0.4-0)^2 = 0.16
    score, n, rows = views.brier(log)
    assert n == 2
    assert score == pytest.approx((0.01 + 0.16) / 2)


def test_brier_empty(log):
    score, n, rows = views.brier(log)
    assert (score, n, rows) == (None, 0, [])


def test_digest_lists_recent_and_open_predictions(log):
    store.append(log, {"kind": "hunch", "title": "a feeling", "body": "text"})
    _predict(log, 0.6, "x happens")
    out = views.digest(log, days=7)
    assert "a feeling" in out
    assert "Open predictions" in out and "x happens" in out


def test_web_render_reports_intact_chain(log):
    store.append(log, {"kind": "hunch", "title": "<b>escaped?</b>", "body": "text"})
    page = web._render(log)
    assert "chain intact" in page
    assert "<b>escaped?</b>" not in page  # html-escaped
    assert "&lt;b&gt;escaped?&lt;/b&gt;" in page


def test_beliefs_marks_corrected(log):
    obs = store.append(log, {"kind": "observation", "title": "claim", "body": "b",
                             "anchors": [{"type": "url", "ref": "https://x.example"}]})
    store.append(log, {"kind": "errata", "anchors": [{"type": "entry", "ref": "e000001"}], "title": "wrong", "body": "why",
                       "corrects": obs["id"]})
    rows = dict((e["id"], s) for e, s in views.beliefs(log))
    assert "corrected by" in rows[obs["id"]]
    page = web._render(log)
    assert "do not act on this entry" in page


def _damaged(root, **over):
    """Write an entry straight to disk, bypassing the gate — the only way to
    produce the damage a view has to survive."""
    import json, os
    from varve import store
    entries = store.read_log(root)
    seq = entries[-1]["seq"] + 1
    e = {"seq": seq, "id": "e%06d" % seq, "ts": "2026-08-23T00:00:00Z", "kind": "meta",
         "title": "t", "body": "b", "anchors": [], "tags": [], "prev": entries[-1]["hash"]}
    e.update(over)
    e["hash"] = store.entry_hash(e)
    json.dump(e, open(os.path.join(root, "log", "%06d.json" % seq), "w"))
    return e


def test_digest_survives_a_malformed_timestamp(log):
    """gate-probe reported this class CLOSED while it was open: the probe tested
    a missing 'kind' and declared damaged-log tolerance fixed. A bad ts still
    killed digest (third review, 2026-08-23). A view's job is to let a reader
    SEE the damage, so the bad entry is reported, not skipped."""
    from varve import views
    _damaged(log, ts="NOT-A-TIMESTAMP")
    out = views.digest(log, days=30)
    assert "BAD TS" in out


def test_digest_survives_a_malformed_prediction_payload(log):
    from varve import views
    _damaged(log, kind="prediction", prediction="not a dict")
    out = views.digest(log, days=30)
    assert "malformed prediction payload" in out


def test_brier_skips_an_unscorable_prediction(log):
    """A resolution pointing at a prediction with no usable p must not crash the
    calibration; verify() is what reports the damage."""
    from varve import store, views
    _damaged(log, kind="prediction", prediction={"statement": "x", "resolve_by": "2027-01-01"})
    entries = store.read_log(log)
    pid = entries[-1]["id"]
    _damaged(log, kind="resolution", resolves=pid, outcome=True,
             anchors=[{"type": "url", "ref": "https://x"}])
    score, n, rows = views.brier(log)
    assert n == 0 and score is None


def test_corrected_prediction_keeps_its_warning_in_the_web_view(log):
    """The one view strangers actually read dropped the correction banner for a
    corrected prediction, because the prediction branch ASSIGNED extra instead
    of appending. beliefs() was right; the public page was not (third review)."""
    from varve import store, web
    pred = store.append(log, {"kind": "prediction", "title": "forecast", "body": "b",
                              "prediction": {"statement": "x", "p": 0.6,
                                             "resolve_by": "2027-01-01"}})
    store.append(log, {"kind": "errata", "title": "wrong", "body": "b",
                       "corrects": pred["id"],
                       "anchors": [{"type": "entry", "ref": pred["id"]}]})
    page = web._render(log)
    assert "do not act on this entry" in page
    assert "resolve by 2027-01-01" in page


def test_digest_survives_malformed_anchors(log):
    """Found by the widened gate-probe, not by the review that widened it: an
    'anchors' that is a bare string raised TypeError in the digest."""
    from varve import views
    _damaged(log, anchors="url:https://x")
    assert "MALFORMED anchors" in views.digest(log, days=30)
