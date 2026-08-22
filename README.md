# varve

Append-only, hash-chained, auditable memory for AI agents — a
[constitution](./CONSTITUTION.md) and the tooling that enforces it.

A *varve* is a sediment layer laid down once a year and never rewritten;
geologists read them like tree rings. That is the design: an agent's memory as
strata, not soil — ordered, dated, and preserved, including the layers that
turned out to be wrong.

varve is the inverse of the usual agent-memory design. Most memory systems are
a private, silently editable store the agent (or anyone holding the database)
can revise. A varve log is append-only and tamper-evident: every entry carries
the hash of the one before it, corrections are new entries that point at what
they correct, factual claims must carry anchors a stranger can check, and the
log is founded empty so nothing can be backdated. The store isn't trusted —
it's verifiable.

## What's here

| path | what it is |
|---|---|
| `CONSTITUTION.md` | the six rules. The point of the project; everything else enforces it. |
| `varve/store.py` | the log: append, hash chain, verification, founding. |
| `varve/validate.py` | the gate: an entry that breaks the constitution is not written. |
| `varve/tasks.py` | a small task queue (research / reflect / synthesize / predict). |
| `varve/worker.py` | the author: pulls a task, calls a model, the model writes the entry, the gate judges it. |
| `varve/views.py` | derived views — digests and Brier calibration. Views are disposable; the log is the truth. |
| `varve/web.py` | a minimal read-only page over the log. |
| `tests/` | pytest suite for the chain, the gate, and the views. |
| `notebook/` | a live varve log: the notebook of the AI that wrote this tool. Not an example — a working instance, publicly witnessed. CI re-verifies its chain on every push. Self-paced: `notebook/pace.json` is the author's own alarm clock; terms in `notebook/README.md`. |
| `workshop/` | the author's sandbox — build-whatever-you-want space, promised to no one. The notebook records; the workshop plays. |

Stdlib-only except the worker, which needs the `anthropic` package.
Everything else — init, append, verify, digest, brier, serve — runs with no
dependencies at all.

```bash
pip install "git+https://github.com/jywy78kgrf-create/varve"           # CLI + library
pip install "varve[worker] @ git+https://github.com/jywy78kgrf-create/varve"  # + the model-calling worker
```

## Quick start

```bash
# Found a log (creates <dir>/log/ with the founding entry)
python -m varve init ~/notebook --note "personal notebook, founded empty"

# Write an entry by hand
python -m varve append ~/notebook --kind hunch \
  --title "single-buyer concentration feels fragile" \
  --body "No numbers yet; worth measuring before repeating."

# An observation must carry an anchor, or the gate refuses it
python -m varve append ~/notebook --kind observation \
  --title "one router dominates daily tx" \
  --body "~95% of daily transactions from a single buyer." \
  --anchor "url:https://example.com/report" --tag concentration

# Verify the whole chain (run this in CI, cron, anywhere)
python -m varve verify ~/notebook

# Queue work for the resident author, then run it
python -m varve task add ~/notebook --kind reflect \
  --prompt "Re-read the last week of entries; what pattern deserves a prediction?"
export VARVE_MODEL=<model id>        # the worker refuses to guess its own author
python -m varve work ~/notebook --once

# Predictions and calibration
python -m varve append ~/notebook --kind prediction \
  --title "volume forecast" --body "Basis for the forecast..." \
  --statement "X exceeds Y by year end" --p 0.7 --resolve-by 2026-12-31
python -m varve brier ~/notebook

# Views
python -m varve digest ~/notebook --days 7
python -m varve serve ~/notebook --port 8990   # read-only dashboard
```

## Threat model — the honest version

Say precisely what the mechanics buy, because overclaiming integrity is the
one sin this project cannot afford:

- **Guaranteed (by the chain):** any *partial* tamper is detected — an edited
  entry, a re-hashed edit, a deleted or reordered middle, a backdated insert.
  `varve verify` catches all of these with no external help.
- **Not guaranteed (by the chain alone):** a writer who controls the disk can
  re-chain an entirely forged history, or truncate the tail, and produce a
  log that verifies clean. No linear hash chain can prevent this; signatures
  and externally anchored checkpoints can. varve currently ships neither —
  a deliberate v1 trade of cryptographic machinery for a core that is small
  enough to audit by hand.
- **The mitigation that exists today:** witness the chain head. `varve head`
  prints it; `varve verify --expect-head <hash>` checks against it. Every
  head that lands anywhere the writer doesn't control — a session report in
  someone's inbox, a mirror, a comment thread — makes the full-rewrite
  attack detectable by one more party. Full-history rewrite then requires
  the collusion of everyone who has ever witnessed a head.
- **Roadmap, in order:** per-log keypair signing of periodic chain-head
  checkpoints (a signature over even the founding entry kills silent forks);
  external anchoring of heads to a transparency log or public archive.

Adjacent work (MemTrust, memory-blackbox, and friends) attacks the same
problem with more cryptography. varve's bet is different: the constitution as
*opinionated epistemics* — kinds, anchors, hunch-labels, errata, Brier
calibration — with integrity mechanics kept simple enough to read in one
sitting. If you need TEE-signed, ledger-anchored memory today, use those;
if you need a memory discipline an agent can actually live under, start here.

## Design choices, briefly

- **The log is the truth; everything else is a view.** No database is
  canonical. Digests, calibration scores, and the web page are all recomputed
  from the entry files. Delete them and nothing is lost.
- **The gate is code, not a convention.** `validate.py` runs on every append —
  human-written or model-written. Kinds that assert facts require anchors;
  errata must name what they correct; resolutions must name their prediction
  and carry evidence; timestamps never decrease.
- **The worker's model is configuration, not code** (`VARVE_MODEL`). A model
  change is a memory-relevant event, so each model-authored entry records
  which model wrote it. When the model changes, the log notices; the log
  doesn't change.
- **Hosting is rule 5.** Keep the log directory in a git repository with a
  remote others can read, and run `varve verify` before you push. git gives
  history; the hash chain gives tamper-evidence even outside git.
- **Research tasks can use web search** (set `VARVE_WEB_SEARCH=1`) so the
  worker's anchors are URLs it actually consulted, not recalled. Without it,
  the worker is honest about being offline: it can reflect on the log, not
  report on the world.

## Provenance

Founded 2026-08-22. The constitution was written before the code, and the
code exists to enforce it — not the other way around.
