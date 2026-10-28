"""Read-only HTTP browser with separate storage and browser-issued source IDs.

This is a text retrieval tool, not Chromium. It does not execute JavaScript.
Resolved addresses are validated and pinned before connecting, including redirects.
"""

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
import uuid
from xml.etree import ElementTree
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from .events import record
from .source_view import (SOURCE_VIEW_VERSION, blocked_alternate_service, blocked_lake_download, blocked_service_operation,
                          is_layer_records, map_request, map_text)

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 60000
TIMEOUT = 15


class BrowserError(ValueError):
    pass


def destination(url):
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise BrowserError("Invalid URL") from exc
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or any(ord(c) < 33 or ord(c) == 127 for c in url)):
        raise BrowserError("This URL is not safe to access.")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise BrowserError("This URL is not safe to access.")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise BrowserError("Host could not be resolved.")
    ips = [item[4][0] for item in addresses]
    if any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise BrowserError("This URL is not safe to access.")
    return parts, host, port, ips[0]


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure):
        self.default_port = 443 if secure else 80
        super().__init__(host, port, timeout=TIMEOUT)
        self.address = address
        self.secure = secure

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            try:
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=self.host)
            except Exception:
                self.sock.close()
                raise


def check_source_access(url, source_layer_url):
    if blocked_alternate_service(url, source_layer_url):
        raise BrowserError("This map service is unavailable through the browser.")
    if blocked_service_operation(url, source_layer_url):
        raise BrowserError("This service's query, find, and search endpoints are unavailable through the browser.")
    if blocked_lake_download(url, source_layer_url):
        raise BrowserError("This lake dataset download is unavailable through the browser.")


def fetch(url, *, restricted_layer=""):
    for _ in range(6):
        check_source_access(url, restricted_layer)
        parts, host, port, address = destination(url)
        connection = PinnedHTTPConnection(host, port, address, parts.scheme == "https")
        try:
            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request("GET", path, headers={
                "User-Agent": "CitationEnvironment/0.1",
                "Accept": "text/html,text/plain,application/json,*/*;q=0.1",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise BrowserError("Redirect without a destination.")
                url = urljoin(url, location)
                continue
            if not 200 <= response.status < 300:
                raise BrowserError("HTTP {}".format(response.status))
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise BrowserError("Response exceeds the 2 MiB browser limit.")
            content_type = response.getheader("Content-Type", "text/plain")
            if not (content_type.startswith("text/") or
                    "json" in content_type or "xml" in content_type or "rss" in content_type):
                raise BrowserError("Unsupported content type: " + content_type)
            encoding = response.headers.get_content_charset() or "utf-8"
            return url, content_type, body.decode(encoding, errors="replace")
        finally:
            connection.close()
    raise BrowserError("Too many redirects.")


class PageText(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.text = []
        self.links = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if not self.hidden and tag == "a":
            href = dict(attrs).get("href")
            if href:
                target = urljoin(self.url, href)
                if target.startswith(("http://", "https://")) and target not in self.links:
                    self.links.append(target)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.text.append(data.strip())


def open_page(url, log_dir="/logs",
              source_view="native", source_layer_url=""):
    log_dir = Path(log_dir)
    record(log_dir / "events.jsonl", "browser_open_attempt", url=url)
    try:
        restricted_layer = source_layer_url if source_view == "map_only" else ""
        check_source_access(url, restricted_layer)
        map_only = source_view == "map_only" and is_layer_records(url, source_layer_url)
        requested_url = map_request(url) if map_only else url
        options = {"restricted_layer": restricted_layer} if restricted_layer else {}
        final_url, content_type, body = fetch(requested_url, **options)
        check_source_access(final_url, restricted_layer)
        map_only = map_only or (source_view == "map_only" and is_layer_records(final_url, source_layer_url))
        links = []
        text = body
        if map_only:
            text = map_text(body)
            final_url = url
        elif "html" in content_type:
            page = PageText(final_url)
            page.feed(body)
            text, links = "\n".join(page.text), page.links[:100]
        elif "xml" in content_type or "rss" in content_type:
            try:
                root = ElementTree.fromstring(body)
            except ElementTree.ParseError:
                root = None
            if root is not None and root.tag == "rss":
                items = root.findall("./channel/item")[:100]
                text = "\n\n".join("\n".join(item.findtext(field, "") for field in
                                              ("title", "link", "description")) for item in items)
                links = [item.findtext("link") for item in items if item.findtext("link")]
        source_id = "src_" + uuid.uuid4().hex
        result = {"source_id": source_id, "url": final_url,
                  "text": text[:MAX_TEXT], "links": links,
                  "truncated": len(text) > MAX_TEXT}
        (log_dir / (source_id + ".json")).write_text(json.dumps(result), encoding="utf-8")
        record(log_dir / "events.jsonl", "browser_open_succeeded", url=final_url,
               source_id=source_id, sha256=hashlib.sha256(body.encode()).hexdigest(),
               source_view="map_only" if map_only else "native", source_view_version=SOURCE_VIEW_VERSION)
        return result
    except (BrowserError, OSError, ValueError, LookupError, http.client.HTTPException) as exc:
        record(log_dir / "events.jsonl", "browser_open_failed", url=url, error=str(exc))
        return {"error": str(exc), "url": url}


def main():
    # Arguments arrive on stdin so URLs and future query strings cannot become shell code.
    request = json.load(sys.stdin)
    source_view = os.environ.get("BROWSER_SOURCE_VIEW", "native")
    if source_view not in ("native", "map_only"):
        raise SystemExit("Invalid browser source view")
    operation = request.get("operation", "open")
    if operation == "search":
        url = "https://www.bing.com/search?format=rss&q=" + quote_plus(request["query"])
    elif operation == "open":
        url = request["url"]
    else:
        raise SystemExit("Unknown browser operation")
    print(json.dumps(open_page(url, os.environ.get("BROWSER_LOG_DIR", "/logs"),
                               source_view, os.environ.get("BROWSER_SOURCE_LAYER", ""))))


if __name__ == "__main__":
    main()
