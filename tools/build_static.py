#!/usr/bin/env python
"""Build a read-only static snapshot of the evidence viewer.

GitHub Pages serves files, not Python, so the live FastAPI app cannot run
there. What CAN run there is the real UI driven by pre-baked responses: the
committed corpus never changes, so every GET the page makes has exactly one
answer that can be written to disk ahead of time.

What this produces is the actual `web/` client, unmodified except for making
its API paths relative, plus:

    api/stats.json  api/facts.json  api/relationships.json  api/rejects.json
    api/pages/<doc_id>/<n>.png      one per page any fact or refusal cites
    api/export*.csv                 the same exports the server offers
    shim.js                         maps the page's fetches onto those files

Uploading a PDF is the one thing that genuinely cannot work without a server,
so the button says so rather than failing quietly.

    python tools/build_static.py --db data/demo.db --out _site
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from tieout.store.db import Store  # noqa: E402

BANNER = """
<div id="snapshot-note">
  <strong>Read-only snapshot.</strong>
  The reasoning, evidence and page images below are the real output of
  <code>python run.py --demo</code> over the committed corpus. Uploading a PDF
  needs the Python server, so that button is disabled here &mdash;
  <a href="https://github.com/Maaadhavq/tieout">run it locally</a> for that.
</div>
"""

BANNER_CSS = """
#snapshot-note {
  padding: 10px 22px; background: color-mix(in oklab, var(--accent) 8%, transparent);
  border-bottom: 1px solid var(--rule); font-size: 13px; color: var(--muted);
  flex-shrink: 0; line-height: 1.5;
}
#snapshot-note strong { color: var(--ink); }
#snapshot-note a { color: var(--accent); }
#upload-btn { opacity: .45; cursor: not-allowed; }
"""

SHIM = """/* Serve the page's API calls from files baked at build time. */
(function () {
  const real = window.fetch.bind(window);
  let factsPromise = null;
  const facts = () => (factsPromise ||= real("api/facts.json").then((r) => r.json()));

  const json = (data) =>
    new Response(JSON.stringify(data), {
      status: 200, headers: { "Content-Type": "application/json" },
    });

  window.fetch = async function (input, init) {
    const url = String(typeof input === "string" ? input : input.url || "");
    const path = url.split("?")[0].replace(/^.*?(\\/api\\/|api\\/)/, "/api/");

    if ((init && init.method || "GET").toUpperCase() === "POST") {
      return new Response(
        JSON.stringify({ detail: "This is a static snapshot with no server. " +
          "Clone the repository and run `python run.py --demo` to upload a PDF." }),
        { status: 501, headers: { "Content-Type": "application/json" } });
    }
    if (path === "/api/stats") return real("api/stats.json");
    if (path === "/api/facts") return real("api/facts.json");
    if (path === "/api/relationships") return real("api/relationships.json");
    if (path === "/api/rejects") return real("api/rejects.json");

    const one = path.match(/^\\/api\\/facts\\/(.+)$/);
    if (one) {
      const hit = (await facts()).find((f) => f.fact_id === one[1]);
      return hit ? json(hit)
                 : new Response('{"detail":"no such fact"}', { status: 404 });
    }
    return real(input, init);
  };
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/demo.db")
    ap.add_argument("--out", default="_site")
    ap.add_argument("--no-pages", action="store_true",
                    help="skip page images (much smaller, no highlights)")
    # The server renders PNG at 132 dpi. For a hosted snapshot that is 119 MB,
    # most of it the graphics-heavy earnings deck, where PNG is the wrong
    # format. JPEG at 110 dpi is 37 MB and still reads cleanly; the client's
    # highlight scale is rewritten to match below, or every box lands wrong.
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--quality", type=int, default=80)
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "api").mkdir(parents=True)

    store = Store(args.db)
    from tieout.api import app as api_mod
    api_mod.configure(args.db, offline=True)
    from fastapi.testclient import TestClient
    client = TestClient(api_mod.app)

    # ---- the four bulk GETs the page makes on load ----------------------
    for name, path in (("stats", "/api/stats"),
                       ("facts", "/api/facts?limit=5000"),
                       ("relationships", "/api/relationships?limit=5000"),
                       ("rejects", "/api/rejects?limit=2000")):
        data = client.get(path).json()
        (out / "api" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        n = len(data) if isinstance(data, list) else 1
        print(f"  api/{name}.json  {n} rows")

    for name in ("export.csv", "export/relationships.csv", "export/refused.csv"):
        r = client.get(f"/api/{name}")
        dest = out / "api" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        print(f"  api/{name}  {len(r.content) / 1024:.0f} KB")

    # ---- one PNG per page any fact or refusal cites ---------------------
    if not args.no_pages:
        wanted = store.q("""
            SELECT DISTINCT d.doc_id, d.path, e.page_no
            FROM evidence e JOIN documents d ON d.doc_id = e.doc_id
            UNION
            SELECT DISTINCT d.doc_id, d.path, r.page_no
            FROM rejects r JOIN documents d ON d.doc_id = r.doc_id""")
        total = 0
        for i, row in enumerate(wanted, 1):
            dest = out / "api" / "pages" / row["doc_id"] / f"{row['page_no']}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                doc = fitz.open(row["path"])
                pix = doc[row["page_no"] - 1].get_pixmap(dpi=args.dpi)
                blob = pix.tobytes("jpeg", jpg_quality=args.quality)
                doc.close()
            except Exception as exc:  # noqa: BLE001 - a missing page is not fatal
                print(f"  ! page {row['doc_id']}/{row['page_no']}: {exc}")
                continue
            dest.write_bytes(blob)
            total += len(blob)
            if i % 50 == 0:
                print(f"  ... {i}/{len(wanted)} pages, {total / 1e6:.0f} MB")
        print(f"  api/pages/  {len(wanted)} pages, {total / 1e6:.1f} MB")

    # ---- the real client, with its API paths made relative --------------
    web = ROOT / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    js = (js.replace('`/api/pages/${docId}/${page}.png`',
                     '`api/pages/${docId}/${page}.jpg`')
            .replace('"/api/', '"api/')
            .replace('(132 / 72)', f'({args.dpi} / 72)'))
    assert f'({args.dpi} / 72)' in js, "highlight scale not rewritten -- boxes would be wrong"
    assert 'api/pages/${docId}/${page}.jpg' in js, "page image path not rewritten"
    (out / "app.js").write_text(js, encoding="utf-8")

    css = (web / "styles.css").read_text(encoding="utf-8") + BANNER_CSS
    (out / "styles.css").write_text(css, encoding="utf-8")
    (out / "shim.js").write_text(SHIM, encoding="utf-8")

    html = (web / "index.html").read_text(encoding="utf-8")
    html = (html
            .replace('href="/static/styles.css"', 'href="styles.css"')
            .replace('<script src="/static/app.js"></script>',
                     '<script src="shim.js"></script>\n<script src="app.js"></script>')
            .replace("<body>", "<body>\n" + BANNER)
            .replace('<button id="upload-btn">Add PDF</button>',
                     '<button id="upload-btn" disabled title="needs the local server">Add PDF</button>'))
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"  built {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
