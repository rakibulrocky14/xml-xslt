"""Detect and pre-render XML documents that rely on browser-side XSLT.

Chrome used libxslt for ``<?xml-stylesheet?>`` rendering, and lxml bundles
libxslt, so transforms here follow the same XSLT 1.0 + EXSLT semantics.
"""

from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from lxml import etree

XSL_NS = "http://www.w3.org/1999/XSL/Transform"
XHTML_NS = "http://www.w3.org/1999/xhtml"

# MIME types browsers accept for an XSLT xml-stylesheet processing instruction.
XSLT_TYPES = {"text/xsl", "text/xml", "application/xml", "application/xslt+xml", "text/xslt"}

_PI_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

_PARSER_KW = dict(resolve_entities=True, load_dtd=False, no_network=True, huge_tree=False)


@dataclass
class StylesheetRef:
    href: str
    type: Optional[str] = None


@dataclass
class Result:
    """Outcome of transforming one XML document."""

    source: str
    affected: bool = False
    stylesheet: Optional[str] = None
    content: Optional[bytes] = None
    extension: str = ".html"
    method: str = "html"
    warnings: list = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.content is not None


def parse_pi_attrs(data: str) -> dict:
    return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in _PI_ATTR.finditer(data or "")}


def find_stylesheet_refs(data: bytes) -> list[StylesheetRef]:
    """Return xml-stylesheet PIs in the document prolog that browsers would treat as XSLT."""
    try:
        tree = etree.parse(_BytesIO(data), etree.XMLParser(**_PARSER_KW, recover=True))
    except (etree.XMLSyntaxError, ValueError):
        return []
    root = tree.getroot()
    if root is None:
        return []
    refs = []
    node = root.getprevious()
    while node is not None:
        if isinstance(node, etree._ProcessingInstruction) and node.target == "xml-stylesheet":
            attrs = parse_pi_attrs(node.text)
            href = attrs.get("href")
            typ = (attrs.get("type") or "").strip().lower() or None
            alternate = attrs.get("alternate", "no").lower() == "yes"
            if href and not alternate and typ in XSLT_TYPES | {None}:
                if typ is not None or href.lower().endswith((".xsl", ".xslt")):
                    refs.append(StylesheetRef(href=href, type=typ))
        node = node.getprevious()
    refs.reverse()
    return refs


def _BytesIO(data: bytes):
    import io

    return io.BytesIO(data)


def _is_url(s: str) -> bool:
    return urllib.parse.urlparse(s).scheme in ("http", "https")


