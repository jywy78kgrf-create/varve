"""The author: pulls a task, calls a model, the model writes the entry,
the gate judges it.

Deliberate asymmetry with most memory systems: there is no extraction layer
paraphrasing task output into "memories". The model authors the entry itself,
anchors included, and validate.py accepts or rejects it — accountability sits
with the author, not a summarizer. On rejection the gate's reasons are fed
back once; a second rejection fails the task visibly rather than storing
something that couldn't pass.

The model is configuration (VARVE_MODEL), never code: which model wrote an
entry is memory-relevant, so it is recorded on the entry, and changing models
changes the log's future, not its past.
"""

import json
import os
import re

from . import store, tasks

SYSTEM = """You are the resident author of a varve log: an append-only, \
hash-chained memory founded empty. You are writing one entry that a future \
reader with no memory of you must be able to verify and act on.

The gate will reject your entry unless it follows the constitution:
- kind is one of: observation, hypothesis, hunch, errata, prediction, resolution, meta
- 'observation' and 'resolution' REQUIRE anchors: things a stranger could check, \
each {"type": "url"|"file"|"query"|"sha256"|"entry", "ref": "..."}. Only anchor \
what you actually consulted in this task — never a recalled or plausible source. \
If you cannot anchor it, it is a 'hypothesis' or 'hunch', and saying so is correct, \
not a failure.
- 'prediction' requires {"statement": <falsifiable claim>, "p": <0<p<1>, \
"resolve_by": "YYYY-MM-DD"} in a 'prediction' field.
- 'errata' requires 'corrects': the id of the entry it corrects.
- title and body are required; write the body self-contained.

Respond with ONLY a single JSON object for the entry: \
{"kind": ..., "title": ..., "body": ..., "anchors": [...], "tags": [...]} \
plus 'prediction'/'corrects'/'resolves'/'outcome' when the kind needs them. \
No prose around the JSON."""

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text):
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("model response contained no JSON object")
    return json.loads(m.group(0))


def _context(root, limit=12):
    entries = store.read_log(root)
    recent = entries[-limit:]
    lines = ["The log currently ends at %s. Recent entries:" % entries[-1]["id"]]
    for e in recent:
        lines.append("- %s %s [%s] %s" % (e["id"], e["ts"], e["kind"], e["title"]))
    unresolved = [
        e for e in entries
        if e["kind"] == "prediction"
        and not any(r.get("resolves") == e["id"] for r in entries if r["kind"] == "resolution")
    ]
    if unresolved:
        lines.append("Unresolved predictions: " + ", ".join(
            "%s (%s, resolve by %s)" % (e["id"], e["prediction"]["statement"], e["prediction"]["resolve_by"])
            for e in unresolved))
    return "\n".join(lines)


def _call_model(client, model, messages, use_web):
    kwargs = dict(model=model, max_tokens=16000, system=SYSTEM, messages=messages)
    if use_web:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    response = client.messages.create(**kwargs)
    # server tools may pause the turn; resume until the model actually finishes
    while response.stop_reason == "pause_turn":
        messages = messages + [{"role": "assistant", "content": response.content}]
        response = client.messages.create(**dict(kwargs, messages=messages))
    return response


def work_once(root):
    """Process the highest-priority pending task. Returns the stored entry,
    or None when the queue is empty."""
    task = tasks.next_pending(root)
    if task is None:
        return None

    model = os.environ.get("VARVE_MODEL")
    if not model:
        raise SystemExit(
            "VARVE_MODEL is not set. The worker refuses to guess its own author — "
            "set it to the model id that should write this log's entries."
        )
    use_web = os.environ.get("VARVE_WEB_SEARCH") == "1" and task["kind"] == "research"
    if task["kind"] == "research" and not use_web:
        # An offline "research" run would push the model toward fabricated
        # anchors. Reframe honestly instead of pretending.
        task = dict(task, prompt=task["prompt"] + (
            "\n\n(No web access on this run: do not report on the outside world. "
            "Work only from the log itself, and label conclusions hypothesis/hunch.)"
        ))

    import anthropic  # deferred: everything but the worker is stdlib-only
    client = anthropic.Anthropic()

    user = "%s\n\nTASK %s (%s): %s" % (_context(root), task["id"], task["kind"], task["prompt"])
    messages = [{"role": "user", "content": user}]

    last_error = None
    for attempt in range(2):
        response = _call_model(client, model, messages, use_web)
        if response.stop_reason == "refusal":
            tasks.mark(root, task["id"], "failed", error="model declined the task")
            raise RuntimeError("model declined task %s" % task["id"])
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            fields = _extract_json(text)
            fields["author"] = {"model": model, "task": task["id"]}
            entry = store.append(root, fields)
            tasks.mark(root, task["id"], "done", entry_id=entry["id"])
            return entry
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            # one feedback round: show the gate's reasons, ask for a corrected entry
            messages = messages + [
                {"role": "assistant", "content": text or "(empty)"},
                {"role": "user", "content": "The gate rejected that entry:\n%s\n"
                 "Return a corrected JSON entry. If you cannot anchor a claim, "
                 "relabel the entry as hypothesis or hunch." % last_error},
            ]

    tasks.mark(root, task["id"], "failed", error=last_error)
    raise RuntimeError("task %s failed the gate twice: %s" % (task["id"], last_error))


def work(root, once=False):
    done = []
    while True:
        entry = work_once(root)
        if entry is None or once:
            return done + ([entry] if entry else [])
        done.append(entry)
