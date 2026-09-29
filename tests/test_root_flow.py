import json
import os
import unittest
from unittest.mock import Mock
from urllib.parse import quote
from urllib.request import Request, urlopen

import slopbro
from webos_root_wizard import RootEngine, WizardError


class LaunchTelemetryTest(unittest.TestCase):
    def test_payload_url_enables_upstream_workaround_by_default(self):
        url = slopbro.build_self_hosted_url(
            "192.0.2.5", 8080, local_ip_override="192.0.2.10"
        )
        self.assertIn(
            "fake-service-path=" + slopbro.DEFAULT_FAKE_SERVICE_PATH, url
        )

    def setUp(self):
        self.tracker = slopbro.RequestedFilesTracker(["index-webos26.html"])
        root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wwwroot")
        self.server = slopbro.start_http_server(
            root, self.tracker, ["index-webos26.html"],
            allow_embedded_assets=False, allow_filesystem_fallback=True,
            bind_host="127.0.0.1", preferred_port=0,
        )
        self.base = "http://127.0.0.1:%d/" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_file_request_is_only_counted_after_get(self):
        with urlopen(Request(self.base + "index-webos26.html", method="HEAD")):
            pass
        self.assertFalse(self.tracker.wait_for_all(0))
        with urlopen(self.base + "index-webos26.html") as response:
            self.assertTrue(response.read())
        self.assertTrue(self.tracker.wait_for_all(1))

    def test_launch_rejection_is_reported_separately_from_files(self):
        response = {
            "returnValue": False,
            "error": "Rejected service launch - path not in trusted locations",
        }
        url = self.base + "primary-run-result?m=" + quote(json.dumps(response))
        with urlopen(url) as result:
            self.assertEqual(result.status, 204)
        self.assertEqual(self.tracker.wait_for_launch_response(1), response)
        self.assertFalse(self.tracker.wait_for_all(0))


class WizardFallbackTest(unittest.TestCase):
    def make_engine(self, primary, compatible, homebrew):
        engine = RootEngine.__new__(RootEngine)
        engine.tv_ip = "192.0.2.5"
        engine.local_ip_override = "192.0.2.10"
        engine.log = Mock()
        engine.status = Mock()
        engine.ask_user = Mock(return_value=True)
        engine.check_prerequisites = Mock()
        engine.primary_attempt = Mock(return_value=primary)
        engine.compatibility_attempt = Mock(return_value=compatible)
        engine.wait_for_homebrew = Mock(side_effect=homebrew)
        return engine

    def test_rejected_primary_launch_uses_compatible_method(self):
        engine = self.make_engine(
            (True, {"returnValue": False, "error": "trusted locations"}),
            (True, {"returnValue": True}), [True],
        )
        engine.run()
        engine.compatibility_attempt.assert_called_once_with(40)
        engine.wait_for_homebrew.assert_called_once()

    def test_missing_homebrew_after_primary_uses_compatible_method(self):
        engine = self.make_engine(
            (True, {"returnValue": True}),
            (True, {"returnValue": True}), [False, True],
        )
        engine.run()
        engine.compatibility_attempt.assert_called_once_with(40)
        self.assertEqual(engine.wait_for_homebrew.call_count, 2)

    def test_rejected_compatible_launch_is_an_error(self):
        engine = self.make_engine(
            (True, {"returnValue": False, "error": "old method rejected"}),
            (True, {"returnValue": False, "error": "new method rejected"}), [],
        )
        with self.assertRaisesRegex(WizardError, "new method rejected"):
            engine.run()
        engine.wait_for_homebrew.assert_not_called()


if __name__ == "__main__":
    unittest.main()
