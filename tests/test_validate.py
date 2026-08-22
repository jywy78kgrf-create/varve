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


def test_backdating_impossible(log):
    with pytest.raises(ValueError, match="founding"):
        store.append(log, {"kind": "hunch", "title": "t", "body": "b",
                           "ts": "1999-01-01T00:00:00Z"})
