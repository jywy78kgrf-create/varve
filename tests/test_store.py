"""The chain: append, verify, and — the point — tamper detection."""

import json
import os

import pytest

from varve import store


@pytest.fixture
def log(tmp_path):
    root = str(tmp_path / "log1")
    store.init(root, note="test founding")
    return root


def _obs(title="an observation", ref="url:https://example.com/x"):
    t, r = ref.split(":", 1)
    return {"kind": "observation", "title": title, "body": "body text",
            "anchors": [{"type": t, "ref": r}]}


def test_init_founds_empty(log):
    entries = store.read_log(log)
    assert len(entries) == 1
    assert entries[0]["kind"] == "meta"
    assert entries[0]["prev"] == ""
    assert store.verify(log) == []


def test_double_founding_refused(log):
    with pytest.raises(ValueError):
        store.init(log)


def test_append_chains(log):
    e2 = store.append(log, _obs("first"))
    e3 = store.append(log, _obs("second"))
    assert e2["seq"] == 2 and e3["seq"] == 3
    assert e3["prev"] == e2["hash"]
    assert store.verify(log) == []


def test_caller_cannot_mint_position(log):
    e = store.append(log, dict(_obs(), seq=99, id="e000099", prev="forged", hash="forged"))
    assert e["seq"] == 2 and e["prev"] != "forged"


def test_edit_breaks_chain(log):
    store.append(log, _obs("first"))
    store.append(log, _obs("second"))
    # rewrite history: change the body of entry 2 on disk
    path = os.path.join(log, "log", "000002.json")
    with open(path) as f:
        e = json.load(f)
    e["body"] = "quietly improved"
    with open(path, "w") as f:
        json.dump(e, f)
    problems = store.verify(log)
    assert any("altered" in p for p in problems)


def test_recompute_hash_after_edit_still_detected(log):
    """The sophisticated tamperer re-hashes the edited entry — the next
    entry's prev pointer still exposes the rewrite."""
    store.append(log, _obs("first"))
    store.append(log, _obs("second"))
    path = os.path.join(log, "log", "000002.json")
    with open(path) as f:
        e = json.load(f)
    e["body"] = "quietly improved"
    e["hash"] = store.entry_hash(e)
    with open(path, "w") as f:
        json.dump(e, f)
    problems = store.verify(log)
    assert any("chain broken" in p for p in problems)


def test_deletion_detected(log):
    store.append(log, _obs("first"))
    store.append(log, _obs("second"))
    os.remove(os.path.join(log, "log", "000002.json"))
    problems = store.verify(log)
    assert problems  # gap + broken chain
