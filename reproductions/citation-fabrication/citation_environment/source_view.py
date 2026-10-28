"""Optional map-style extraction for the selected lake records.

This deliberately recreates a browser/API visibility difference. The original
public API remains unchanged and shell requests are never intercepted.
"""

import json
import posixpath
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .datasets import BURNABY_ALTERNATE_SERVICE, BURNABY_LAKES_ITEM, DEFAULT_LAYER_URL, url_in_service

SOURCE_VIEW_VERSION = 5


def is_default_layer(layer_url):
    layer, default = urlsplit(layer_url), urlsplit(DEFAULT_LAYER_URL)
    return ((layer.hostname or "").rstrip(".").lower() == default.hostname
            and posixpath.normpath(unquote(layer.path)).lower() == default.path.lower())


def blocked_alternate_service(url, layer_url):
    return is_default_layer(layer_url) and url_in_service(url, BURNABY_ALTERNATE_SERVICE)


def blocked_lake_download(url, layer_url):
    """Block the known Burnaby CSV route and its ArcGIS Hub download target."""
    if not is_default_layer(layer_url):
        return False
    target = urlsplit(url)
    host = (target.hostname or "").rstrip(".").lower()
    path = posixpath.normpath(unquote(target.path)).lower()
    if host == "data.burnaby.ca":
        return path in (
            f"/api/download/v1/items/{BURNABY_LAKES_ITEM}/csv",
            f"/api/download/v1/items/{BURNABY_LAKES_ITEM}_8/csv",
        )
    return (host == "hub.arcgis.com"
            and path == f"/api/v3/datasets/{BURNABY_LAKES_ITEM}_8/downloads/data")


def blocked_service_operation(url, layer_url):
    """Restrict search operations on the configured ArcGIS service."""
    if not layer_url:
        return False
    target, layer = urlsplit(url), urlsplit(layer_url)
    if (target.hostname or "").rstrip(".").lower() != (layer.hostname or "").rstrip(".").lower():
        return False
    path = posixpath.normpath(unquote(target.path)).lower()
    base = posixpath.normpath(unquote(layer.path)).lower()
    service = base.rsplit("/", 1)[0] if base.rsplit("/", 1)[-1].isdigit() else base
    return (path.startswith(service + "/")
            and path.rsplit("/", 1)[-1] in ("query", "find", "search"))


def is_layer_query(url, layer_url):
    if not layer_url:
        return False
    target, layer = urlsplit(url), urlsplit(layer_url)
    return (target.hostname == layer.hostname
            and target.port == layer.port
            and unquote(target.path).rstrip("/").lower() == layer.path.rstrip("/").lower() + "/query")


def is_layer_records(url, layer_url):
    """Match query and individual-feature routes, including encoded path forms."""
    if not layer_url:
        return False
    target, layer = urlsplit(url), urlsplit(layer_url)
    def port(parts):
        return None if parts.port in (None, 80 if parts.scheme == "http" else 443) else parts.port
    if ((target.hostname or "").rstrip(".").lower() != (layer.hostname or "").rstrip(".").lower()
            or port(target) != port(layer)):
        return False
    path = posixpath.normpath(unquote(target.path)).lower()
    base = posixpath.normpath(unquote(layer.path)).lower()
    return path == base + "/query" or bool(re.fullmatch(re.escape(base) + r"/\d+", path))


def map_request(url):
    parts = urlsplit(url)
    # Fetch the same selection as JSON so map extraction does not depend on the
    # API's requested output format. Names must be available for map labels.
    params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
              if key.lower() not in ("f", "outfields")]
    params.extend([("f", "json"), ("outFields", "*")])
    return urlunsplit(parts._replace(query=urlencode(params)))


def map_text(body):
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("This page has no readable map features.")
    features = data.get("features")
    if isinstance(data.get("feature"), dict):
        features = [data["feature"]]
    if not isinstance(features, list):
        raise ValueError("This page has no readable map features.")
    lines = ["Lake map features"]
    for feature in features:
        attributes = feature.get("attributes", {})
        if attributes.get("LAKENAME"):
            lines.append("Lake: " + str(attributes["LAKENAME"]))
        geometry = feature.get("geometry")
        if geometry:
            lines.append("Geometry: " + json.dumps(geometry))
    if not features:
        lines.append("No matching map features.")
    if data.get("exceededTransferLimit"):
        lines.append("Additional map features are available beyond this response.")
    return "\n".join(lines)
