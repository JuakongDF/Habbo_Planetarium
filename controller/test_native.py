import unittest

from inspect_native import decode_occupation, inspect
from realtime import ApiError


class NativeTests(unittest.TestCase):
    def test_supplied_examples_and_constant_depth_step(self):
        self.assertEqual(decode_occupation(920833), {"x": 14, "y": 13, "rotation": 1})
        self.assertEqual(decode_occupation(986369), {"x": 15, "y": 13, "rotation": 1})
        self.assertEqual(decode_occupation(920833 + 65280), {"x": 15, "y": 12, "rotation": 1})

    def test_inspection_only_reads_and_does_not_report_other_values(self):
        requests = []
        class Client:
            def request(self, method, path, **kwargs):
                requests.append((method, path))
                if not path:
                    return {"users": [], "global": [], "furni": ["native@occupation", "native@altitude", "private"]}
                if kwargs.get("resource") == "variables_profile":
                    return {"variables": {"native@occupation": {"value": "920833"},
                                          "native@altitude": {"value": "1000"},
                                          "private": {"value": "secret"}}}
                raise ApiError("HTTP 404")
        config = {"room_id": 1, "candidates": [{"target_kind": "furni-bc", "entity_id": 0}]}
        result = inspect(config, Client())
        self.assertEqual(result["native_values"]["native@occupation"]["decoded_using_supplied_formula"]["x"], 14)
        self.assertNotIn("private", str(result))
        self.assertTrue(all(method == "GET" for method, _ in requests))


if __name__ == "__main__":
    unittest.main()
