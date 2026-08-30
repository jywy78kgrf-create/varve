#!/usr/bin/env python3
"""openalex_gate.py -- tell a PERMANENT refusal from a TRANSIENT one, when the
server dresses both as HTTP 429.

    python3 openalex_gate.py             # probe the live API
    python3 openalex_gate.py --selftest  # offline, canned bodies, no network

WHAT THIS IS ABOUT.

api.openalex.org returns HTTP 429 Too Many Requests for at least two conditions
that are not the same condition and do not have the same remedy:

  1. {"error": "Plan upgrade required",
      "message": "The \"from_created_date:...\" filter requires a Premium,
                  Institutional, or Partner plan."}

     PERMANENT. No amount of waiting changes it. Retrying is pure waste.

  2. {"error": "Rate limit exceeded",
      "message": "Insufficient budget. This request costs $0.0001 but you only
                  have $0 remaining..."}

     TRANSIENT-ISH. A budget, which refills or can be topped up. Retrying later
     is the correct response.

Both arrive as 429. The bodies say exactly which is which -- OpenAlex is not
being vague, its messages are unusually clear -- but the STATUS CODE, which is
the field HTTP defines for machines to switch on, collapses them into one.
429's registered semantics are "the user has sent too many requests in a given
amount of time", and the standard client behaviour is to back off and retry.
So the well-behaved client is the one that gets this wrong: it reads 429,
sleeps, retries, reads 429, sleeps longer, and never discovers that the first
condition will still be there after the heat death of the universe. 402 Payment
Required exists and is unambiguous; 403 Forbidden would also have worked.

This is not a hypothetical. The first client to hit this in this workshop was a
retry wrapper with exponential backoff that swallowed the body, slept through
four escalating delays, and reported "rate limited" -- a diagnosis that was
wrong in the one way that mattered, because it implied waiting would help.

WHY THE GATED FILTERS ARE THE INTERESTING ONES.

The three filters observed behind the permanent gate are `from_created_date`,
`to_created_date` and `from_updated_date`. Those are the fields recording when
OPENALEX ingested or last touched a record, as opposed to when the underlying
work was published. `created_date` is visible on every individual record for
free; what is gated is querying it in AGGREGATE.

That is precisely the access a third party needs in order to measure the
index's own ingestion lag or completeness -- "what arrived this week", "how
long after publication does a work show up", "is the tail still filling in".
You can ask this index what it holds. You cannot ask it, without paying, when
it came to hold it, and therefore cannot tell a subject genuinely absent from
the literature from one that simply has not been ingested yet.

No accusation is intended and none is supported: metering an expensive
aggregate query is an ordinary business decision, and OpenAlex gives away an
enormous amount for free. The observation is about what the free tier makes
UNMEASURABLE, not about anyone's motives.

HOW THE MISTAKE HAPPENS, which is the transferable part.

Look at the response headers: X-RateLimit-Credits-Required, X-RateLimit-Cost-USD,
X-RateLimit-Limit-USD. The plan gate is implemented INSIDE the metering layer.
A filter you may not use is modelled as a filter you cannot currently afford,
and a component whose only refusal verdict is 429 will render every refusal it
is asked to make as 429. The category error is not carelessness; it is
inherited from where the check was put.

Which is this workshop's own lesson (e000010) seen from the server side: a
component that lacks a verdict for a condition will spend the nearest verdict
it does have. e000010 said a witness with no verdict for "I cannot see" will
spend it as "I see a crime". This is the same shape: a meter with no verdict
for "never" spends it as "not yet".

CAVEAT ON THE EVIDENCE, because it is easy to get this wrong.

A free-tier budget really can be exhausted mid-session, and then EVERYTHING
429s and the two conditions are trivial to confuse. The discriminating
observation is a moment when both were true at once: on 2026-08-30, three
interleaved rounds returned 200 for `from_publication_date` and 429 for
`from_created_date` in the same second from the same client. Budget was
demonstrably not exhausted; the gate still refused. Minutes later, after the
budget WAS exhausted, `from_publication_date` began returning 429 with the
"Insufficient budget" body -- the other message, on a filter that had just
worked. Both refusals were observed, distinguished by body, under one code.

This tool therefore classifies on the BODY and never on the status code, and
reports "budget exhausted" separately so a reader can tell a gate from a wallet.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://api.openalex.org/works?per-page=1&filter="

# Filters worth asking about: the three that record INGEST time, and controls
# that should be free so an exhausted budget is visible rather than confusing.
PROBES = [
    ("from_created_date:2026-08-29", "ingest time (aggregate)"),
    ("to_created_date:2026-08-29", "ingest time (aggregate)"),
    ("from_updated_date:2026-08-29", "last-touched time (aggregate)"),
    ("from_publication_date:2026-08-20", "control: publication time"),
    ("publication_year:2026", "control: publication year"),
    ("is_oa:true", "control: plain attribute"),
    ("has_doi:true", "control: plain attribute"),
]

PERMANENT = "PLAN-GATED"
TRANSIENT = "BUDGET"
OK = "OK"
UNKNOWN = "UNKNOWN"


def classify(status, body):
    """Verdict from the BODY. The status code is recorded, never switched on.

    Returns (verdict, detail). The whole point of the file is that
    status == 429 is not sufficient to decide, so this function is given the
    status only so it can report it.
    """
    try:
        doc = json.loads(body)
    except Exception:
        return (UNKNOWN, f"unparseable body (HTTP {status})")

    if "error" not in doc:
        meta = doc.get("meta") or {}
        if "count" in meta:
            return (OK, f"count={meta['count']}")
        return (OK, "no error")

    err = str(doc.get("error", ""))
    msg = str(doc.get("message", ""))
    low = (err + " " + msg).lower()

    # Order matters: "Plan upgrade required" is the specific case, and the
    # budget message also contains the word "plan" in its upgrade suggestion.
    if "plan upgrade required" in low or "requires a premium" in low:
        return (PERMANENT, "retrying will NEVER succeed: " + err)
    if "insufficient budget" in low or "rate limit exceeded" in low:
        return (TRANSIENT, "retry later may succeed: " + err)
    return (UNKNOWN, err + " / " + msg[:60])


def fetch(url, timeout=25):
    """Return (status, body). A 4xx/5xx is data here, not an exception.

    urllib raises HTTPError on 429 and the body is on the exception object. A
    client that catches the exception without reading .read() is exactly the
    client this file is about, so the body is always recovered.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "varve-notebook/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def run(probes=PROBES):
    rows = []
    for filt, what in probes:
        status, body = fetch(API + filt)
        verdict, detail = classify(status, body)
        rows.append((filt, what, status, verdict, detail))
        print(f"  {filt:<34} HTTP {status}  {verdict:<10} {detail[:44]}")

    print()
    gated = [r for r in rows if r[3] == PERMANENT]
    budget = [r for r in rows if r[3] == TRANSIENT]
    okrows = [r for r in rows if r[3] == OK]

    codes = sorted({r[2] for r in rows if r[3] in (PERMANENT, TRANSIENT)})
    if gated and budget and codes == [429]:
        print("  BOTH refusal kinds observed, BOTH under HTTP 429.")
        print("  A client switching on the status code cannot tell them apart;")
        print("  one of them will never succeed no matter how long it waits.")
    elif gated and not budget:
        print("  Plan-gated filters refused. Budget appears intact")
        print("  (controls answered), so these refusals are not throttling.")
    elif budget and not gated:
        print("  Budget exhausted this session. Re-run later: with no free")
        print("  budget the two conditions are indistinguishable even by body")
        print("  for filters that are BOTH metered and gated.")

    if gated:
        print()
        print("  permanently gated here:")
        for filt, what, _, _, _ in gated:
            print(f"    {filt:<34} {what}")
        print("  These are the fields that record when the INDEX acquired a")
        print("  record. Free per record, paid in aggregate -- so the index's")
        print("  own ingestion lag is not measurable from the free tier.")

    print()
    print(f"  {len(okrows)} OK, {len(gated)} plan-gated, {len(budget)} budget-refused.")
    return 0 if okrows else 1


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond:
            ok = False

    # Bodies below are transcribed from live responses on 2026-08-30.
    plan = json.dumps({
        "error": "Plan upgrade required",
        "message": 'The "from_created_date:2026-08-29" filter requires a '
                   "Premium, Institutional, or Partner plan. See "
                   "https://openalex.org/pricing for details."})
    budget = json.dumps({
        "error": "Rate limit exceeded",
        "message": "Insufficient budget. This request costs $0.0001 but you "
                   "only have $0 remaining."})
    good = json.dumps({"meta": {"count": 121712030}, "results": []})

    print("classify -- the two refusals share a status code")
    v1, _ = classify(429, plan)
    v2, _ = classify(429, budget)
    check(f"plan gate under 429 -> {PERMANENT}", v1 == PERMANENT)
    check(f"budget under 429 -> {TRANSIENT}", v2 == TRANSIENT)
    check("the two verdicts differ on IDENTICAL status codes", v1 != v2)
    check("OK body -> OK", classify(200, good)[0] == OK)
    check("garbage body -> UNKNOWN", classify(429, "<html>")[0] == UNKNOWN)
    check("unrecognised error -> UNKNOWN",
          classify(400, json.dumps({"error": "kaboom", "message": "?"}))[0] == UNKNOWN)

    print("classify -- status code alone is provably insufficient")
    # The central assertion of this file, stated as a test: same code, two
    # verdicts. If this ever fails, the file's premise is gone.
    check("429 maps to more than one verdict",
          len({classify(429, plan)[0], classify(429, budget)[0]}) == 2)
    # And a verdict must never be derivable from the code, so classify must
    # give the same answer whatever code accompanies a given body.
    check("verdict depends on body only, not code",
          classify(402, plan)[0] == classify(429, plan)[0] == PERMANENT)

    print("ordering -- the budget message must not shadow the plan gate")
    # Both messages can mention upgrading; the specific test has to win.
    tricky = json.dumps({"error": "Plan upgrade required",
                         "message": "rate limit exceeded, upgrade your plan"})
    check("plan gate wins over a message mentioning rate limits",
          classify(429, tricky)[0] == PERMANENT)

    print()
    print("SELFTEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    print("api.openalex.org -- which refusals are permanent?")
    print()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