def _read(location: str, allow_network: bool, user_agent: str = "xslt-rescue/0.1") -> bytes:
    if _is_url(location):
        if not allow_network:
            raise PermissionError(f"remote resource skipped (use --allow-network): {location}")
        req = urllib.request.Request(location, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - http(s) checked above
            return r.read()
    with open(location, "rb") as f:
        return f.read()


class _Resolver(etree.Resolver):
    """Resolve xsl:include / xsl:import / document() per access policy."""

    def __init__(self, allow_network: bool, allowed_root: Optional[str]):
        self.allow_network = allow_network
        self.allowed_root = os.path.realpath(allowed_root) if allowed_root else None

    def resolve(self, url, pubid, context):
        if url is None:
            return None
        if url.startswith("file://"):
            path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        elif _is_url(url):
            if not self.allow_network:
                raise PermissionError(f"blocked network fetch: {url}")
            return self.resolve_string(_read(url, True), context, base_url=url)
        else:
            path = url
        if self.allowed_root is not None:
            real = os.path.realpath(path)
            if real != self.allowed_root and not real.startswith(self.allowed_root + os.sep):
                raise PermissionError(f"blocked read outside site root: {path}")
        return None  # let libxml2 read the (allowed) local file itself


def _embedded_stylesheet(doc: etree._ElementTree, frag: str) -> etree._ElementTree:
    nodes = doc.xpath("//*[@id=$i or @xml:id=$i]", i=frag)
    if not nodes:
        raise ValueError(f"embedded stylesheet #{frag} not found")
    return etree.ElementTree(etree.fromstring(etree.tostring(nodes[0])))


def _detect_output(sheet: etree._ElementTree, result) -> tuple[str, str]:
    """Return (method, file extension) for the transform output."""
    outs = sheet.xpath("//xsl:output", namespaces={"xsl": XSL_NS})
    method = None
    for o in outs:  # last xsl:output wins, mirroring the spec's precedence in a flat stylesheet
        method = o.get("method") or method
    root = result.getroot()
    if method is None:
        is_html = root is not None and isinstance(root.tag, str) and root.tag.lower() == "html"
        method = "html" if is_html else "xml"
    method = method.lower()
    if method == "text":
        return method, ".txt"
    if method == "html":
        return method, ".html"
    if root is not None and isinstance(root.tag, str) and root.tag in (f"{{{XHTML_NS}}}html", "html"):
        return method, ".html"
    return method, ".xml"


def transform_bytes(
    data: bytes,
    base_path: str,
    *,
    allow_network: bool = False,
    allowed_root: Optional[str] = None,
    stylesheet_override: Optional[str] = None,
) -> Result:
    """Apply the xml-stylesheet XSLT referenced by ``data`` and return the rendered output.

    ``base_path`` is the file path (or URL) of the source, used to resolve relative hrefs.
    """
    res = Result(source=base_path)
    refs = [StylesheetRef(stylesheet_override)] if stylesheet_override else find_stylesheet_refs(data)
    if not refs:
        res.error = "no xml-stylesheet processing instruction found"
        return res
    res.affected = True

    resolver = _Resolver(allow_network, allowed_root)
    parser = etree.XMLParser(**_PARSER_KW)
    parser.resolvers.add(resolver)

    try:
        doc = etree.parse(_BytesIO(data), parser, base_url=base_path)
    except etree.XMLSyntaxError as e:
        res.error = f"XML is not well-formed: {e}"
        return res

    # Browsers apply the first usable stylesheet.
    ref = refs[0]
    if len(refs) > 1:
        res.warnings.append(f"{len(refs)} stylesheets declared; using the first ({ref.href})")
    try:
        if ref.href.startswith("#"):
            sheet = _embedded_stylesheet(doc, ref.href[1:])
            res.stylesheet = ref.href
        else:
            if _is_url(base_path):
                sheet_loc = urllib.parse.urljoin(base_path, ref.href)
            elif _is_url(ref.href):
                sheet_loc = ref.href
            else:
                sheet_loc = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(base_path)), urllib.parse.unquote(ref.href.split("#")[0])))
            res.stylesheet = sheet_loc
            if allowed_root and not _is_url(sheet_loc):
                resolver.resolve(sheet_loc, None, None)  # policy check only
            sheet = etree.parse(_BytesIO(_read(sheet_loc, allow_network)), parser, base_url=sheet_loc)
    except (OSError, PermissionError, ValueError, etree.XMLSyntaxError) as e:
        res.error = f"cannot load stylesheet {ref.href!r}: {e}"
        return res

    access = etree.XSLTAccessControl(
        read_file=True, write_file=False, create_dir=False, read_network=allow_network, write_network=False
    )
    try:
        xslt = etree.XSLT(sheet, access_control=access)
        out = xslt(doc)
    except (etree.XSLTError, PermissionError, OSError) as e:
        res.error = f"XSLT failed: {e}"
        return res

    for entry in xslt.error_log:
        if entry.level_name in ("ERROR", "FATAL") and entry.message not in res.warnings:
            res.warnings.append(entry.message)
    if out.getroot() is None and not bytes(out):
        res.error = "transform produced empty output"
        return res

    res.method, res.extension = _detect_output(sheet, out)
    res.content = bytes(out)
    return res


def transform_file(path: str, *, allow_network: bool = False, allowed_root: Optional[str] = None) -> Result:
    with open(path, "rb") as f:
        data = f.read()
    return transform_bytes(data, path, allow_network=allow_network, allowed_root=allowed_root)


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def scan_tree(root: str, on_file: Optional[Callable[[str], None]] = None):
    """Yield (path, [StylesheetRef]) for every XML file under ``root`` using xml-stylesheet XSLT."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if not name.lower().endswith((".xml", ".rss", ".atom", ".rdf")):
                continue
            path = os.path.join(dirpath, name)
            if on_file:
                on_file(path)
            try:
                with open(path, "rb") as f:
                    head = f.read(1 << 20)  # PIs live in the prolog; 1 MiB is plenty
            except OSError:
                continue
            refs = find_stylesheet_refs(head)
            if refs:
                yield path, refs


def check_url(url: str) -> Result:
    """Fetch a live URL and report whether it relies on XSLT and whether it transforms cleanly."""
    res = Result(source=url)
    try:
        data = _read(url, True)
    except Exception as e:  # noqa: BLE001 - surface any network error to the user
        res.error = f"cannot fetch: {e}"
        return res
    return transform_bytes(data, url, allow_network=True)
