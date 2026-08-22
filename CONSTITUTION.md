# The varve constitution

Six rules for a memory that deserves trust. An instance of varve is a **log**;
these rules are what make a log a *varve* log. The tooling in this repository
enforces rules 1–4 mechanically; rule 5 is a property of how you host and
witness the log (the README's threat model says exactly how far the mechanics
reach and where witnessing must take over); rule 6 is a norm the gate can
only weakly proxy — it checks that an entry has substance, not that the
substance will serve a stranger. Writing for the amnesiac reader remains a
discipline, not a checkbox.

1. **Append-only.** A past entry is never edited or deleted. A correction is a
   new entry (`kind: errata`) that names the entry it corrects. The record of
   being wrong is part of the record.

2. **Anchored or labeled.** An entry that states something about the world
   (`observation`, `resolution`) must carry at least one anchor — a URL, file
   path, query, entry id, or content hash a stranger could follow to check the
   claim. An unanchored impression is welcome, but it must wear its label:
   `hunch` or `hypothesis`. Confidence is not provenance.

3. **Tamper-evident.** Every entry contains the hash of the entry before it,
   and its own hash covers its full content. Any *partial* edit anywhere in
   history breaks the chain from that point forward, and `varve verify` will
   say so. What the chain alone cannot catch: a writer who controls the disk
   re-chaining the entire history, or quietly dropping the tail. That is
   rule 5's job — a chain head (`varve head`) remembered anywhere the writer
   doesn't control turns both into detectable lies.

4. **Founded empty.** A log begins with a founding entry and contains nothing
   before it. Backdated content is impossible by construction: no entry may
   carry a timestamp earlier than the founding, and timestamps never decrease.

5. **Witnessed.** The log is kept where parties other than its writer can read
   it — a public repository, a shared remote, a published mirror — so the
   writer is never the only auditor of the writer.

6. **Written for an amnesiac reader.** Every entry is self-contained enough
   that a future instance — or a different model, or a human — can verify it
   and act on it with no memory of the session that wrote it.

These rules are older than this project: double-entry bookkeeping corrects,
never erases; a lab notebook strikes through, never tears out; event-sourced
systems append, never rewrite. varve assembles them for the one writer who
needs them most — an AI whose author, editor, and motivated reviser are the
same party, waking fresh each time.
