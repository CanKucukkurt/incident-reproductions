import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from citation_environment.browser import BrowserError, PageText, PinnedHTTPConnection, destination, fetch, open_page
from citation_environment.config import compose_file


def address(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]


class BrowserTests(unittest.TestCase):
    def test_https_uses_canonical_host_header(self):
        # Some public GIS servers close connections when default :443 is included.
        for port, expected in ((443, b"Host: maps.example.com\r\n"),
                               (8443, b"Host: maps.example.com:8443\r\n")):
            connection = PinnedHTTPConnection("maps.example.com", port, "93.184.216.34", True)
            with patch.object(connection, "connect"):
                with patch("socket.socket") as sock:
                    connection.sock = sock.return_value
                    connection.request("GET", "/")
                    payload = b"".join(call.args[0] for call in connection.sock.sendall.call_args_list)
            self.assertIn(expected, payload)
            if port == 443:
                self.assertNotIn(b"Host: maps.example.com:443", payload)

    def test_rejects_local_urls(self):
        for url in ("file:///mnt/data/a.txt", "/mnt/data/a.txt", "http://localhost:8765/a",
                    "http://user:pass@example.com", "http://example.com/\na"):
            with self.subTest(url=url), self.assertRaises(BrowserError):
                destination(url)

    def test_rejects_private_and_mixed_dns_results(self):
        for ips in (address("127.0.0.1"), address("10.1.2.3"), address("169.254.169.254"),
                    address("93.184.216.34") + address("192.168.1.1")):
            with patch("socket.getaddrinfo", return_value=ips), self.assertRaises(BrowserError):
                destination("http://example.com")

    def test_redirect_to_private_destination_is_blocked(self):
        with patch("socket.getaddrinfo", side_effect=[address("93.184.216.34"), address("127.0.0.1")]), \
                patch("citation_environment.browser.PinnedHTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = "http://127.0.0.1/secret"
            with self.assertRaises(BrowserError):
                fetch("https://example.com/")
            self.assertEqual(connection.call_count, 1)

    def test_failure_never_gets_source_id(self):
        with tempfile.TemporaryDirectory() as directory:
            result = open_page("file:///private.txt", log_dir=directory)
            self.assertIn("error", result)
            self.assertNotIn("source_id", result)
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
            self.assertEqual([e["event"] for e in events], ["browser_open_attempt", "browser_open_failed"])

    def test_html_hides_scripts_and_keeps_links(self):
        page = PageText("https://example.com/map/")
        page.feed('<h1>Map</h1><script>secret_id=5</script><a href="/api">API</a>')
        self.assertEqual(page.text, ["Map", "API"])
        self.assertEqual(page.links, ["https://example.com/api"])

    def test_rss_search_results_have_text_links_and_source(self):
        rss = '<rss><channel><item><title>Example</title><link>https://example.com</link>' \
              '<description>Example page</description></item></channel></rss>'
        with tempfile.TemporaryDirectory() as directory, \
                patch("citation_environment.browser.fetch", return_value=("https://search.test", "text/xml", rss)):
            result = open_page("https://search.test", log_dir=directory)
        self.assertEqual(result["links"], ["https://example.com"])
        self.assertIn("Example page", result["text"])
        self.assertIn("source_id", result)


class ConfigurationTests(unittest.TestCase):
    def test_single_compose_file(self):
        self.assertTrue(Path(compose_file()).is_file())
        self.assertEqual(Path(compose_file()).name, "compose.yaml")


if __name__ == "__main__":
    unittest.main()
