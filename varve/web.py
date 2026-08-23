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
<p class="meta">head <code>{head}</code></p>
<p class="meta">{count} entries · brier: {brier}</p>
{pace}
{entries}
<p class="meta">Rendered from the log itself. The log is the truth; this page is
a view — recompute it any time with <code>varve render</code>. {links}</p>
"""


def _render(root):
    problems = store.verify(root)
    entries = store.read_log(root)
    score, n, _ = views.brier(root)
    corr = views.corrections(entries)
    head_txt = "%s %s" % (entries[-1]["id"], entries[-1]["hash"]) if entries else "(empty)"
    pace = _pace_block(root)
    items = []
    for e in reversed(entries):
        anchors = "; ".join(
            "%s:%s" % (a.get("type"), a.get("ref")) if isinstance(a, dict) else str(a)
            for a in (e.get("anchors") or []))
        extra = ""
        if e.get("id") in corr:
            extra += "<div class=meta>⚠ corrected by %s — do not act on this entry as written</div>" % (
                html.escape(", ".join(corr[e["id"]])))
        # Read defensively, like views.py: this page is how a reader inspects a
        # log, and the log most worth inspecting is a damaged one. A KeyError
        # here would replace the verdict banner — which is the whole point of
        # the page — with a traceback (second review, 2026-08-23).
        if e.get("kind") == "prediction" and isinstance(e.get("prediction"), dict):
            p = e["prediction"]
            extra = "<div class=meta>p=%s · resolve by %s · %s</div>" % (
                html.escape(str(p.get("p"))), html.escape(str(p.get("resolve_by"))),
                html.escape(str(p.get("statement"))))
        items.append(
            "<article><span class=k>%s</span><strong>%s</strong>"
            "<div class=meta>%s · %s · prev %s…</div>%s<p>%s</p>%s</article>" % (
                html.escape(str(e.get("kind", "?"))), html.escape(str(e.get("title", ""))),
                html.escape(str(e.get("id", "?"))), html.escape(str(e.get("ts", "?"))),
                html.escape(str(e.get("prev", ""))[:12] or "(founding)"), extra,
                html.escape(str(e.get("body", ""))).replace("\n", "<br>"),
                ("<div class=anchors>anchors: %s</div>" % html.escape(anchors)) if anchors else "",
            ))
    verdict = ("chain intact — %d entries verified" % len(entries)) if not problems \
        else "CHAIN BROKEN: " + "; ".join(problems)
    return _PAGE.format(
        cls="ok" if not problems else "bad", verdict=html.escape(verdict),
        count=len(entries), head=html.escape(head_txt), pace=pace,
        links=_links(root),
        brier=("%.3f over %d" % (score, n)) if n else "no resolved predictions yet",
        entries="\n".join(items))


def _pace_block(root):
    """The author's own clock, shown as-is. Operational state, not memory —
    which is why it is rendered separately from the chain."""
    import json
    import os
    path = os.path.join(root, "pace.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            pace = json.load(f)
    except (OSError, ValueError):
        return ""
    return ('<article><span class=k>pace</span><strong>next wake: %s</strong>'
            '<p>%s</p></article>' % (html.escape(str(pace.get("next", "?"))),
                                     html.escape(str(pace.get("hold", "")))))


def _links(root):
    """Point a reader at the things the log references but does not contain:
    the source entries, and the workshop where anything built ends up."""
    import os
    repo = os.environ.get("VARVE_REPO_URL", "")
    if not repo:
        return ""
    return ('Source: <a href="%s/tree/main/%s/log">entries</a> · '
            '<a href="%s/tree/main/workshop">workshop</a> · '
            '<a href="%s/commits/main">history</a>.' % (repo, os.path.basename(root.rstrip("/")), repo, repo))


def render_to(root, out_path):
    """Write the view as a static file (for CI / GitHub Pages)."""
    import os
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(_render(root))
    return out_path


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
