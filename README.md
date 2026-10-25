# xslt-rescue

Chrome is removing XSLT (`<?xml-stylesheet type="text/xsl" ...?>` and `XSLTProcessor`) from Stable on **2026-11-17**. XML files that rely on it will render as a raw XML tree. `xslt-rescue` finds those files and pre-renders them to plain HTML on your side, with no JavaScript needed in the browser.

It uses [lxml](https://lxml.de/)'s bundled **libxslt**, the same XSLT 1.0 + EXSLT engine Chrome used, so output should match what browsers rendered.

## Install

```sh
pip install .            # from a checkout; Python 3.9+, only dependency is lxml
```

## Use

```sh
xslt-rescue scan ./site              # which XML files will break? (exit 1 if any, handy in CI)
xslt-rescue check https://example.org/feed.xml   # live URL: affected? can it be pre-rendered?
xslt-rescue build ./site -o ./out    # copy site, add feed.html next to each XSLT-styled feed.xml
xslt-rescue serve ./site --port 8000 # same URL: HTML for browsers, raw XML for feed readers
```

`build` never modifies your XML, so feed readers and crawlers keep getting the original. Output type follows `xsl:output`: `.html`, `.txt` (text), or `.xml`.

### Keep the same URL (static hosting)

Serve the pre-rendered HTML to browsers and XML to everything else.

**nginx**
```nginx
location ~ ^(?<base>.+)\.xml$ {
    add_header Vary Accept;
    if ($http_accept ~* "text/html") { rewrite ^ $base.html last; }
}
```

**Apache (.htaccess)**
```apache
RewriteEngine On
RewriteCond %{HTTP_ACCEPT} text/html
RewriteCond %{REQUEST_FILENAME} -f
RewriteCond %{DOCUMENT_ROOT}/$1.html -f
RewriteRule ^(.+)\.xml$ $1.html [L]
Header append Vary Accept
```

Netlify/Cloudflare Pages/S3 can't branch on `Accept` without an edge function; link to the `.html` page instead (or use `serve` behind a proxy).

## What it handles

`xsl:include` / `xsl:import`, `document()`, EXSLT, non-UTF-8 source encodings, embedded stylesheets (`href="#id"`), `xsl:output method=html|xml|text`.

**Safety:** reads are confined to the site root and network access (remote stylesheets, `document('http://...')`) is off unless you pass `--allow-network`.

## Limits (be aware)

- XSLT 1.0 only, like browsers. XSLT 2/3 stylesheets are out of scope.
- Pages whose stylesheet uses `XSLTProcessor` in client-side JS (not the `xml-stylesheet` PI) are not detected; run the transform server-side or use a polyfill.
- Stylesheets that depend on browser-only behaviour (e.g. `xsl:output` media-type quirks, `disable-output-escaping` inside Chrome's DOM-based renderer) may differ slightly. Compare the result for important pages.
- A WASM XSLT polyfill that runs in the browser also exists (linked from [Chrome's migration page](https://developer.chrome.com/docs/web-platform/deprecating-xslt)). This tool is the server-side alternative, for no-JS clients, crawlers and sites that don't want to edit their XML.

## Develop

```sh
pip install -e '.[test]' && pytest
```
