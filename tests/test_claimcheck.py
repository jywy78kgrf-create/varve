"""The claim checker gets checked. It would be a poor joke to ship a tool that
hunts unbacked promises and take its own behaviour on faith."""

import importlib.machinery
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def cc():
    path = os.path.join(ROOT, "tools", "claimcheck.py")
    loader = importlib.machinery.SourceFileLoader("claimcheck", path)
    spec = importlib.util.spec_from_loader("claimcheck", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_naked_claim_is_flagged(cc, tmp_path):
    f = _write(tmp_path, "a.md", "This design prevents any history rewrite.\n")
    flagged, anchored = cc.scan([f])
    assert [t for t, *_ in flagged] == ["CLAIM"] and anchored == 0


def test_a_claim_backed_in_the_same_paragraph_passes(cc, tmp_path):
    f = _write(tmp_path, "b.md",
               "This design prevents any history rewrite.\n"
               "Demonstrated in test_rewrite_is_caught and workshop/probe.py.\n")
    flagged, anchored = cc.scan([f])
    assert flagged == [] and anchored == 1


def test_a_stated_limit_counts_as_backing(cc, tmp_path):
    """A sentence admitting what it does NOT do is doing the thing this tool
    exists to encourage — chasing it would punish the honest wording."""
    f = _write(tmp_path, "c.md",
               "The chain detects any partial tamper.\n"
               "It does not detect a full re-chain by the disk's owner.\n")
    flagged, _ = cc.scan([f])
    assert flagged == []


def test_paragraphs_are_the_unit_not_lines(cc, tmp_path):
    """Line-by-line scanning flagged 96 claims in this repo, most of them
    fragments or already-anchored. Two paragraphs, one naked claim."""
    f = _write(tmp_path, "d.md",
               "The gate refuses malformed entries.\n"
               "See varve/validate.py for how.\n"
               "\n"
               "This can never fail.\n")
    flagged, anchored = cc.scan([f])
    assert len(flagged) == 1 and anchored == 1
    assert flagged[0][3].startswith("This can never fail")


def test_code_is_read_only_where_it_speaks_prose(cc, tmp_path):
    """A claim in a comment or docstring is a promise; an ordinary expression
    is not, even when it contains the word."""
    f = _write(tmp_path, "e.py",
               '"""This module prevents everything bad."""\n'
               'NEVER = "always never impossible"\n')
    flagged, _ = cc.scan([f])
    assert len(flagged) == 1 and "prevents everything bad" in flagged[0][3]


def test_it_runs_clean_on_a_file_with_no_claims(cc, tmp_path):
    f = _write(tmp_path, "f.md", "Here is a description of what the code does.\n")
    assert cc.scan([f]) == ([], 0)
