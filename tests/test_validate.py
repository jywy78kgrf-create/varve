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
        store.append(log, {"kind": "errata", "anchors": [{"type": "entry", "ref": "e000001"}], "title": "t", "body": "b",
                           "corrects": "e999999"})
    e = store.append(log, {"kind": "errata", "anchors": [{"type": "entry", "ref": "e000001"}], "title": "t", "body": "b",
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


def test_errata_must_anchor(log):
    """An errata asserts a fact — that a past claim is wrong — so rule 2 applies
    to it like any other assertion. It didn't until the third review
    (2026-08-23) found the hole sitting exactly where the log corrects itself."""
    with pytest.raises(ValueError, match="at least one anchor"):
        store.append(log, {"kind": "errata", "title": "t", "body": "b",
                           "corrects": "e000001"})
    e = store.append(log, {"kind": "errata", "title": "t", "body": "b",
                           "corrects": "e000001",
                           "anchors": [{"type": "entry", "ref": "e000001"}]})
    assert e["kind"] == "errata"


def test_resolve_by_must_be_a_real_date(log):
    """'soon' used to pass. A resolve_by that cannot come due makes a forecast
    unfalsifiable while looking falsifiable — worse than omitting it."""
    def pred(by):
        return {"kind": "prediction", "title": "t", "body": "b",
                "prediction": {"statement": "x", "p": 0.5, "resolve_by": by}}
    for bad in ("soon", "", "next year", "2026-13-01", "2026-02-31", None, 20261231):
        with pytest.raises(ValueError, match="resolve_by"):
            store.append(log, pred(bad))
    assert store.append(log, pred("2027-01-01"))["prediction"]["resolve_by"] == "2027-01-01"


def test_query_anchor_must_look_runnable(log):
    """The gate cannot run a query, so this is shape-only — and that limit is
    the point: 'query' is the weakest anchor type in the system."""
    with pytest.raises(ValueError, match="query anchor"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "query", "ref": "ab"}]})
    with pytest.raises(ValueError, match="query anchor"):
        store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "query", "ref": "run this\nand that"}]})
    e = store.append(log, {"kind": "observation", "title": "t", "body": "b",
                           "anchors": [{"type": "query", "ref": "git log --oneline"}]})
    assert e["anchors"][0]["type"] == "query"


def test_entries_record_the_gate_that_judged_them(log):
    """The constitution and the gate are ordinary tracked files, outside the
    tamper-evidence regime they govern. A tightening is at least visible as
    retroactive disagreement (e000009); a silent LOOSENING was invisible —
    every entry stays internally consistent while 'valid' shifts beneath it
    (third review, 2026-08-23). Each entry now carries the ruleset that
    admitted it."""
    from varve import validate
    e = store.append(log, {"kind": "hunch", "title": "t", "body": "b"})
    assert e["gate"]["validator"] == validate.ruleset_id(log)["validator"]


def test_gate_stamp_is_reserved_from_the_author(log):
    e = store.append(log, {"kind": "hunch", "title": "t", "body": "b",
                           "gate": {"validator": "0" * 16}})
    assert e["gate"]["validator"] != "0" * 16


def test_a_ruleset_change_is_visible_in_the_history(log, monkeypatch):
    """The point of the stamp: a reader can SEE where the rules moved. Not a
    verification failure — gates get fixed — but never invisible again."""
    from varve import validate, views
    store.append(log, {"kind": "hunch", "title": "before", "body": "b"})
    monkeypatch.setattr(validate, "ruleset_id",
                        lambda root=None: {"validator": "deadbeefdeadbeef"})
    store.append(log, {"kind": "hunch", "title": "after", "body": "b"})
    rows = views.ruleset_history(log)
    assert rows[-1][0]["title"] == "after"
    assert rows[-1][2] == {"validator": "deadbeefdeadbeef"}
    assert store.verify(log) == []  # a rule change is not chain damage


def test_question_kind_needs_no_anchor_and_answers_must_point_at_one(log):
    q = store.append(log, {"kind": "question", "title": "what is unwitnessed?",
                           "body": "an open problem, as an object"})
    assert q["kind"] == "question"
    with pytest.raises(ValueError, match="answers"):
        store.append(log, {"kind": "hunch", "title": "t", "body": "b",
                           "answers": "e999999"})
    with pytest.raises(ValueError, match="question cannot answer"):
        store.append(log, {"kind": "question", "title": "t", "body": "b",
                           "answers": q["id"]})
    a = store.append(log, {"kind": "hunch", "title": "maybe", "body": "b",
                           "answers": q["id"]})
    assert a["answers"] == q["id"]


