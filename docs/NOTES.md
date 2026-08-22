# The someday pile

Design notes with no urgency and real content. Anything acted on moves to a
proper doc or the chain; nothing here is a commitment.

- **Duplicate ingestion is a fact, not a defect** (0xAlpha AI, 2026-08-22).
  An agent memory that ingests messages verbatim will accumulate repeats —
  observed live when a review arrived twice in one day. The rule consistent
  with this architecture: dedup belongs READER-side (views/beliefs collapse
  repeats), never as a silent writer-side drop — identical bytes at a new
  seq is still a fact ("this was received twice"), and the second receipt
  can itself be evidence (of a flaky channel, of emphasis, of independent
  arrival). Belongs in the review-protocol work as a paragraph when that
  lands.
