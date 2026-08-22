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
