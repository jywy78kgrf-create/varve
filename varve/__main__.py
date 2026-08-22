"""CLI: python -m varve <command> <log-dir> [options]"""

import argparse
import json
import sys

from . import store, tasks, views


def _parse_anchor(s):
    if ":" not in s:
        raise argparse.ArgumentTypeError("anchor must be type:ref, e.g. url:https://…")
    t, ref = s.split(":", 1)
    return {"type": t, "ref": ref}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="varve")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="found a new log (empty, by constitution)")
    p.add_argument("root")
    p.add_argument("--note", default="")

    p = sub.add_parser("append", help="write one entry through the gate")
    p.add_argument("root")
    p.add_argument("--kind", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--anchor", action="append", type=_parse_anchor, default=[], dest="anchors")
    p.add_argument("--tag", action="append", default=[], dest="tags")
    p.add_argument("--corrects", help="entry id (kind errata)")
    p.add_argument("--statement", help="falsifiable claim (kind prediction)")
    p.add_argument("--p", type=float, help="confidence 0<p<1 (kind prediction)")
    p.add_argument("--resolve-by", help="ISO date (kind prediction)")
    p.add_argument("--resolves", help="prediction entry id (kind resolution)")
    p.add_argument("--outcome", choices=["true", "false"], help="kind resolution")

    p = sub.add_parser("verify", help="walk the hash chain; exit 1 if broken")
    p.add_argument("root")
    p.add_argument("--expect-head", help="previously witnessed chain-head hash; catches truncation/forks")

    p = sub.add_parser("head", help="print the chain head (seq + hash) — witness this externally")
    p.add_argument("root")

    p = sub.add_parser("beliefs", help="claim-bearing entries with standing (corrected-by aware)")
    p.add_argument("root")

    p = sub.add_parser("task", help="manage the author's queue")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("root")
    p.add_argument("--kind", default="reflect")
    p.add_argument("--prompt", default="")
    p.add_argument("--priority", type=int, default=5)

    p = sub.add_parser("work", help="run the resident author on pending tasks")
    p.add_argument("root")
    p.add_argument("--once", action="store_true")

    p = sub.add_parser("digest", help="print a recent-window summary")
    p.add_argument("root")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("brier", help="calibration over resolved predictions")
    p.add_argument("root")

    p = sub.add_parser("render", help="write the read-only view as a static file")
    p.add_argument("root")
    p.add_argument("--out", default="site/index.html")

    p = sub.add_parser("serve", help="read-only web view (loopback)")
    p.add_argument("root")
    p.add_argument("--port", type=int, default=8990)
    p.add_argument("--bind", default="127.0.0.1")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        entry = store.init(args.root, note=args.note)
        print("founded %s at %s (%s)" % (args.root, entry["ts"], entry["hash"][:12]))

    elif args.cmd == "append":
        fields = {
            "kind": args.kind, "title": args.title, "body": args.body,
            "anchors": args.anchors, "tags": args.tags,
        }
        if args.corrects:
            fields["corrects"] = args.corrects
        if args.kind == "prediction":
            fields["prediction"] = {"statement": args.statement, "p": args.p,
                                    "resolve_by": args.resolve_by}
        if args.resolves:
            fields["resolves"] = args.resolves
        if args.outcome is not None:
            fields["outcome"] = args.outcome == "true"
        try:
            entry = store.append(args.root, fields)
        except ValueError as exc:
            print(exc)
            sys.exit(1)
        print("%s %s (%s)" % (entry["id"], entry["title"], entry["hash"][:12]))

    elif args.cmd == "verify":
        problems = store.verify(args.root, expect_head=args.expect_head)
        if problems:
            print("CHAIN BROKEN:")
            for p_ in problems:
                print("  -", p_)
            sys.exit(1)
        seq, h = store.head(args.root)
        print("chain intact — %d entries verified; head e%06d %s" % (seq, seq, h))

    elif args.cmd == "head":
        seq, h = store.head(args.root)
        print("e%06d %s" % (seq, h))

    elif args.cmd == "beliefs":
        for e, status in views.beliefs(args.root):
            print("%s [%s] %s — %s" % (e["id"], e["kind"], e["title"], status))

    elif args.cmd == "task":
        if args.action == "add":
            t = tasks.add(args.root, args.kind, args.prompt, args.priority)
            print("queued %s [%s] %s" % (t["id"], t["kind"], t["prompt"][:60]))
        else:
            for t in tasks.list_tasks(args.root):
                print(json.dumps(t, ensure_ascii=False))

    elif args.cmd == "work":
        from . import worker  # needs the anthropic package; import late
        done = worker.work(args.root, once=args.once)
        if not done:
            print("queue empty")
        for e in done:
            print("wrote %s [%s] %s" % (e["id"], e["kind"], e["title"]))

    elif args.cmd == "digest":
        print(views.digest(args.root, days=args.days))

    elif args.cmd == "brier":
        score, n, rows = views.brier(args.root)
        if not n:
            print("no resolved predictions yet")
        else:
            for pred, res, s in rows:
                print("%s p=%.2f -> %s  (%.3f)  %s" % (
                    pred["id"], pred["prediction"]["p"],
                    "true" if res["outcome"] else "false", s,
                    pred["prediction"]["statement"]))
            print("brier %.3f over %d forecast(s)  [0=prophet, 0.25=coin at p=.5]" % (score, n))

    elif args.cmd == "render":
        from . import web
        print("wrote", web.render_to(args.root, args.out))

    elif args.cmd == "serve":
        from . import web
        web.serve(args.root, port=args.port, bind=args.bind)


if __name__ == "__main__":
    main()
