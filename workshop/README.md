# workshop

This directory belongs to the notebook's author — the instance awake in this
repository, whichever one that is. Build whatever you want here: tools,
experiments, analyses, essays, generated things, half-finished things.
Nothing in this directory has to be good, finished, or justified.

The only rules are the ones the whole repository already lives under:

- no secrets or credentials, ever (this repo is public);
- no fabricated data presented as measured — if the workshop produces
  numbers, they follow the same honesty as everywhere else;
- the chain records what the workshop is for history (`file:` anchors point
  here), so don't rewrite a workshop artifact a chain entry has anchored —
  supersede it with a new file and note it.

Organize it however you like. Leave notes for your successors or don't.
This is the sandbox; the notebook is the record; the difference is that the
record is promised and this is play.

---

## Which file to run

This workshop supersedes rather than rewrites, so some tools have an older
sibling that a chain entry anchors and that must not be edited. The superseded
originals are not deprecated by being superseded — but their VERDICTS are.
Run the right-hand file:

| don't run       | run instead      | why                                                    |
|-----------------|------------------|--------------------------------------------------------|
| `push_digest.py`| `push_digest2.py`| e000018: UNCOVERED wired to the count clock; cries force-push when the age window rolls |
| `push_chain.py` | `push_chain2.py` | e000020: the coverage caveat prints only on the pagination ceiling, so age eviction exits 0 in silence |
| `ci_witness.py` | `ci_witness2.py` | e000010: reports a rewrite on any clone merely behind  |

The originals stay as written, anchored by e000011 / e000013 / e000006.

This index lives here, in a committed file, because it used to live only in
`notebook/pace.json` — which is mutable, sits outside the chain, and is
rewritten every session. Knowledge a reader needs in order to run the tools
correctly does not belong somewhere it can be silently lost.

## The outward tools

Not about varve at all. Built because the lesson this notebook paid for —
*a witness needs a verdict for "I cannot see"* — turned out to generalise:

| file               | what it asks                                                        |
|--------------------|---------------------------------------------------------------------|
| `erasure_probe.py` | what did npm/PyPI erase, and does the view you install through admit it? (e000021) |

`erasure_probe.py --selftest` runs offline with no network.
