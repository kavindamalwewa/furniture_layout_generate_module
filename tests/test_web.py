import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from furniture_layout.web import DemoHandler


class QuietHandler(DemoHandler):
    def log_message(self, format, *args):
        pass


class StalePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def generate(self, headers):
        request = {"roomId": "s", "roomType": "study", "scale": {"confirmed": True}, "count": 1, "seed": 1,
                   "polygon": {"outer": [[0, 0], [4, 0], [4, 3], [0, 3]], "holes": []},
                   "furniture": [{"id": "desk", "width": 1.2, "depth": 0.6, "required": True}]}
        sent = urllib.request.Request(self.base + "/api/layouts/generate", data=json.dumps(request).encode(),
                                      headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(sent) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_page_is_stamped_with_its_build(self):
        page = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertNotIn("__PAGE_BUILD__", page)
        self.assertRegex(page, r"const PAGE_BUILD='[0-9a-f]{12}'")

    def test_a_tab_running_an_older_page_is_asked_to_reload(self):
        page = urllib.request.urlopen(self.base + "/").read().decode()
        build = re.search(r"const PAGE_BUILD='([0-9a-f]{12})'", page).group(1)
        from_page = {"Referer": self.base + "/"}
        self.assertEqual(200, self.generate({**from_page, "X-Page-Build": build})[0])
        status, body = self.generate(from_page)  # a page from before the build stamp sends none
        self.assertEqual(409, status)
        self.assertIn("Ctrl+F5", body["error"])
        self.assertEqual(409, self.generate({**from_page, "X-Page-Build": "0" * 12})[0])

    def test_api_clients_without_a_page_are_unaffected(self):
        self.assertEqual(200, self.generate({})[0])
        self.assertEqual(200, self.generate({"Referer": "https://example.com/tool"})[0])


if __name__ == "__main__":
    unittest.main()
