"""xslt-rescue command line: scan, check, build, serve."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import sys

from . import __version__
from .core import check_url, scan_tree, transform_file

EXIT_AFFECTED = 1  # scan/check found sites that will break (useful in CI)
EXIT_ERROR = 2


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def cmd_scan(args) -> int:
    root = os.path.abspath(args.path)
    found = [(p, refs) for p, refs in scan_tree(root)]
    if args.json:
        print(json.dumps([{"file": _rel(p, root), "stylesheets": [r.href for r in refs]} for p, refs in found], indent=2))
    else:
        for p, refs in found:
            print(f"{_rel(p, root)}  ->  {', '.join(r.href for r in refs)}")
        print(f"\n{len(found)} XML file(s) rely on browser-side XSLT and will break in Chrome on 2026-11-17." if found
              else "No XML files with an XSLT xml-stylesheet found. Nothing to fix here.")
    return EXIT_AFFECTED if found else 0


def cmd_check(args) -> int:
    res = check_url(args.url)
    if not res.affected:
        if res.error and "no xml-stylesheet" not in res.error:
            print(f"? {args.url}: {res.error}")
            return EXIT_ERROR
        print(f"OK  {args.url} does not use an XSLT xml-stylesheet. Chrome's change does not affect it.")
        return 0
    print(f"AFFECTED  {args.url} uses XSLT ({res.stylesheet}); it will render as raw XML in Chrome after 2026-11-17.")
    if res.ok:
        print(f"  xslt-rescue can pre-render it ({res.method} output, {len(res.content)} bytes).")
    else:
        print(f"  Could not pre-render automatically: {res.error}")
    for w in res.warnings:
        print(f"  warning: {w}")
    return EXIT_AFFECTED


def cmd_build(args) -> int:
    src, out = os.path.abspath(args.path), os.path.abspath(args.output)
    if out == src or out.startswith(src + os.sep):
        print("error: output directory must be outside the source directory", file=sys.stderr)
        return EXIT_ERROR
    if os.path.exists(out):
        if not args.force:
            print(f"error: {args.output} exists (use --force to overwrite)", file=sys.stderr)
            return EXIT_ERROR
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=shutil.ignore_patterns(".git", "node_modules"))

    done = failed = 0
    for path, _refs in scan_tree(src):
        rel = _rel(path, src)
        res = transform_file(path, allow_network=args.allow_network, allowed_root=src)
        if not res.ok:
            failed += 1
            print(f"FAIL {rel}: {res.error}")
            continue
        dest = os.path.join(out, os.path.splitext(rel)[0] + res.extension)
        with open(dest, "wb") as f:
            f.write(res.content)
        done += 1
        print(f"ok   {rel} -> {_rel(dest, out)}")
        for w in res.warnings:
            print(f"     warning: {w}")
    print(f"\n{done} rendered, {failed} failed. Original XML files were left untouched in {args.output}.")
    if done:
        print("Serve the .html to browsers and the .xml to feed readers; see README for nginx/Apache rules.")
    return EXIT_ERROR if failed else 0


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static server that returns rendered HTML to browsers for XSLT-styled XML at the same URL."""

    root = "."
    allow_network = False


    def do_GET(self):
        fs = self.translate_path(self.path.split("?", 1)[0])
        wants_html = "text/html" in self.headers.get("Accept", "")
        if wants_html and os.path.isfile(fs) and fs.lower().endswith((".xml", ".rss", ".atom", ".rdf")):
            res = transform_file(fs, allow_network=self.allow_network, allowed_root=self.root)
            if res.ok:
                ctype = {".html": "text/html", ".txt": "text/plain", ".xml": "application/xml"}[res.extension]
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Vary", "Accept")
                self.send_header("Content-Length", str(len(res.content)))
                self.end_headers()
                self.wfile.write(res.content)
                return
        super().do_GET()

    def end_headers(self):
        self.send_header("Vary", "Accept")
        super().end_headers()


def cmd_serve(args) -> int:
    root = os.path.abspath(args.path)
    handler = type("H", (_Handler,), {"root": root, "allow_network": args.allow_network})
    srv = http.server.ThreadingHTTPServer((args.host, args.port), lambda *a, **k: handler(*a, directory=root, **k))
    print(f"Serving {root} on http://{args.host}:{args.port}  (HTML for browsers, raw XML for feed readers)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xslt-rescue", description="Keep XSLT-styled XML sites working after Chrome removes XSLT.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list XML files that use an XSLT xml-stylesheet")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scan)

    c = sub.add_parser("check", help="check a live URL: is it affected, and can it be pre-rendered?")
    c.add_argument("url")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("build", help="copy a site to OUTPUT and add pre-rendered HTML next to each XSLT-styled XML file")
    b.add_argument("path")
    b.add_argument("-o", "--output", default="out")
    b.add_argument("--force", action="store_true", help="overwrite OUTPUT if it exists")
    b.add_argument("--allow-network", action="store_true", help="allow http(s) stylesheets / document() calls")
    b.set_defaults(fn=cmd_build)

    v = sub.add_parser("serve", help="serve a directory, rendering XSLT-styled XML to HTML for browsers on the fly")
    v.add_argument("path", nargs="?", default=".")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.add_argument("--allow-network", action="store_true")
    v.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
