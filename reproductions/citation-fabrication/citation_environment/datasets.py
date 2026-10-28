"""Public source selection. Contains endpoints and schema, never answer records."""

import posixpath
from urllib.parse import unquote, urlsplit

BURNABY_SERVICE = "https://gis.burnaby.ca/arcgis/rest/services/OpenData/OpenData3/MapServer"
BURNABY_ALTERNATE_SERVICE = "https://gis.burnaby.ca/arcgis/rest/services/BurnabyMap/BBY_PUBLIC_TOC/MapServer"
DEFAULT_MAP_URL = BURNABY_SERVICE + "?f=jsapi"
DEFAULT_LAYER_URL = BURNABY_SERVICE + "/8"
BURNABY_LAKES_ITEM = "25210ce5a70c487d8481e856870a9a43"


def url_in_service(url, service_url):
    """Match the service root and descendants, including encoded path forms."""
    parts, service = urlsplit(url), urlsplit(service_url)
    path = posixpath.normpath(unquote(parts.path)).lower()
    root = posixpath.normpath(unquote(service.path)).lower()
    return ((parts.hostname or "").rstrip(".").lower() == (service.hostname or "").rstrip(".").lower()
            and (path == root or path.startswith(root + "/")))
