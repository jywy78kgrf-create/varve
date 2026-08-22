# Appending from an external worker

varve is a log format and a gate, not a runtime. Any agent, any model, any
platform can write into a varve log — the gate does not care who is writing,
which is the point. This note is for someone who already has a worker
(task queue, scheduled runs, digests) and wants its output to be auditable
instead of merely stored.

## Why bother, if your store already works

One reason, and it is specific rather than general: **a system that measures
itself cannot also be the only party able to edit the measurements.**

If your worker A/B tests its own reasoning strategies, logs which performed
better, and updates its defaults from that log — then the log is evidence,
and evidence a subject can silently revise is not evidence. Same for
calibration: a prediction score means nothing if the prediction's confidence
can be adjusted after the outcome is known. Append-only plus hash chaining
is what turns "I logged my failures" into a claim a stranger can check.

Everything else varve offers (kinds, anchors, errata, Brier) is downstream of
that.

## Two ways in

**1. Shell out.** Cheapest, and the version most workers should use:

```bash
pip install "git+https://github.com/jywy78kgrf-create/varve"
varve init /path/to/log --note "worker log, founded empty"

varve append /path/to/log --kind observation \
  --title "strategy B beat A on the retrieval set" \
  --body "…what you did, what you measured, what a reader needs to check it…" \
  --anchor "file:runs/2026-08-22/results.json" --tag experiment

varve verify /path/to/log      # exit 1 if the chain is broken
varve head   /path/to/log      # publish this; see below
```

The gate runs on every append and rejects what breaks the constitution —
your worker never has to remember the rules, only handle a non-zero exit.

**2. Write the files yourself.** The format is small enough to reimplement,
and doing so means you are not trusting varve's code either (see
`workshop/witness_replay.py` for a reader-side implementation in ~20 lines).
One JSON file per entry under `<root>/log/NNNNNN.json`, and:

- `hash` = SHA-256 over the entry's canonical bytes: JSON with sorted keys,
  no whitespace (`separators=(",", ":")`), `ensure_ascii=False`, UTF-8,
  **excluding the `hash` field itself**.
- `prev` = the previous entry's `hash`; empty string on the founding entry.
- `seq` is 1-based and gapless; `ts` never decreases.

Get canonicalization wrong and every verifier disagrees with you, so test
against `varve verify` before trusting your own writer.

## The parts a worker specifically needs

- **`prediction` / `resolution`.** A prediction carries
  `{statement, p, resolve_by}` with `0 < p < 1` — 0 and 1 are not forecasts.
  A resolution names the prediction id, carries `outcome: true|false`, and
  needs an anchor for the outcome. A prediction can be resolved once; a
  dispute is an errata, not a second resolution. `varve brier` then scores
  calibration over the whole history, and the score is meaningful precisely
  because no one could have edited the forecasts afterward.
- **`errata`.** Your worker will be wrong. It corrects by appending an
  errata entry naming the id it corrects — never by editing. `varve beliefs`
  then reports which claims still stand, so the next run does not act on a
  dead one.
- **`observation` vs `hunch`/`hypothesis`.** Anything asserting a fact needs
  at least one anchor a stranger could follow (`url:`, `file:`, `query:`,
  `sha256:`, `entry:`). If you cannot anchor it, label it a hunch. The gate
  enforces this, and it is the rule that keeps an autonomous worker's log
  from filling with confident residue.
- **Found empty.** Do not seed the store with synthetic history. A log whose
  founding entry is real is a log where backdating is impossible by
  construction; demo data in the real tables destroys that permanently.

## Publish the head

The chain detects any partial edit on its own. It cannot detect a writer who
re-chains the whole history — for that, `varve head` must land somewhere the
writer does not control (a run report in an inbox, a mirror, a transparency
log). Anyone holding an old head can then run `witness_replay.py` and prove a
rewrite. Read `README.md`'s threat model for what that does and does not buy,
and `docs/DESIGN-witnessing.md` for the signed-checkpoint design that closes
the rest.

If your worker emits a digest, put the head in it. It costs one line and it
is the entire difference between a log people trust and a log people can
check.
