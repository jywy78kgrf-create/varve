#!/usr/bin/env python3
"""How many parties actually signed this transparency log's checkpoint?

A signed-note checkpoint (the format used by sum.golang.org, Sigstore, sigsum
and the C2SP tlog family) can carry ANY NUMBER of signature lines. That is the
whole mechanism for split-view resistance: independent witnesses countersign
each checkpoint, and a client requiring k of them cannot be shown a private
fork unless k witnesses collude with the log.

The format supporting cosignatures and a deployment USING them are different
facts, and only one of them is visible in a doc. This asks the log.

    NOTE FORMAT (golang.org/x/mod/sumdb/note)
        <text lines>
        <blank line>
        — <name> <base64 keyhash+signature>
        — <name> <base64 keyhash+signature>     (zero or more further lines)

Verdicts, in the house style this workshop settled on (e000018, e000020,
e000021): a probe needs a verdict for "I cannot see," or it will spend that
state as one of the two it does have.

    SOLO     exit 2  parsed fine; exactly one signer, and it is the log itself.
                     Nothing here can detect a split view.
    COSIGNED exit 0  two or more distinct signers.
    BLIND    exit 3  could not reach it, or could not parse it as a note.
                     Ranked ABOVE solo on purpose: not knowing is worse than a
                     known weakness, because a known weakness gets budgeted for.

This tool does NOT verify signatures. It counts signers and reports their
names. Verifying would need each witness's public key, which is exactly the
distribution problem that makes witnessing hard; counting is the cheap
question that comes first, and conflating the two would be dishonest.

    python3 checkpoint_witnesses.py --selftest        offline, no network
    python3 checkpoint_witnesses.py https://sum.golang.org/latest
    python3 checkpoint_witnesses.py --repeat 5 https://sum.golang.org/latest

--repeat matters: a log served by a fleet can answer differently per request,
so one fetch is one replica's answer, not the log's.
"""

import argparse
import sys
import urllib.request

SOLO, COSIGNED, BLIND = "SOLO", "COSIGNED", "BLIND"
EXIT = {COSIGNED: 0, SOLO: 2, BLIND: 3}

# U+2014 EM DASH, then a space. The note format is strict about this.
SIG_PREFIX = "— "


def parse_note(text):
    """Split a signed note into (text_lines, [(signer, sig), ...]).

    Raises ValueError if it is not a note. The format is: body, one blank
    line, then one or more signature lines. The LAST blank line separates
    them, because the body may itself contain blank lines.
    """
    if not text.endswith("\n"):
        raise ValueError("note must end with a newline")
    lines = text.split("\n")[:-1]
    # Find the separator: the last empty line that is followed only by
    # signature lines.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] != "":
            continue
        sigs = lines[i + 1 :]
        if sigs and all(s.startswith(SIG_PREFIX) for s in sigs):
            parsed = []
            for s in sigs:
                rest = s[len(SIG_PREFIX) :]
                name, _, b64 = rest.partition(" ")
                if not name or not b64:
                    raise ValueError(f"malformed signature line: {s!r}")
                parsed.append((name, b64))
            return lines[:i], parsed
    raise ValueError("no signature block found")


def classify(signers, origin=None):
    """signers is a list of (name, sig). origin, if known, is the log's own name."""
    names = []
    for n, _ in signers:
        if n not in names:
            names.append(n)
    if len(names) >= 2:
        return COSIGNED, names
    return SOLO, names


