# Witnessed checkpoints — design (not yet implemented)

Status: accepted design, awaiting a fresh implementation session. Source: an
external reviewer's sketch (relayed by the operator, 2026-08-22), amended
where varve's constraints demanded it. The framing that governs everything
here, quoted because it is the cleanest statement of the roadmap:

> The constitution stays; the enforcement crosses the trust boundary.

The witness's high-water-mark check is rule 4 enforced outside the writer's
machine; the checkpoint quorum is rule 5 turned from hosting advice into
mechanism.

## Decisions (settled)

1. **Threshold in verification policy, not key math.** n independent plain
   signatures, verifier requires k. No DKG, no share ceremonies, no exotic
   dependency; witnesses are chosen for *institutional* independence
   (different people, accounts, jurisdictions) rather than mathematical
   independence of shares. FROST is a later optimization, probably never.
2. **Roles.** The author (agent) composes entries, computes heads, requests
   checkpoints, and commits returned signatures — it possesses zero private
   key material, ever; it may request attestation, never produce it.
   Witnesses (n = 3–5) hold keys the agent cannot reach and sign
   checkpoints only. Verifiers check chain, then ≥k signatures, then the
   witness set against pinned policy.
3. **The load-bearing rule: a witness signs the head it DERIVES, not the
   head it is shown.** Each witness retains its last attested (seq, head);
   on request it receives the entries since then, re-verifies they chain
   from its own stored head, confirms seq strictly advanced, then signs.
   Consequences: a rewritten history can never obtain a checkpoint (it
   diverges from every witness's high-water mark); truncation becomes
   self-evident (final seq below last witnessed seq); and varve's linear
   chain makes the check trivial — store last head, recompute — no Merkle
   consistency proofs needed. That simplicity is a structural advantage of
   the linear chain and should be stated, not apologized for.
4. **Sign checkpoints, not entries.** Witness attention (human or KMS) is
   the scarce resource. Triggers: time-based (the existing daily Routine)
   and event-driven (before consequential actions, at session end). The
   tail between checkpoints is unprotected; the exposure window equals the
   cadence — say so plainly wherever checkpoints are documented.
5. **Custody diversity beats count.** Concrete n=3, k=2: the operator's
   hardware-token key; a KMS key in an account that is not the agent's
   (ideally a different legal person); one external witness endpoint.
   Rekor's signed entry timestamp attaches as a fourth, weaker signal — it
   proves *when*, not *what-must-follow*. No two witnesses behind the same
   credentials, provider, or jurisdiction.
6. **Failure modes, stated honestly.** Witness down → the log continues
   unwitnessed, the checkpoint record shows the gap, verifiers warn:
   witnessing is liveness-optional, safety-critical. Up to k−1 colluding →
   contained. All n → identical to today's baseline, no worse. Key loss →
   rotation (below), with the keygen ceremony documented (offline machine
   or non-exportable KMS; the agent runtime never touches private bytes).

## Checkpoint object

```json
{"type": "checkpoint", "log": "<founding-entry-hash>", "seq": 42,
 "head": "<hash-at-seq-42>", "time": "2026-01-01T12:00:00Z"}
```

Canonicalized with the exact rules `store.canonical()` already implements
(sorted keys, no whitespace, ensure_ascii=False, UTF-8) — one
canonicalization for the whole project; drift between entry and checkpoint
canonicalization is where schemes like this historically rot. Stored as
`witness/checkpoints/NNNNNN.json`: the object plus `[{witness_id, sig}]`,
committed to the repo beside the log.

## Amendments to the sketch (varve-specific)

- **Signature scheme: SSH signatures (sshsig), not raw Ed25519 libraries.**
  Python's stdlib has no asymmetric crypto, and varve's core stays
  dependency-free. `ssh-keygen -Y sign` / `-Y verify` ships on effectively
  every machine varve runs on, does Ed25519 underneath, supports hardware
  tokens natively (ed25519-sk), and means witnesses use keys and custody
  practices they already have. The verifier dependency becomes a
  universally-present binary instead of a Python package. KMS-held keys can
  still participate: the checkpoint bytes are what's signed; any scheme the
  policy pins is acceptable per-witness (`witness_id` carries the type).
- **Bootstrap for logs that already exist.** The sketch pins the initial
  witness set in the founding entry — right for new logs, impossible for
  founded ones (append-only forbids amending the founding). Adoption for an
  existing log is a rotation-from-empty: a `witness-policy` entry appended
  to the chain naming the set and k, pinned out-of-band exactly like the
  founding hash — the daily report already states heads verbatim, and that
  channel pins the policy entry's hash the same way. Rotation thereafter:
  policy changes are entries co-signed by a quorum of the *outgoing* set
  (the TUF pattern) — trust evolves forward, append-only, same philosophy
  as the log.
- **Hash-only witnessing for private logs.** The high-water-mark check does
  not require entry *content*: linkage alone — each new (prev, hash) pair
  chaining from the stored head — pins the writer's commitments, and anyone
  who later obtains content can check it against the committed hashes. A
  witness fed only hash pairs cannot validate content, but fork-protection
  survives. Public logs (this repo) send full entries; private/tenant logs
  can witness without disclosure. This matters if the multi-tenant idea
  ever ships.

## Ship order

1. Checkpoint format + `varve witness keygen/init` (runs on the OPERATOR's
   machine, never in an agent session) + `varve verify --require-sigs k`.
   k=1 with the operator's SSH key already upgrades the inbox from lucky
   archive to cryptographic proof — phase 1 is worth shipping alone.
2. Witness endpoint (~200 lines, stdlib http.server): monotonicity
   enforcement + its own retained checkpoint log. Witness-side retention is
   the teeth.
3. Second/third witness onboarding; Rekor submission folded into the daily
   Routine; rotation entries.
4. FROST: probably never.

## The claim phase 1 buys (for the README, when it lands — not before)

Today: anyone can prove a rewrite by diffing against a report they happened
to keep. After: anyone holding one old checkpoint can prove it
cryptographically without trusting their inbox — and a rewrite must
additionally compromise k of n independently custodied keys and wipe the
witnesses' retained records.
