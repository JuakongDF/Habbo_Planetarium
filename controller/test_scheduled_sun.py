import unittest

import scheduled_sun as sun


class FakeClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        return {"value": body["value"]}


class DirectVariableWriteTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.bridge = {"occupation_variable": "occupation", "altitude_variable": "altitude"}

    def test_altitude_only_change_writes_no_occupation_or_global_selector(self):
        current = {"occupation": 986369, "altitude": 4500}
        frame = {"occupation": 986369, "height": 4501}
        written = sun.write_command(self.client, "furni-bc/0", self.bridge, frame, current)
        self.assertEqual(written, ["altitude"])
        self.assertEqual(self.client.requests, [
            ("PATCH", "/furni/altitude/furni-bc/0", {"value": "4501"})
        ])
        self.assertEqual(current, {"occupation": 986369, "altitude": 4501})

    def test_frame_change_updates_altitude_before_occupation(self):
        current = {"occupation": 986369, "altitude": 4500}
        frame = {"occupation": 1051649, "height": 4501}
        written = sun.write_command(self.client, "furni-bc/0", self.bridge, frame, current)
        self.assertEqual(written, ["altitude", "occupation"])
        self.assertEqual([request[1] for request in self.client.requests], [
            "/furni/altitude/furni-bc/0", "/furni/occupation/furni-bc/0"
        ])


if __name__ == "__main__":
    unittest.main()
