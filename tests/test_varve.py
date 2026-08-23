"""Regression tests for the gate and the verifier.

stdlib only, like everything but the worker: `python -m unittest discover tests`.

Each test in ChainIntegrity names the constitutional rule it defends. The
three marked "second review, 2026-08-23" are regressions for defects found by
probing the gate rather than reading it — the probes are reproduced here so a
future instance can re-run them instead of trusting this note.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from varve import store, validate, views  # noqa: E402


class LogTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="varve-test-")
        store.init(self.root, note="test log")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def append(self, **fields):
        fields.setdefault("kind", "hunch")
        fields.setdefault("title", "t")
        fields.setdefault("body", "b")
        return store.append(self.root, fields)

    def assertRejected(self, substring, **fields):
        with self.assertRaises(ValueError) as cm:
            self.append(**fields)
        self.assertIn(substring, str(cm.exception))


class ChainIntegrity(LogTestCase):
    def test_founding_is_first_and_unchained(self):
        entries = store.read_log(self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["seq"], 1)
        self.assertEqual(entries[0]["prev"], "")
        self.assertEqual(store.verify(self.root), [])

    def test_refuses_to_found_twice(self):
        with self.assertRaises(ValueError):
            store.init(self.root)

    def test_appends_chain_and_verify_walks_them(self):
        a = self.append(title="one")
        b = self.append(title="two")
        self.assertEqual(b["prev"], a["hash"])
        self.assertEqual(store.verify(self.root), [])
        self.assertEqual(store.head(self.root), (3, b["hash"]))

    def test_edited_entry_breaks_the_chain(self):
        """Rule 3: any partial edit is detectable."""
        self.append(title="original")
        path = os.path.join(self.root, "log", "000002.json")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw.replace('"original"', '"tampered"'))
        problems = store.verify(self.root)
        self.assertTrue(any("content hash mismatch" in p for p in problems))

    def test_author_cannot_mint_its_own_timestamp(self):
        """Rule 4, second review 2026-08-23.

        worker.py hands the model's raw JSON to store.append, so an
        author-supplied 'ts' was an author choosing its own position in
        history. Post-dating was the sharp end: rule 1 forbids removing the
        offending entry and no later entry may carry an earlier timestamp, so
        a single future date jammed the log permanently.
        """
        e = self.append(title="tries to post-date", ts="2099-01-01T00:00:00Z")
        self.assertNotEqual(e["ts"], "2099-01-01T00:00:00Z")
        self.assertRegex(e["ts"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
        # the log is still writable, which is the property that was lost
        self.append(title="ordinary entry after it")
        self.assertEqual(store.verify(self.root), [])

    def test_gate_rejects_a_malformed_timestamp(self):
        """Second review 2026-08-23: a non-string ts skipped every check."""
        problems = validate.check(
            {"kind": "hunch", "title": "t", "body": "b", "ts": 12345},
            store.read_log(self.root),
        )
        self.assertTrue(any("ts must look like" in p for p in problems))

    def test_verify_reports_malformed_data_instead_of_crashing(self):
        """Second review 2026-08-23: verify raised TypeError comparing an int
        ts against its predecessor. A verifier that crashes on a damaged log
        has failed at its one job — diagnosis."""
        entry = dict(store.read_log(self.root)[0])
        entry.update(seq=2, id="e000002", ts=12345, prev=entry["hash"])
        entry["hash"] = store.entry_hash(entry)
        store._write(self.root, entry)
        problems = store.verify(self.root)  # must not raise
        self.assertTrue(any("malformed timestamp" in p for p in problems))

    def test_corrupt_entry_file_is_reported_by_name(self):
        """Second review 2026-08-23: an unparseable file in log/ raised a bare
        JSONDecodeError naming a column of a file it never identified."""
        with open(os.path.join(self.root, "log", "000002.json"), "w") as f:
            f.write("{not json")
        problems = store.verify(self.root)  # must not raise
        self.assertTrue(any("000002.json is not valid JSON" in p for p in problems))

    def test_verify_catches_a_truncated_tail_only_with_a_witnessed_head(self):
        """Rule 5: the chain alone cannot see truncation."""
        self.append(title="one")
        witnessed = self.append(title="two")["hash"]
        os.remove(os.path.join(self.root, "log", "000003.json"))
        self.assertEqual(store.verify(self.root), [])  # chain alone: silent
        problems = store.verify(self.root, expect_head=witnessed)
        self.assertTrue(any("witnessed head" in p for p in problems))


class Gate(LogTestCase):
    def test_kind_must_be_known(self):
        self.assertRejected("kind must be one of", kind="musing")

    def test_title_and_body_are_required(self):
        self.assertRejected("title is required", title="  ")
        self.assertRejected("body is required", body="")

    def test_observation_requires_an_anchor(self):
        """Rule 2: assert about the world, or wear the label."""
        self.assertRejected("must carry at least one anchor", kind="observation")
        self.append(kind="hunch")  # the labeled kind needs none

    def test_url_anchor_must_be_a_url(self):
        self.assertRejected(
            "url anchor must start with", kind="observation",
            anchors=[{"type": "url", "ref": "example.com"}])

    def test_entry_anchor_must_exist(self):
        self.assertRejected(
            "does not exist in this log", kind="observation",
            anchors=[{"type": "entry", "ref": "e999999"}])

    def test_file_anchor_must_resolve(self):
        """Second review 2026-08-23: 'entry' anchors were existence-checked
        from the start, 'file' anchors only had to be non-empty, though both
        are checkable locally at write time."""
        self.assertRejected(
            "file anchor does not resolve", kind="observation",
            anchors=[{"type": "file", "ref": "no/such/file.py"}])

    def test_file_anchor_accepts_a_real_path_with_a_line_reference(self):
        open(os.path.join(self.root, "real.py"), "w").close()
        self.append(kind="observation", anchors=[{"type": "file", "ref": "real.py"}])
        self.append(kind="observation", anchors=[{"type": "file", "ref": "real.py:12"}])
        self.append(kind="observation", anchors=[{"type": "file", "ref": "real.py:12-30"}])
        self.assertEqual(store.verify(self.root), [])

    def test_file_anchor_cannot_escape_the_log_root(self):
        """A '../' ref can resolve on the author's disk while naming nothing a
        reader who cloned the repository would have."""
        self.assertRejected(
            "file anchor does not resolve", kind="observation",
            anchors=[{"type": "file", "ref": "../../../etc/passwd"}])

    def test_file_anchor_unchecked_without_a_root(self):
        """A caller with no root must not have its anchors wrongly rejected."""
        entries = store.read_log(self.root)
        problems = validate.check(
            {"kind": "observation", "title": "t", "body": "b",
             "ts": entries[-1]["ts"],
             "anchors": [{"type": "file", "ref": "no/such/file.py"}]},
            entries)
        self.assertEqual(problems, [])

    def test_errata_must_name_a_real_entry(self):
        """Rule 1: a correction is a new entry, never an edit."""
        self.assertRejected("must name an existing entry id", kind="errata", corrects="e999999")
        target = self.append(kind="hunch", title="wrong thing")
        self.append(kind="errata", title="fix", corrects=target["id"])

    def test_corrects_is_reserved_for_errata(self):
        self.assertRejected("reserved for kind errata", kind="hunch", corrects="e000001")

    def test_prediction_needs_a_falsifiable_shape(self):
        self.assertRejected("prediction needs prediction.statement", kind="prediction",
                            prediction={"p": 0.6, "resolve_by": "2026-12-01"})
        self.assertRejected("strictly between 0 and 1", kind="prediction",
                            prediction={"statement": "x", "p": 1.0, "resolve_by": "2026-12-01"})
        self.assertRejected("resolve_by", kind="prediction",
                            prediction={"statement": "x", "p": 0.6})

    def test_a_prediction_resolves_once(self):
        pred = self.append(kind="prediction", title="p",
                           prediction={"statement": "x", "p": 0.6, "resolve_by": "2026-12-01"})
        self.append(kind="resolution", title="r", resolves=pred["id"], outcome=True,
                    anchors=[{"type": "entry", "ref": pred["id"]}])
        self.assertRejected("already resolved", kind="resolution", resolves=pred["id"],
                            outcome=False, anchors=[{"type": "entry", "ref": pred["id"]}])

    def test_malformed_prediction_is_rejected_not_crashed(self):
        """Second review 2026-08-23: a non-dict 'prediction' raised
        AttributeError. worker.py catches only ValueError, so a malformed
        model field crashed the run instead of returning as gate feedback."""
        self.assertRejected("prediction must be an object", kind="prediction",
                            prediction="not a dict")

    def test_unserialisable_entry_is_rejected_not_crashed(self):
        """Second review 2026-08-23: entry_hash runs after the gate, so a
        non-JSON value raised TypeError out of canonical(). Nothing reached
        the disk, but worker.py catches only ValueError, so the run died."""
        self.assertRejected("must be JSON-serialisable", extra={1, 2})
        self.assertEqual(len(store.read_log(self.root)), 1)

    def test_tags_must_be_a_list_of_strings(self):
        self.assertRejected("tags must be a list of strings", tags="nope")
        self.assertRejected("tags must be a list of strings", tags=[1, 2])

    def test_reserved_fields_cannot_be_supplied(self):
        e = self.append(seq=99, id="e000099", prev="deadbeef", hash="cafe")
        self.assertEqual(e["seq"], 2)
        self.assertEqual(e["id"], "e000002")
        self.assertEqual(e["hash"], store.entry_hash(e))


class Views(LogTestCase):
    def test_beliefs_marks_corrected_claims(self):
        claim = self.append(kind="hunch", title="a claim")
        rows = dict((e["id"], s) for e, s in views.beliefs(self.root))
        self.assertEqual(rows[claim["id"]], "standing")
        fix = self.append(kind="errata", title="not so", corrects=claim["id"])
        rows = dict((e["id"], s) for e, s in views.beliefs(self.root))
        self.assertEqual(rows[claim["id"]], "corrected by " + fix["id"])

    def test_brier_scores_resolved_predictions(self):
        pred = self.append(kind="prediction", title="p",
                           prediction={"statement": "x", "p": 1.0 - 0.75, "resolve_by": "2026-12-01"})
        self.append(kind="resolution", title="r", resolves=pred["id"], outcome=True,
                    anchors=[{"type": "entry", "ref": pred["id"]}])
        score, n, _ = views.brier(self.root)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(score, 0.5625)

    def test_digest_runs_over_a_real_log(self):
        self.append(kind="hunch", title="something")
        self.assertIn("varve digest", views.digest(self.root, days=30))

    def test_views_survive_a_damaged_entry(self):
        """Second review 2026-08-23: views are how a reader inspects a log,
        including a damaged one, so they must not KeyError on it. An entry
        with no 'kind' needs disk access to create — rule 5's adversary, not
        the gate's — but that is exactly when someone reaches for a view."""
        entry = dict(store.read_log(self.root)[-1])
        entry.update(seq=2, id="e000002", prev=entry["hash"])
        entry.pop("kind", None)
        entry["hash"] = store.entry_hash(entry)
        store._write(self.root, entry)
        views.beliefs(self.root)   # must not raise
        views.digest(self.root, days=30)


if __name__ == "__main__":
    unittest.main()
