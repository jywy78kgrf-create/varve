# Witnessed checkpoints — design (not yet implemented)

Status: accepted design, awaiting a fresh implementation session. Source:
a design sketch and two review passes by **0xAlpha AI** (relayed by the
operator, 2026-08-22), amended where varve's constraints demanded it. The
review found the v1 threat-model gap, four mechanical defects, and the
rogue-policy hole in this document's own first draft — the design is as
much theirs as ours. The framing that governs everything
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
  universally-present binary instead of a Python package. Pins that make
  sshsig safe here rather than merely convenient:
  - **One namespace constant, enforced at both ends.** SSHSIG signs the
    message plus a namespace string (`-n`); the policy fixes
    `varve-checkpoint@<log-id>` and both signer and verifier reject
    anything else. That is the domain separation — a witness signing under
    a loose namespace makes checkpoint signatures replayable across
    protocols.
  - **The policy file IS the allowed_signers file.** One registry, not two:
    the pinned witness policy is byte-for-byte the OpenSSH
    `allowed_signers` database (witness_id ↔ key, `-I <witness_id>` at
    verify; newer OpenSSH can also restrict namespaces inside it). The
    artifact the daily report pins is the artifact the verifier consumes —
    no translation layer to drift.
  - **Hardware keys: touch is a feature.** `ed25519-sk` requires
    OpenSSH ≥ 8.2 and a FIDO2 token (≥ 8.0 for `-Y` itself; both floors go
    in the README). The default touch requirement is physical presence as
    part of witnessing — policy rejects keys created with
    `-O no-touch-required`. sk private keys are bound to the token: the
    keygen ceremony registers a backup token, or key loss is witness loss
    (rotation handles it, but plan for it).
  - **Explicit `scheme` per signature record** (`sshsig` | `kms-ed25519` |
    …): KMS signatures are not SSHSIG-shaped, so verifiers dispatch on a
    field instead of guessing. Per-witness pinning made structural.
- **Bootstrap for logs that already exist.** The sketch pins the initial
  witness set in the founding entry — right for new logs, impossible for
  founded ones (append-only forbids amending the founding). Adoption for an
  existing log is a rotation-from-empty: a `witness-policy` entry appended
  to the chain naming the set and k. But note the hole rotation-by-entry
  opens and the two rules that close it — without them, a disk-holder need
  not forge or truncate anything: they *append* a policy entry naming their
  own keys, keep chaining, and mint checkpoints that verify perfectly
  against the newest policy.
  1. **Policy transitions are quorum-gated, except the first.** A policy
     entry is valid iff co-signed by a quorum of the OUTGOING set (the TUF
     pattern). The sole exception is the initial policy-from-empty, which
     has no predecessor and leans entirely on external pinning — that
     asymmetry is the one moment trust enters from outside the chain, and
     it gets named rather than hidden: **the first-policy exception**.
  2. **The daily report pins the policy beside the head.** The report
     already states the chain head verbatim; it states the current policy
     entry's hash next to it. Appending a rogue policy then requires
     defeating every kept report — the same bar as rewriting the head.
     Cost: one template line.
  **Coverage boundary, stated plainly:** entries before the first witnessed
  checkpoint are protected by report-diffing only, forever. Cryptographic
  coverage begins at the first checkpoint's seq; nothing is retroactive.
- **Hash-only witnessing for private logs.** The high-water-mark check does
  not require entry *content*: linkage alone — each new (seq, prev, hash)
  tuple chaining from the stored head — pins the writer's commitments, and
  anyone who later obtains content can check it against the committed
  hashes. Two properties that keep this a mode rather than a schism:
  - **The checkpoint object is identical in both modes.** Privacy is a
    property of what the witness *ingests*, not what it *attests* — both
    sign the same `{log, seq, head}`. One format, one verifier path;
    tenants choose exposure without forking the ecosystem.
  - **The tiers are named honestly in the policy vocabulary:** a *linkage
    witness* enforces chaining and monotonicity (timestamps ride inside the
    hashed content) but cannot audit anchors or labels; only a *full
    witness* saw the entries. Tier-1 attestation must never imply
    tier-2 review.
  Public logs (this repo) use full witnesses; private/tenant logs can use
  linkage witnesses without disclosure. This matters if the multi-tenant
  idea ever ships.

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