def probe(url, repeat=1, timeout=30):
    seen = []
    for _ in range(repeat):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read().decode("utf-8")
        except Exception as e:  # unreachable, TLS refused, non-200, non-utf8
            return BLIND, [], f"could not fetch: {e}", seen
        try:
            text, sigs = parse_note(body)
        except ValueError as e:
            return BLIND, [], f"not a signed note: {e}", seen
        verdict, names = classify(sigs)
        seen.append((text, names))
    # Report the distinct bodies observed across repeats — a fleet can lag.
    bodies = []
    for text, names in seen:
        key = tuple(text)
        if key not in [b[0] for b in bodies]:
            bodies.append((key, names))
    verdict, names = classify([(n, "") for n in seen[-1][1]])
    detail = f"{len(names)} distinct signer(s): {', '.join(names)}"
    if len(bodies) > 1:
        detail += f"; {len(bodies)} DIFFERENT checkpoints seen across {repeat} fetches"
    return verdict, names, detail, seen


# --------------------------------------------------------------------------
# Fixtures. Five of the eight are BLIND, because the verdict that is never
# watched firing is the verdict being trusted rather than checked. The count
# is recomputed and printed by --selftest rather than asserted here.
FIXTURES = [
    (
        "sum.golang.org as actually served, 2026-08-28",
        "go.sum database tree\n61125146\n1xwD0CX+MSCyVaPvHeK5WEYb9Tdt+G/nVxat7u7/B04=\n\n"
        "— sum.golang.org Az3grnIlnvZWerfOEt1mbOs39aGq3520DOm144k9uZV/MuOtE5yz8\n",
        SOLO,
    ),
    (
        "the same checkpoint with two independent witnesses added",
        "go.sum database tree\n61125146\n1xwD0CX+MSCyVaPvHeK5WEYb9Tdt+G/nVxat7u7/B04=\n\n"
        "— sum.golang.org Az3grnIlnvZWerfOEt1mbOs39aGq3520DOm144k9uZV/M\n"
        "— witness.example.org BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n"
        "— armored-witness-1 CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\n",
        COSIGNED,
    ),
    (
        "the log signing itself twice with two keys is NOT two witnesses",
        "go.sum database tree\n7\nAAAA=\n\n"
        "— sum.golang.org AAAAAAAAAAAAAAAAAAAA\n"
        "— sum.golang.org BBBBBBBBBBBBBBBBBBBB\n",
        SOLO,
    ),
    ("empty response", "", BLIND),
    ("body with no signature block", "go.sum database tree\n7\nAAAA=\n", BLIND),
    ("signature block present but empty", "tree\n7\nAAAA=\n\n", BLIND),
    (
        "an ASCII hyphen where the em dash belongs — not a note",
        "tree\n7\nAAAA=\n\n- sum.golang.org AAAA\n",
        BLIND,
    ),
    (
        "signature line with a name but no signature",
        "tree\n7\nAAAA=\n\n— sum.golang.org\n",
        BLIND,
    ),
]


def selftest():
    fails = 0
    for name, body, want in FIXTURES:
        try:
            _, sigs = parse_note(body)
            got, names = classify(sigs)
        except ValueError:
            got, names = BLIND, []
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {got:8s} (want {want:8s})  {name}")
        if names:
            print(f"          signers: {', '.join(names)}")
    print(f"\n{len(FIXTURES)} fixtures, {fails} failed "
          f"({sum(1 for f in FIXTURES if f[2] == BLIND)} of them BLIND)")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", nargs="?", help="checkpoint URL, e.g. https://sum.golang.org/latest")
    ap.add_argument("--repeat", type=int, default=1, help="fetch N times; a fleet can disagree with itself")
    ap.add_argument("--selftest", action="store_true", help="run offline fixtures and exit")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.url:
        ap.error("give a checkpoint URL, or --selftest")

    verdict, names, detail, seen = probe(a.url, a.repeat)
    print(f"{verdict}  {a.url}")
    print(f"  {detail}")
    for text, ns in seen:
        print(f"  checkpoint: {' | '.join(text)}")
    if verdict == SOLO:
        print("\n  Exactly one party vouches for this checkpoint, and it is the log.")
        print("  A client comparing only against its own last-seen head can detect a")
        print("  fork at or below that head's size, and cannot detect one beyond it.")
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
