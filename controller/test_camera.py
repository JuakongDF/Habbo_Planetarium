import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import camera_controller as camera
import realtime as r
import scheduled_sun as sun


def configuration():
    config = r.validate_config(json.loads(Path(__file__).with_name("config.habbo-es.json").read_text()), live=True)
    sun.validate_constellations(config, live=True)
    sun.validate_planets(config)
    camera.validate_camera(config)
    return config


class ProjectionTests(unittest.TestCase):
    def project(self, altitude, azimuth, view):
        config = configuration()
        bridge = config["motion_bridge"]
        return r.project_screen_ellipse(altitude, azimuth,
                                       {**config["projection"], "horizon_height": 3500},
                                       bridge["screen_aspect_ratio"],
                                       bridge["native_height_units_per_tile"], view=view)

    def test_all_eight_headings_and_their_left_right_boundaries(self):
        for view in range(8):
            heading = view * 45
            with self.subTest(view=view):
                front = self.project(0, heading, view)
                back = self.project(0, (heading+180) % 360, view)
                left = self.project(0, (heading-90) % 360, view)
                right = self.project(0, (heading+90) % 360, view)
                self.assertEqual((front["visible"], back["visible"]), (1, 0))
                self.assertEqual((left["x"], left["y"], left["visible"]), (1, 53, 1))
                self.assertEqual((right["x"], right["y"], right["visible"]), (54, 0, 1))
                self.assertEqual(front["height"], 3500)
                self.assertEqual(self.project(-30, heading, view)["visible"], 1)
                self.assertEqual(self.project(-30, (heading+180) % 360, view)["visible"], 0)

    def test_zenith_map_has_north_up_east_right_and_no_lower_hemisphere(self):
        up = self.project(90, 123, 8)
        self.assertEqual((up["x"], up["y"], up["height"], up["visible"]), (28, 26, 3500, 1))
        self.assertEqual(self.project(-90, 123, 8)["visible"], 0)
        self.assertEqual(self.project(-.01, 0, 8)["visible"], 0)
        self.assertEqual(self.project(0, 0, 8)["height"], 6000)
        self.assertEqual(self.project(0, 180, 8)["height"], 1000)
        self.assertEqual(self.project(0, 90, 8)["x"], 54)
        self.assertEqual(self.project(0, 270, 8)["x"], 1)

    def test_legacy_view_still_overlays_and_keeps_both_hemispheres(self):
        for altitude in (-90, -30, 0, 30, 90):
            ne = self.project(altitude, 45, -1)
            se = self.project(altitude, 135, -1)
            self.assertEqual(ne, se)
            self.assertEqual(ne["visible"], 1)
        for view in (-2, 9, .5, True):
            with self.assertRaises(r.ConfigurationError):
                self.project(0, 0, view)


class Ephemeris:
    def apparent_altaz(self, observer, body, timestamp):
        return (30 if body == "sun" else -30), (45 + timestamp/60) % 360

    def moon_phase(self, observer, timestamp):
        return {"illumination": .3, "waxing": True, "elongation_degrees": 60}

    def fixed_star_altaz(self, observer, ra, dec, timestamp):
        return dec, (ra + timestamp/60) % 360


