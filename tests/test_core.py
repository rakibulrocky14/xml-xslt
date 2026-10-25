import os, shutil, subprocess, sys, threading, urllib.request, http.server, functools
import pytest
from xslt_rescue.core import scan_tree, transform_file, find_stylesheet_refs
from xslt_rescue.cli import main

SITE = os.path.join(os.path.dirname(__file__), "site")


def test_scan_finds_only_xslt():
    found = {os.path.relpath(p, SITE) for p, _ in scan_tree(SITE)}
    assert found == {"feed.xml", "embedded.xml", "text.xml", "bad.xml", os.path.join("blog", "latin1.xml")}


def test_include_import_document_exslt_and_utf8():
    r = transform_file(os.path.join(SITE, "feed.xml"), allowed_root=SITE)
    assert r.ok, r.error
    html = r.content.decode("utf-8")
    assert r.extension == ".html"
    assert '<h1 class="base">Café Feed</h1>' in html          # xsl:import + non-ASCII
    assert "Example Gov Data" in html                           # document()
    assert "2 items" in html                                    # exsl:node-set
    assert '<b class="badge">**</b>' in html                    # xsl:include + str:padding
    assert "One &amp; first" in html


def test_latin1_source_roundtrip():
    r = transform_file(os.path.join(SITE, "blog", "latin1.xml"), allowed_root=SITE)
    assert r.ok, r.error
    assert "Café latin1" in r.content.decode("utf-8") and "über" in r.content.decode("utf-8")


def test_embedded_stylesheet_and_text_output():
    r = transform_file(os.path.join(SITE, "embedded.xml"), allowed_root=SITE)
    assert r.ok, r.error
    assert b"embedded ok" in r.content
    t = transform_file(os.path.join(SITE, "text.xml"), allowed_root=SITE)
    assert t.ok and t.extension == ".txt" and t.content.strip() == b"a,b,"


def test_missing_stylesheet_reports_error():
    r = transform_file(os.path.join(SITE, "bad.xml"), allowed_root=SITE)
    assert not r.ok and "cannot load stylesheet" in r.error


def test_reads_outside_root_blocked(tmp_path):
    secret = tmp_path / "secret.xml"; secret.write_text("<s>TOPSECRET</s>")
    site = tmp_path / "site"; site.mkdir()
    (site / "x.xsl").write_text(f'''<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
<xsl:template match="/"><o><xsl:value-of select="document('{secret}')"/></o></xsl:template></xsl:stylesheet>''')
    (site / "x.xml").write_text('<?xml-stylesheet type="text/xsl" href="x.xsl"?><a/>')
    r = transform_file(str(site / "x.xml"), allowed_root=str(site))
    assert not r.ok and "outside site root" in r.error


def test_network_blocked_by_default(tmp_path):
    (tmp_path / "x.xml").write_text('<?xml-stylesheet type="text/xsl" href="http://127.0.0.1:9/x.xsl"?><a/>')
    r = transform_file(str(tmp_path / "x.xml"), allowed_root=str(tmp_path))
    assert not r.ok and "allow-network" in r.error


def test_build_cli(tmp_path, capsys):
    out = tmp_path / "out"
    rc = main(["build", SITE, "-o", str(out)])
    assert rc == 2  # bad.xml fails
    assert (out / "feed.html").exists() and (out / "feed.xml").exists()
    assert (out / "blog" / "latin1.html").exists() and (out / "text.txt").exists()
    assert (out / "plain.xml").exists() and not (out / "plain.html").exists()


def test_scan_exit_codes(capsys):
    assert main(["scan", SITE]) == 1
    assert main(["scan", os.path.join(SITE, "xsl")]) == 0


def test_serve_negotiation_and_check(tmp_path):
    from xslt_rescue.cli import _Handler
    handler = type("H", (_Handler,), {"root": SITE})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: handler(*a, directory=SITE, **k))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        h = urllib.request.urlopen(urllib.request.Request(base + "/feed.xml", headers={"Accept": "text/html"}))
        assert h.headers["Content-Type"].startswith("text/html") and b"Example Gov Data" in h.read()
        x = urllib.request.urlopen(urllib.request.Request(base + "/feed.xml", headers={"Accept": "application/rss+xml"}))
        assert b"<?xml-stylesheet" in x.read()
        from xslt_rescue.core import check_url
        r = check_url(base + "/feed.xml")
        assert r.affected and r.ok, r.error
        assert not check_url(base + "/plain.xml").affected
    finally:
        srv.shutdown()
