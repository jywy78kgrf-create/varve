"""A minimal read-only page over the log. Stdlib only, loopback by default.

Read-only is a constitutional stance, not a missing feature: the web surface
can never become a second write path around the gate. It re-reads and
re-verifies the chain on every request — slow-by-honesty at a scale where
that cost is invisible.
"""

import html
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import store, views

_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>varve log</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
        padding: 0 1rem; color: #1a1d21; background: #fdfcf9; }}
 h1 {{ font-size: 1.3rem; }} .ok {{ color: #22633c; }} .bad {{ color: #a3202e; }}
 article {{ border-top: 1px solid #ddd6c8; padding: .8rem 0; }}
 .k {{ display: inline-block; font-size: .75rem; padding: .05rem .5rem;
      border: 1px solid #c9c2b2; border-radius: 1rem; margin-right: .5rem; }}
 .meta {{ color: #6b6455; font-size: .8rem; }}
 .anchors {{ font-size: .8rem; color: #6b6455; word-break: break-all; }}
 @media (prefers-color-scheme: dark) {{
   body {{ color: #e8e4da; background: #16181b; }}
   article {{ border-color: #33373d; }} .k {{ border-color: #4a4f57; }}
 }}
</style>
<h1>varve log</h1>
<p class="{cls}">{verdict}</p>
<p class="meta">{count} entries · brier: {brier}</p>
{entries}
"""


def _render(root):
    problems = store.verify(root)
    entries = store.read_log(root)
    score, n, _ = views.brier(root)
    items = []
    for e in reversed(entries):
        anchors = "; ".join("%(type)s:%(ref)s" % a for a in e.get("anchors", []))
        extra = ""
        if e["kind"] == "prediction":
            p = e["prediction"]
            extra = "<div class=meta>p=%.2f · resolve by %s · %s</div>" % (
                p["p"], html.escape(p["resolve_by"]), html.escape(p["statement"]))
        items.append(
            "<article><span class=k>%s</span><strong>%s</strong>"
            "<div class=meta>%s · %s · prev %s…</div>%s<p>%s</p>%s</article>" % (
                html.escape(e["kind"]), html.escape(e["title"]),
                html.escape(e["id"]), html.escape(e["ts"]),
                html.escape(e.get("prev", "")[:12] or "(founding)"), extra,
                html.escape(e["body"]).replace("\n", "<br>"),
                ("<div class=anchors>anchors: %s</div>" % html.escape(anchors)) if anchors else "",
            ))
    verdict = ("chain intact — %d entries verified" % len(entries)) if not problems \
        else "CHAIN BROKEN: " + "; ".join(problems)
    return _PAGE.format(
        cls="ok" if not problems else "bad", verdict=html.escape(verdict),
        count=len(entries),
        brier=("%.3f over %d" % (score, n)) if n else "no resolved predictions yet",
        entries="\n".join(items))


def serve(root, port=8990, bind="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = _render(root).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer((bind, port), Handler)
    print("varve: read-only view of %s on http://%s:%d" % (root, bind, port))
    server.serve_forever()
