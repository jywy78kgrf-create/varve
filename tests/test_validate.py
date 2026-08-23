"""The gate: constitution rules enforced on every append."""

import pytest

from varve import store


@pytest.fixture
def log(tmp_path):
    root = str(tmp_path / "log1")
    store.init(root)
    return root


def test_observation_requires_anchor(log):
    with pytest.raises(ValueError, match="anchor"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b"})


def test_hunch_needs_no_anchor(log):
    e = store.append(log, {"kind": "hunch", "title": "t", "body": "b"})
    assert e["kind"] == "hunch"


def test_unknown_kind_rejected(log):
    with pytest.raises(ValueError, match="kind"):
        store.append(log, {"kind": "memory", "title": "t", "body": "b"})


def test_body_required(log):
    with pytest.raises(ValueError, match="body"):
        store.append(log, {"kind": "hunch", "title": "t", "body": "  "})


def test_errata_must_point_at_real_entry(log):
    with pytest.raises(ValueError, match="errata"):
        store.append(log, {"kind": "errata", "title": "t", "body": "b",
                           "corrects": "e999999"})
    e = store.append(log, {"kind": "errata", "title": "t", "body": "b",
                           "corrects": "e000001"})
    assert e["corrects"] == "e000001"


def test_prediction_needs_falsifiable_shape(log):
    with pytest.raises(ValueError, match="prediction"):
        store.append(log, {"kind": "prediction", "title": "t", "body": "b",
                           "prediction": {"statement": "x", "p": 1.0,
                                          "resolve_by": "2027-01-01"}})
    e = store.append(log, {"kind": "prediction", "title": "t", "body": "b",
                           "prediction": {"statement": "x", "p": 0.7,
                                          "resolve_by": "2027-01-01"}})
    assert e["prediction"]["p"] == 0.7


def test_resolution_rules(log):
    pred = store.append(log, {"kind": "prediction", "title": "t", "body": "b",
                              "prediction": {"statement": "x", "p": 0.7,
                                             "resolve_by": "2027-01-01"}})
    # must resolve a real prediction, with evidence and a boolean outcome
    with pytest.raises(ValueError, match="resolution"):
        store.append(log, {"kind": "resolution", "title": "t", "body": "b",
                           "resolves": "e000001", "outcome": True,
                           "anchors": [{"type": "url", "ref": "https://x"}]})
    res = store.append(log, {"kind": "resolution", "title": "t", "body": "b",
                             "resolves": pred["id"], "outcome": True,
                             "anchors": [{"type": "url", "ref": "https://x"}]})
    assert res["resolves"] == pred["id"]
    # a second resolution of the same prediction is a dispute -> errata
    with pytest.raises(ValueError, match="already resolved"):
        store.append(log, {"kind": "resolution", "title": "t2", "body": "b",
                           "resolves": pred["id"], "outcome": False,
                           "anchors": [{"type": "url", "ref": "https://x"}]})


def test_ts_is_reserved_not_chosen(log):
    """A caller-supplied ts is DISCARDED, not honoured. Rule 4 rests on the
    timestamp, so it is assigned by the store like seq/id/prev/hash — and the
    worker hands a model's raw JSON straight to append(), which made this the
    untrusted path. Found by the second review (QQ1eF, 2026-08-23)."""
    e = store.append(log, {"kind": "hunch", "title": "t", "body": "b",
                           "ts": "2099-01-01T00:00:00Z"})
    assert e["ts"] != "2099-01-01T00:00:00Z"
    # and the log is still writable, which is the whole point: a post-dated
    # entry used to jam it permanently, since rule 1 forbids removing it
    store.append(log, {"kind": "hunch", "title": "t2", "body": "b"})
    assert store.verify(log) == []


def test_gate_still_rejects_backdating_when_called_directly(log):
    """The monotonicity rules stay in the gate even though append() no longer
    lets a caller reach them — verify() and any direct validate caller need
    them, and defence in depth is cheap."""
    from varve import validate

    entries = store.read_log(log)
    founding_ts = entries[0]["ts"]
    assert any("founding" in p for p in validate.check(
        {"kind": "hunch", "title": "t", "body": "b", "ts": "1999-01-01T00:00:00Z"},
        entries))
    assert any("only moves forward" in p for p in validate.check(
        {"kind": "hunch", "title": "t", "body": "b", "ts": founding_ts},
        [entries[0], dict(entries[0], id="e000002", ts="2030-01-01T00:00:00Z")]))


def test_anchor_lint(log):
    with pytest.raises(ValueError, match="http"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "url", "ref": "example.com/x"}]})
    with pytest.raises(ValueError, match="hex"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "sha256", "ref": "beef"}]})
    with pytest.raises(ValueError, match="does not exist"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "entry", "ref": "e009999"}]})
    e = store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "entry", "ref": "e000001"}]})
    assert e["anchors"][0]["ref"] == "e000001"