class Client:
    def __init__(self, config, now, controls=None, budget=False):
        self.config, self.now = config, now
        self.controls_at = controls or (lambda t: (True, 1, 0, 0))
        self.polls, self.writes, self.clears = [], [], 0
        self.budget = r.Budget(lambda: now[0]) if budget else None
        self.values = {}
        for item in config["bodies"] + [config["moon"]] + config["constellations"]:
            target = f"{item.get('target_kind', 'furni')}/{item['furni_id']}"
            self.values[target] = {"occupation": 28*65536+26*256,
                                   "altitude": 3500, "visible": 1, "state": 2}

    def controls_with_update_request(self):
        self.polls.append(self.now[0])
        return self.controls_at(self.now[0])[:3]

    def clear_update_request(self):
        self.clears += 1

    def request(self, method, path, **kwargs):
        if path == "":
            return {"global": ["astra_view"], "furni": ["occupation", "altitude", "state", "visible"]}
        if path == "/global/astra_view":
            return {"value": str(self.controls_at(self.now[0])[3])}
        return {"variables": {key: {"value": str(value)} for key, value in
                              self.values[path.removeprefix("/furni/")].items()}}

    def publish_variable(self, variable, rows):
        if self.budget:
            self.budget.acquire({"write": len(rows)+1, "batch": 1, "batch_write": len(rows)})
        self.writes.append((self.now[0], variable, copy.deepcopy(rows)))
        for row in rows:
            self.values[row["target"]][variable] = int(row["value"])
        return len(rows)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.now = [0.0]
        self.client = Client(self.config, self.now)

    def run_loop(self, duration):
        with patch.object(camera.time, "monotonic", lambda: self.now[0]), \
             patch.object(camera.time, "time", lambda: self.now[0]), \
             patch.object(camera.time, "sleep", lambda d: self.now.__setitem__(0, self.now[0]+d)), \
             patch.object(camera, "log") as logger:
            camera.run_camera(self.config, self.client, Ephemeris(), duration)
        return logger

    def test_all_bindings_are_required_before_any_write(self):
        del self.client.values["furni/1430121204"]["visible"]
        with self.assertRaises(r.ConfigurationError):
            self.run_loop(10)
        self.assertFalse(self.client.writes)

    def test_linked_galaxy_and_sagittarius_share_view_and_keep_sprite_offsets(self):
        profiles = camera.read_camera_profiles(self.client, self.config)
        for view in range(-1, 9):
            entries = camera.sample_frames(self.config, Ephemeris(), profiles, 1, view, 0)
            frames = {entry["profile"]["target"]: entry["frame"] for entry in entries}
            sag, galaxy = frames["furni/1430121204"], frames["furni/1241761742"]
            self.assertEqual((sag["x"], sag["y"], sag["visible"]),
                             (galaxy["x"], galaxy["y"], galaxy["visible"]))
            self.assertEqual(sag["height"]-galaxy["height"], 1000)
            self.assertEqual(sag["state"], 0)
            self.assertEqual(galaxy["state"], 0)

    def test_full_refresh_hides_before_positions_and_reveals_after(self):
        profiles = camera.read_camera_profiles(self.client, self.config)
        frames = camera.sample_frames(self.config, Ephemeris(), profiles, 1, 8, 0)
        jobs = camera.build_jobs(self.config, frames, force=True)
        self.assertTrue(all(len(job["rows"]) <= 10 for job in jobs))
        self.assertEqual(jobs[0]["variable"], "visible")
        self.assertTrue(all(row["value"] == "0" for row in jobs[0]["rows"]))
        self.assertEqual(jobs[-1]["variable"], "visible")
        self.assertTrue(all(row["value"] == "1" for row in jobs[-1]["rows"]))
        for variable in ("occupation", "altitude", "visible"):
            rows = [row for job in jobs if job["variable"] == variable for row in job["rows"]]
            self.assertEqual(len({row["target"] for row in rows}), 16)
        state_targets = {row["target"] for job in jobs if job["variable"] == "state" for row in job["rows"]}
        self.assertNotIn("furni-bc/0", state_targets)
        self.assertNotIn("furni/165326448", state_targets)
        for entry in frames:
            entry["profile"]["values"].update(entry["values"])
        self.assertEqual(camera.build_jobs(self.config, frames), [])
        self.assertTrue(camera.build_jobs(self.config, frames, force=True))

    def test_latest_view_replaces_stale_jobs_during_write_cooldown(self):
        self.client.controls_at = lambda t: (True, 1, 0, 0 if t < 5 else 6 if t < 10 else 8)
        publish = self.client.publish_variable
        attempted = []

        def fail_once(variable, rows):
            attempted.append(self.now[0])
            if len(attempted) == 1:
                raise r.RetryLater("HTTP 429", 20)
            return publish(variable, rows)

        self.client.publish_variable = fail_once
        self.run_loop(21)
        self.assertEqual(self.client.polls, [0, 5, 10, 15, 20])
        self.assertTrue(all(t >= 20 for t, _, _ in self.client.writes))
        # Sun is above the horizon; Moon is below it. Only the final zenith view is applied.
        self.assertEqual(self.client.values["furni-bc/0"]["visible"], 1)
        self.assertEqual(self.client.values["furni/523624626"]["visible"], 0)
        profiles = camera.read_camera_profiles(self.client, self.config)
        expected = camera.sample_frames(self.config, Ephemeris(), profiles, 1, 8, 20)
        for entry in expected:
            for name, value in entry["values"].items():
                self.assertEqual(self.client.values[entry["profile"]["target"]][name], value)

    def test_disable_and_invalid_view_stop_writes_then_reenable_refreshes(self):
        for middle in ((False, 1, 0, 6), (True, 1, 0, 9)):
            self.now[0] = 0
            self.client = Client(self.config, self.now,
                                 lambda t: (True, 1, 0, 0) if t < 5 else middle if t < 20 else (True, 1, 0, 8))
            self.run_loop(21)
            self.assertFalse(any(5 <= t < 20 for t, _, _ in self.client.writes))
            self.assertEqual(self.client.values["furni/523624626"]["visible"], 0)

    def test_partial_batch_failure_is_retried_before_any_reveal(self):
        publish = self.client.publish_variable
        attempts = []

        def partial_once(variable, rows):
            attempts.append((variable, copy.deepcopy(rows)))
            if len(attempts) == 1:
                publish(variable, rows[:1])
                raise r.ApiError("partial batch failure")
            return publish(variable, rows)

        self.client.publish_variable = partial_once
        self.run_loop(2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertTrue(any(variable == "occupation" for _, variable, _ in self.client.writes))

    def test_real_budget_preserves_polling_and_completes_a_full_refresh(self):
        self.client = Client(self.config, self.now, budget=True)
        logger = self.run_loop(61)
        self.assertTrue(any(call.args[0] == "camera_frame_complete" for call in logger.call_args_list))
        self.assertTrue(any(call.args[0] == "camera_write_retry" for call in logger.call_args_list))
        self.assertTrue(all(b-a <= 5.001 for a, b in zip(self.client.polls, self.client.polls[1:])))

    def test_update_request_forces_a_full_refresh_and_is_acknowledged_once(self):
        self.client.controls_at = lambda t: (True, 1, 7 if t >= 5 else 0, 0)
        logger = self.run_loop(6)
        self.assertEqual(self.client.clears, 1)
        full = [call for call in logger.call_args_list
                if call.args[0] == "camera_frame" and call.kwargs["full_refresh"]]
        self.assertEqual(len(full), 2)


if __name__ == "__main__":
    unittest.main()
