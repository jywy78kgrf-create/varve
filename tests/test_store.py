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


def test_tail_truncation_caught_by_expected_head(log):
    """Reviewer finding: deleting the tail leaves an internally-consistent
    log — only a witnessed head exposes it."""
    store.append(log, _obs("first"))
    e3 = store.append(log, _obs("second"))
    witnessed = e3["hash"]
    assert store.verify(log, expect_head=witnessed) == []
    os.remove(os.path.join(log, "log", "000003.json"))
    assert store.verify(log) == []  # the blind spot, on the record
    problems = store.verify(log, expect_head=witnessed)
    assert any("witnessed head" in p for p in problems)


def test_seq_numeric_ordering():
    """%06d widens past 999999; the regex and the sort must both keep up."""
    assert store._SEQ_RE.match("1000000.json")
    names = [(int(store._SEQ_RE.match(n).group(1)), n)
             for n in ["1000000.json", "999999.json"]]
    assert [n for _, n in sorted(names)] == ["999999.json", "1000000.json"]


def test_concurrent_appends_do_not_lose_writes(log):
    """Two sessions can be awake at once — the terms bless an operator knock
    landing during a scheduled wake — and append() reads the head then writes
    its successor. Without a lock both see the same head and one dies."""
    import multiprocessing as mp

    def w(root, n, q):
        ok = 0
        for i in range(n):
            try:
                store.append(root, {"kind": "hunch", "title": "%d-%d" % (os.getpid(), i),
                                    "body": "b"})
                ok += 1
            except ValueError:
                pass
        q.put(ok)

    import os
    q = mp.Queue()
    procs = [mp.Process(target=w, args=(log, 6, q)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert sum(q.get() for _ in procs) == 18
    entries = store.read_log(log)
    assert len(entries) == 19
    assert [e["seq"] for e in entries] == list(range(1, 20))
    assert store.verify(log) == []


def test_a_stale_lock_does_not_silence_successors(log):
    """A session killed mid-append must not jam the log forever — the same
    reasoning that made a post-dated entry a permanent jam worth fixing."""
    import os, time
    lock = os.path.join(log, ".append.lock")
    os.mkdir(lock)
    os.utime(lock, (time.time() - 10000, time.time() - 10000))
    e = store.append(log, {"kind": "hunch", "title": "after a crash", "body": "b"})
    assert e["title"] == "after a crash"
    assert not os.path.exists(lock)
