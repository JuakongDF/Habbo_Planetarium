import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from email.message import Message

import probe_connection as probe
from realtime import ApiError, ConfigurationError


class ProbeTests(unittest.TestCase):
    def config(self):
        config = json.loads((Path(__file__).parent.parent / "connection.json").read_text())
        config["candidates"].insert(0, {"target_kind": "furni", "entity_id": 2147418112})
        return probe.validate_connection(config)

    def test_bc_zero_id_discovery_is_read_only_and_controls_are_optional(self):
        requests = []

        class Client:
            def request(self, method, path):
                requests.append((method, path))
                if path.endswith("/furni-bc/0"):
                    return {"value": "9223372036854775807"}
                raise ApiError("HTTP 404")

        result = probe.probe(self.config(), Client())
        self.assertTrue(result["unique_binding"])
        self.assertFalse(result["ready_for_controller"])
        self.assertTrue(result["candidates"][1]["found"])
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(method == "GET" for method, _ in requests))
        self.assertNotIn("9223372036854775807", json.dumps(result))

    def test_two_matches_are_ambiguous(self):
        class Client:
            def request(self, method, path):
                return {"value": 0}
        self.assertFalse(probe.probe(self.config(), Client())["unique_binding"])

    def test_bad_candidate_is_rejected(self):
        config = copy.deepcopy(self.config())
        config["candidates"][0]["entity_id"] = 0
        with self.assertRaises(ConfigurationError):
            probe.validate_connection(config)

    def test_public_diagnostic_does_not_send_credentials(self):
        headers = Message()
        headers["Content-Type"] = "text/html"
        client = Mock()
        client.opener.open.side_effect = HTTPError("https://www.habbo.es", 463, "", headers, None)
        result = probe.public_api_check(client, "https://www.habbo.es")
        self.assertEqual(result, {"status": 463, "content_type": "text/html"})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.headers, {"Accept": "application/json", "User-agent": probe.USER_AGENT})

    def test_setup_distinguishes_definition_scope_and_empty_profile(self):
        requests = []
        class Client:
            def request(self, method, path, **kwargs):
                requests.append((method, path, kwargs))
                if path == "":
                    return {"users": [], "furni": [], "global": ["test1", "private_name"]}
                return {"variables": {}}
        result = probe.diagnose_setup(self.config(), Client())
        self.assertEqual(result["definitions"]["scopes"]["global"]["matching_names"], ["test1"])
        self.assertTrue(all(p["exists"] for p in result["profiles"]))
        self.assertNotIn("private_name", json.dumps(result))
        self.assertTrue(all(method == "GET" for method, _, _ in requests))
        self.assertEqual(requests[1][2]["resource"], "variables_profile")


if __name__ == "__main__":
    unittest.main()
