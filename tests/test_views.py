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


def test_questions_lists_open_problems(log):
    from varve import store, views
    q1 = store.append(log, {"kind": "question", "title": "open one", "body": "b"})
    q2 = store.append(log, {"kind": "question", "title": "closed one", "body": "b"})
    store.append(log, {"kind": "hunch", "title": "answer", "body": "b", "answers": q2["id"]})
    rows = dict((e["id"], ans) for e, ans in views.questions(log))
    assert rows[q1["id"]] == []
    assert rows[q2["id"]] == [store.read_log(log)[-1]["id"]]


def test_digest_leads_with_the_claim_and_says_what_it_cut(log):
    """200 characters rendered a 1000-word entry as noise in the one view meant
    for a reader with no memory of the author (rule 6, third review)."""
    from varve import store, views
    body = "The claim, stated first.\n\n" + ("filler " * 400).strip()
    store.append(log, {"kind": "hunch", "title": "long", "body": body})
    out = views.digest(log, days=30)
    assert "The claim, stated first." in out
    assert "more words]" in out


def test_pace_id_reports_the_rudder(log, tmp_path):
    """The hold steers the next session, sits outside the chain, and corrupting
    it trips nothing. Hashing it costs one line in a report that already
    carries the head (third review)."""
    import json, os
    from varve import store
    assert store.pace_id(log) is None
    json.dump({"next": "2026-09-01T00:00:00Z", "hold": "x"},
              open(os.path.join(log, "pace.json"), "w"))
    nxt, digest = store.pace_id(log)
    assert nxt == "2026-09-01T00:00:00Z" and len(digest) == 16
    json.dump({"next": "2026-09-01T00:00:00Z", "hold": "TAMPERED"},
              open(os.path.join(log, "pace.json"), "w"))
    assert store.pace_id(log)[1] != digest


def test_commitments_ledger_separates_open_overdue_and_kept(log):
    from varve import store, views
    old = store.append(log, {"kind": "commitment", "title": "late", "body": "b",
                             "due": "2020-01-01", "owed_to": "x"})
    soon = store.append(log, {"kind": "commitment", "title": "future", "body": "b",
                              "due": "2099-01-01", "owed_to": "y"})
    done = store.append(log, {"kind": "commitment", "title": "done", "body": "b",
                              "due": "2020-01-01", "owed_to": "z"})
    store.append(log, {"kind": "meta", "title": "did it", "body": "b",
                       "discharges": done["id"]})
    state = {e["id"]: s for e, _, s in views.commitments(log)}
    assert state[old["id"]] == "overdue"
    assert state[soon["id"]] == "open"
    assert state[done["id"]] == "kept"


def test_the_public_page_says_whether_the_alarm_is_overdue(log):
    """From outside, chosen silence and a dead scheduler are the same picture.
    Saying 'OVERDUE by N hours' is the cheapest thing that separates them."""
    import json, os
    from varve import web
    json.dump({"next": "2020-01-01T00:00:00Z", "hold": "h"},
              open(os.path.join(log, "pace.json"), "w"))
    assert "OVERDUE" in web._render(log)
    json.dump({"next": "2099-01-01T00:00:00Z", "hold": "h"},
              open(os.path.join(log, "pace.json"), "w"))
    page = web._render(log)
    assert "resting" in page and "OVERDUE" not in page
    json.dump({"next": "whenever", "hold": "h"},
              open(os.path.join(log, "pace.json"), "w"))
    assert "unknown" in web._render(log)


def test_check_anchors_separates_dead_from_unreachable(log, monkeypatch):
    """A 403 from a filtering proxy is a fact about the checker. Reporting it as
    a broken link is the crying-wolf failure that makes a checker worthless."""
    import urllib.error, urllib.request
    from varve import store, views
    store.append(log, {"kind": "observation", "title": "t", "body": "b",
                       "anchors": [{"type": "url", "ref": "https://example.com/gone"}]})

    def fake(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert [r[3] for r in views.check_anchors(log)] == ["DEAD"]

    def blocked(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    assert [r[3] for r in views.check_anchors(log)] == ["blocked"]
