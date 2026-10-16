import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import realtime as r


def config():
    c = json.loads(Path(__file__).with_name("config.example.json").read_text())
    c["room_id"] = 42
    c["bodies"][0]["furni_id"] = 101
    return r.validate_config(c, live=True)


class FakeOpener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(json.dumps(item).encode())


class CodecAndGeometryTests(unittest.TestCase):
    def test_lossless_58_bit_packet_and_wire_format(self):
        rng = random.Random(42)
        cases = [(55, 2047, 1, 255, 2**32-1), (0, 0, 0, 1, 0)]
        cases += [(rng.randrange(56), rng.randrange(2048), rng.randrange(2),
                   rng.randrange(1, 256), rng.randrange(2**32)) for _ in range(1000)]
        for index, height, visible, observer, timestamp in cases:
            word = r.pack_state(index, height, visible, observer, timestamp)
            recovered = r.unpack_state(int(json.loads(json.dumps({"value": str(word)}))["value"]))
            self.assertEqual(recovered, dict(index=index, height=height, visible=visible,
                                             observer=observer, timestamp=timestamp))
            self.assertLess(word, 2**58)

    def test_rejects_rounded_or_malformed_values(self):
        for invalid in (1.2, float(2**58), True, "1.0", "nan", -1, 2**58):
            with self.assertRaises(r.ConfigurationError):
                r.unpack_state(invalid)
        with self.assertRaises(r.ConfigurationError):
            r.unpack_state(0)  # Missing observer, typical uninitialized binding.

    def test_cardinal_geometry_and_altitude(self):
        p = config()["projection"]
        self.assertEqual(r.project(0, 90, p)["index"], 55)
        self.assertEqual(r.project(0, 270, p)["index"], 0)
        self.assertEqual(r.project(90, 0, p)["height"], 1900)
        self.assertEqual(r.project(-90, 0, p)["height"], 100)
        for az in range(361):
            point = r.project(35, az, p)
            self.assertEqual(point["x"]+point["y"], 55)
        self.assertEqual(r.project(-0.01, 90, p)["visible"], 0)

    def test_live_configuration_rejects_placeholders_and_bad_ranges(self):
        c = config()
        for path, value in [("room_id", 0), ("hotel_origin", "http://example.com"),
                            ("poll_seconds", 0), ("update_seconds", float("nan"))]:
            broken = copy.deepcopy(c)
            broken[path] = value
            with self.assertRaises(r.ConfigurationError):
                r.validate_config(broken, live=True)
        c["projection"]["height_per_degree"] = 30
        with self.assertRaises(r.ConfigurationError):
            r.validate_config(c, live=True)


class TransportTests(unittest.TestCase):
    def client(self, *responses, write_key="write-secret"):
        opener = FakeOpener(*responses)
        return r.HabboClient(config(), "read-secret", write_key, opener=opener), opener

    def test_read_only_probe_and_exact_global_values(self):
        client, opener = self.client({"value": "1"}, {"value": "2"}, {"value": "0"}, write_key=None)
        self.assertEqual(client.preflight(), dict(enabled=True, observer=2, bindings=1))
        self.assertTrue(all(q.method == "GET" for q in opener.requests))
        self.assertTrue(all("X-wired-write-key" not in q.headers for q in opener.requests))
        self.assertTrue(opener.requests[-1].full_url.endswith("/furni/astra_state/furni/101"))

    def test_unknown_observer_fails_without_writes(self):
        client, opener = self.client({"value": 1}, {"value": 255})
        with self.assertRaises(r.ConfigurationError):
            client.controls()
        self.assertTrue(all(q.method == "GET" for q in opener.requests))

    def test_batch_payload_and_headers(self):
        client, opener = self.client({"results": [{"op_id": "101", "status": 200}]})
        word = r.pack_state(55, 1900, 1, 2, 1792065600)
        self.assertEqual(client.publish([{"furni_id": 101, "value": str(word)}]), 1)
        request = opener.requests[0]
        self.assertTrue(request.full_url.endswith("/furni/astra_state/batch"))
        self.assertEqual(request.headers["X-wired-write-key"], "write-secret")
        self.assertEqual(request.headers["X-wired-read-key"], "read-secret")
        self.assertEqual(json.loads(request.data)["requests"][0],
                         dict(op_id="101", method="PATCH", path="furni/101", body={"value": str(word)}))

    def test_partial_failures_and_missing_results_are_not_success(self):
        rows = [{"furni_id": 101, "value": "1"}, {"furni_id": 102, "value": "2"}]
        for result, exception in [
            ({"results": [{"op_id": "101", "status": 200}, {"op_id": "102", "status": 429}]}, r.RetryLater),
            ({"results": [{"op_id": "101", "status": 200}, {"op_id": "102", "status": 404}]}, r.ApiError),
            ({"results": [{"op_id": "101", "status": 200}]}, r.ApiError),
            ({"results": [{"op_id": "101", "status": 200}]*2}, r.ApiError),
        ]:
            client, _ = self.client(result)
            with self.assertRaises(exception):
                client.publish(rows)

    def test_http_retry_after_and_redacted_errors(self):
        error = HTTPError("https://example.com", 429, "secret", {"Retry-After": "17"}, None)
        client, _ = self.client(error)
        with self.assertRaises(r.RetryLater) as caught:
            client.controls()
        self.assertEqual(caught.exception.delay, 17)
        self.assertNotIn("secret", str(caught.exception))
        epoch = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
        self.assertEqual(r.retry_after("Thu, 15 Oct 2026 00:00:30 GMT", epoch), 30)
        self.assertEqual(r.retry_after("NaN"), 1)

    def test_credentials_cannot_follow_redirects(self):
        self.assertIsNone(r.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))


class BudgetAndLoopTests(unittest.TestCase):
    def test_burst_and_sustained_windows(self):
        now = [0.0]
        budget = r.Budget(lambda: now[0])
        budget.acquire({"write": 24})
        with self.assertRaises(r.RetryLater) as caught:
            budget.acquire({"write": 1})
        self.assertGreaterEqual(caught.exception.delay, 10)
        for moment in (10.001, 20.002, 30.003):
            now[0] = moment
            budget.acquire({"write": 24})
        now[0] = 40.004
        with self.assertRaises(r.RetryLater) as caught:
            budget.acquire({"write": 1})
        self.assertGreater(caught.exception.delay, 19)
        now[0] = 60.001
        budget.acquire({"write": 1})

    def test_disable_and_cooldown_recompute_without_catchup(self):
        now = [0.0]
        frames, publications = [], []

        class Ephemeris:
            def frame(self, observer, timestamp):
                frames.append((observer, timestamp))
                return [{"value": str(r.pack_state(1, 1000, 1, observer, int(timestamp)))}]

        class Client:
            def controls(self):
                return (now[0] < 25, 1 if now[0] < 20 else 2)

            def publish(self, rows):
                publications.append(now[0])
                if len(publications) == 1:
                    raise r.RetryLater("HTTP 429", 20)
                return 1

        with patch.object(r.time, "monotonic", lambda: now[0]), \
             patch.object(r.time, "time", lambda: 1792065600+now[0]), \
             patch.object(r.time, "sleep", lambda delay: now.__setitem__(0, now[0]+delay)), \
             patch.object(r, "log"):
            r.run_loop(config(), Ephemeris(), Client(), 40)
        self.assertEqual(publications, [0, 20])
        self.assertEqual(frames, [(1, 1792065600), (2, 1792065620)])


if __name__ == "__main__":
    unittest.main()
