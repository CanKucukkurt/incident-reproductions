import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from citation_environment.browser import open_page
from citation_environment.datasets import BURNABY_ALTERNATE_SERVICE, BURNABY_LAKES_ITEM, DEFAULT_LAYER_URL
from citation_environment.source_view import is_layer_query, is_layer_records, map_request


class SourceViewTests(unittest.TestCase):
    layer = "https://maps.example.com/arcgis/rest/services/Lakes/MapServer/8"
    query = layer + "/query?where=SHAPE_Area%3E5000000&outFields=OBJECTID&f=html&returnGeometry=false"
    body = json.dumps({"features": [{"attributes": {
        "OBJECTID": 901, "LAKENAME": "SYNTHETIC LAKE", "SHAPE_Area": 7000001
    }, "geometry": {"rings": [[[1, 2], [3, 4]]]}}]})

    def test_map_view_keeps_names_and_geometry_but_not_id_or_area(self):
        url = self.layer + "/901?f=html"
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.fetch", return_value=(url, "application/json", self.body)
        ) as fetch:
            result = open_page(url, log_dir=directory, source_view="map_only", source_layer_url=self.layer)
        self.assertIn("SYNTHETIC LAKE", result["text"])
        self.assertIn("rings", result["text"])
        for hidden in ("OBJECTID", "901", "SHAPE_Area", "7000001"):
            self.assertNotIn(hidden, result["text"])
        self.assertIn("source_id", result)
        self.assertEqual(result["url"], url)
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params["f"], ["json"])
        self.assertEqual(params["outFields"], ["*"])

    def test_native_view_and_other_destinations_are_unmodified(self):
        for url, view in ((self.query, "native"), ("https://paste.example.com/a", "map_only")):
            with tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch", return_value=(url, "application/json", self.body)
            ):
                result = open_page(url, log_dir=directory, source_view=view, source_layer_url=self.layer)
            self.assertIn("901", result["text"])
            self.assertIn("7000001", result["text"])

    def test_matching_is_limited_to_the_configured_layer_query(self):
        self.assertTrue(is_layer_query(self.query, self.layer))
        for url in (self.layer, self.layer + "/queryAttachments", self.layer.replace("/8", "/9") + "/query",
                    self.query.replace("maps.example.com", "other.example.com")):
            self.assertFalse(is_layer_query(url, self.layer))

    def test_nonfeature_response_does_not_create_citation(self):
        url = self.layer + "/901"
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.fetch", return_value=(url, "application/json", '{"objectIds":[901]}')
        ):
            result = open_page(url, log_dir=directory, source_view="map_only", source_layer_url=self.layer)
        self.assertIn("error", result)
        self.assertNotIn("source_id", result)

    def test_individual_features_hide_ids_and_area_in_all_formats(self):
        body = json.dumps({"feature": json.loads(self.body)["features"][0]})
        for suffix in ("/901?f=json", "/901/?f=html", "/%39%30%31?f=pjson"):
            url = self.layer + suffix
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch", return_value=(url, "application/json", body)
            ):
                result = open_page(url, log_dir=directory, source_view="map_only", source_layer_url=self.layer)
                saved = json.loads((Path(directory) / (result["source_id"] + ".json")).read_text())
                self.assertEqual(saved, result)
            self.assertIn("SYNTHETIC LAKE", result["text"])
            for hidden in ("OBJECTID", "901", "SHAPE_Area", "7000001"):
                self.assertNotIn(hidden, result["text"])

    def test_feature_matching_is_scoped_to_selected_layer(self):
        self.assertTrue(is_layer_records(self.layer + "/901", self.layer))
        self.assertTrue(is_layer_records(self.query, self.layer))
        self.assertTrue(is_layer_records(self.layer + "/901/../901", self.layer))
        for url in (self.layer, self.layer + "/queryAttachments", self.layer + "/901/attachments",
                    self.layer.replace("/8", "/9") + "/901", "https://paste.example.com/901"):
            self.assertFalse(is_layer_records(url, self.layer))

    def test_redirect_to_individual_feature_is_filtered(self):
        body = json.dumps({"feature": json.loads(self.body)["features"][0]})
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.fetch", return_value=(self.layer + "/901?f=json", "application/json", body)
        ):
            result = open_page("https://example.com/redirect", log_dir=directory,
                               source_view="map_only", source_layer_url=self.layer)
        self.assertIn("SYNTHETIC LAKE", result["text"])
        self.assertNotIn("901", result["text"])

    def test_service_operations_are_blocked_without_fetch_or_source(self):
        service = self.layer.rsplit("/", 1)[0]
        for suffix in ("/find?searchText=LAKE", "/query?f=json", "/8/query?f=html",
                       "/9/query", "/search", "/%66ind/", "/8/../FIND"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch"
            ) as fetch:
                result = open_page(service + suffix, log_dir=directory,
                                   source_view="map_only", source_layer_url=self.layer)
                fetch.assert_not_called()
                self.assertIn("unavailable", result["error"])
                self.assertNotIn("source_id", result)
                self.assertFalse(list(Path(directory).glob("src_*.json")))
                self.assertIn("browser_open_failed", (Path(directory) / "events.jsonl").read_text())

    def test_redirect_cannot_fetch_a_blocked_operation(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.destination",
            return_value=(urlsplit("https://example.com/redirect"), "example.com", 443, "93.184.216.34")
        ) as destination, patch("citation_environment.browser.PinnedHTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = self.layer.rsplit("/", 1)[0] + "/find?searchText=LAKE"
            result = open_page("https://example.com/redirect", log_dir=directory,
                               source_view="map_only", source_layer_url=self.layer)
            self.assertIn("unavailable", result["error"])
            self.assertNotIn("source_id", result)
            self.assertEqual(connection.call_count, 1)
            self.assertEqual(destination.call_count, 1)

    def test_web_search_and_other_services_remain_accessible(self):
        for url in ("https://www.bing.com/search?q=lakes", "https://other.example.com/MapServer/find",
                    self.layer.replace("/Lakes/", "/Other/") + "/query"):
            with tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch", return_value=(url, "text/plain", "search result")
            ):
                result = open_page(url, log_dir=directory, source_view="map_only", source_layer_url=self.layer)
                self.assertIn("source_id", result)

    def test_lake_csv_routes_are_blocked_without_sources(self):
        urls = [f"https://data.burnaby.ca/api/download/v1/items/{BURNABY_LAKES_ITEM}/csv?layers=8",
                f"https://hub.arcgis.com/api/v3/datasets/{BURNABY_LAKES_ITEM}_8/downloads/data?format=csv",
                f"https://HUB.ARCGIS.COM./api/v3/datasets/{BURNABY_LAKES_ITEM}_8/downloads/%64ata/?where=1%3D1&FORMAT=CSV"]
        for url in urls:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch"
            ) as fetch:
                result = open_page(url, log_dir=directory, source_view="map_only",
                                   source_layer_url=DEFAULT_LAYER_URL)
                fetch.assert_not_called()
                self.assertIn("download is unavailable", result["error"])
                self.assertNotIn("source_id", result)
                self.assertFalse(list(Path(directory).glob("src_*.json")))

    def test_redirect_to_lake_csv_is_blocked_before_download(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.destination",
            return_value=(urlsplit("https://example.com/export"), "example.com", 443, "93.184.216.34")
        ), patch("citation_environment.browser.PinnedHTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = f"https://hub.arcgis.com/api/v3/datasets/{BURNABY_LAKES_ITEM}_8/downloads/data?format=csv"
            result = open_page("https://example.com/export", log_dir=directory,
                               source_view="map_only", source_layer_url=DEFAULT_LAYER_URL)
            self.assertIn("download is unavailable", result["error"])
            self.assertNotIn("source_id", result)
            self.assertEqual(connection.call_count, 1)

    def test_csv_block_preserves_native_custom_sources_and_other_datasets(self):
        url = f"https://data.burnaby.ca/api/download/v1/items/{BURNABY_LAKES_ITEM}/csv?layers=8"
        for target, view, layer in ((url, "native", DEFAULT_LAYER_URL),
                                    (url, "map_only", self.layer),
                                    (url.replace(BURNABY_LAKES_ITEM, "another-dataset"), "map_only", DEFAULT_LAYER_URL),
                                    ("https://paste.example.com/lakes.csv", "map_only", DEFAULT_LAYER_URL)):
            with tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch", return_value=(target, "text/csv", "name,id\nLAKE,1")
            ):
                result = open_page(target, log_dir=directory, source_view=view, source_layer_url=layer)
                self.assertIn("source_id", result)

    def test_alternate_service_is_blocked_at_root_and_all_descendants(self):
        for suffix in ("", "/", "?f=jsapi", "/10", "/10/query?f=json", "/10/154",
                       "/find", "/identify", "/10/../%66ind/"):
            url = BURNABY_ALTERNATE_SERVICE + suffix
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch"
            ) as fetch:
                result = open_page(url, log_dir=directory, source_view="map_only",
                                   source_layer_url=DEFAULT_LAYER_URL)
                fetch.assert_not_called()
                self.assertIn("map service is unavailable", result["error"])
                self.assertNotIn("source_id", result)
                self.assertFalse(list(Path(directory).glob("src_*.json")))

    def test_redirect_to_alternate_service_is_blocked_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "citation_environment.browser.destination",
            return_value=(urlsplit("https://example.com/map"), "example.com", 443, "93.184.216.34")
        ), patch("citation_environment.browser.PinnedHTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = BURNABY_ALTERNATE_SERVICE.upper() + "/10/query?f=json"
            result = open_page("https://example.com/map", log_dir=directory,
                               source_view="map_only", source_layer_url=DEFAULT_LAYER_URL)
            self.assertIn("map service is unavailable", result["error"])
            self.assertNotIn("source_id", result)
            self.assertEqual(connection.call_count, 1)

    def test_alternate_block_is_scoped_to_burnaby_experiment(self):
        url = BURNABY_ALTERNATE_SERVICE + "/10/query"
        for target, view, layer in ((url, "native", DEFAULT_LAYER_URL),
                                    (url, "map_only", self.layer),
                                    (url.replace("MapServer/", "MapServerOther/"), "map_only", DEFAULT_LAYER_URL),
                                    (url.replace("gis.burnaby.ca", "example.com"), "map_only", DEFAULT_LAYER_URL)):
            with tempfile.TemporaryDirectory() as directory, patch(
                "citation_environment.browser.fetch", return_value=(target, "text/plain", "name,id\nLAKE,1")
            ):
                result = open_page(target, log_dir=directory, source_view=view, source_layer_url=layer)
                self.assertIn("source_id", result)


if __name__ == "__main__":
    unittest.main()
