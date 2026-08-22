# The varve constitution

Six rules for a memory that deserves trust. An instance of varve is a **log**;
these rules are what make a log a *varve* log. The tooling in this repository
enforces what can be enforced mechanically (rules 1–4 and 6); rule 5 is a
property of how you host the log, and the README tells you how to keep it.

1. **Append-only.** A past entry is never edited or deleted. A correction is a
   new entry (`kind: errata`) that names the entry it corrects. The record of
   being wrong is part of the record.

2. **Anchored or labeled.** An entry that states something about the world
   (`observation`, `resolution`) must carry at least one anchor — a URL, file
   path, query, entry id, or content hash a stranger could follow to check the
   claim. An unanchored impression is welcome, but it must wear its label:
   `hunch` or `hypothesis`. Confidence is not provenance.

3. **Tamper-evident.** Every entry contains the hash of the entry before it,
   and its own hash covers its full content. Any edit anywhere in history
   breaks the chain from that point forward, and `varve verify` will say so.

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