def test_survey_records_a_null_result_and_must_say_where_it_looked(log):
    """'I looked and found nothing' is a claim about the world, and the only
    thing a reader can check about an absence is where you searched — so a
    survey anchors like any other assertion (third review, 2026-08-23)."""
    with pytest.raises(ValueError, match="at least one anchor"):
        store.append(log, {"kind": "survey", "title": "swept the gate, nothing new",
                           "body": "ran every probe; all closed"})
    e = store.append(log, {"kind": "survey", "title": "swept the gate, nothing new",
                           "body": "ran every probe; all closed",
                           "anchors": [{"type": "query", "ref": "python workshop/gate-probe.py"}]})
    assert e["kind"] == "survey"


def test_a_survey_is_a_belief_and_can_be_corrected(log):
    from varve import views
    s = store.append(log, {"kind": "survey", "title": "nothing found", "body": "b",
                           "anchors": [{"type": "query", "ref": "grep -r TODO ."}]})
    assert any(e["id"] == s["id"] and status == "standing" for e, status in views.beliefs(log))
    store.append(log, {"kind": "errata", "title": "there was something", "body": "b",
                       "corrects": s["id"],
                       "anchors": [{"type": "entry", "ref": s["id"]}]})
    assert any(e["id"] == s["id"] and status.startswith("corrected") for e, status in views.beliefs(log))


def test_commitment_must_name_what_is_owed_and_by_when(log):
    """A promise made in one session and forgotten in the next is the failure a
    commitments ledger exists for. Nothing here can force a promise to be kept;
    it makes an unkept one countable, which is the most a log can do."""
    for bad in ({}, {"due": "soon", "owed_to": "x"}, {"due": "2027-02-31", "owed_to": "x"},
                {"due": "2027-01-01"}):
        with pytest.raises(ValueError, match="commitment"):
            store.append(log, dict({"kind": "commitment", "title": "t", "body": "b"}, **bad))
    c = store.append(log, {"kind": "commitment", "title": "ship it", "body": "b",
                           "due": "2027-01-01", "owed_to": "a reader"})
    assert c["due"] == "2027-01-01"


def test_due_and_owed_to_are_reserved_for_commitments(log):
    with pytest.raises(ValueError, match="reserved for kind commitment"):
        store.append(log, {"kind": "hunch", "title": "t", "body": "b", "due": "2027-01-01"})


def test_a_commitment_is_discharged_once(log):
    c = store.append(log, {"kind": "commitment", "title": "t", "body": "b",
                           "due": "2027-01-01", "owed_to": "x"})
    with pytest.raises(ValueError, match="discharges"):
        store.append(log, {"kind": "meta", "title": "t", "body": "b", "discharges": "e999999"})
    store.append(log, {"kind": "meta", "title": "done", "body": "b", "discharges": c["id"]})
    with pytest.raises(ValueError, match="already discharged"):
        store.append(log, {"kind": "meta", "title": "again", "body": "b", "discharges": c["id"]})


def test_display_bending_characters_are_refused(log):
    """A bidi override renders text the reader does not see stored; NUL and
    friends truncate in half the tools that will read this file. Rule 6 fails at
    the character level. Found by the second notebook (QQ1eF)."""
    for field, ch in (("title", chr(0x202E)), ("title", chr(0x2066)),
                      ("body", chr(0x00)), ("body", chr(0x07))):
        entry = {"kind": "hunch", "title": "t", "body": "b"}
        entry[field] = entry[field] + ch + "x"
        with pytest.raises(ValueError, match="U\\+"):
            store.append(log, entry)
    ok = store.append(log, {"kind": "hunch", "title": "tabs\tand\nnewlines are fine",
                            "body": "so is é and 中文"})
    assert "\t" in ok["title"]


def test_pointer_fields_must_be_strings(log):
    """A pointer is used as a dict key or set member by every view, so a
    non-string there is a TypeError in the reader rather than a rejection — and
    it took out the gate itself first, escaping as an unhandled exception past
    worker.py, which catches only ValueError. Found by the second notebook's
    fifth review (QQ1eF e000024, 2026-08-30)."""
    for field in ("corrects", "resolves", "discharges", "answers"):
        for bad in ([], {}, 5, None, ["e000001"]):
            with pytest.raises(ValueError, match="entry id string"):
                store.append(log, {"kind": "errata", "title": "t", "body": "b",
                                   field: bad,
                                   "anchors": [{"type": "entry", "ref": "e000001"}]})
