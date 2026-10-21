import unittest
from unittest.mock import patch

import scheduled_sun as sun


class FakeClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None, costs=None):
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

    def test_projection_tracks_below_horizon_to_nadir(self):
        projection = {"room_size": 56, "horizon_height": 3500}
        nadir = sun.project_screen_ellipse(-90, 180, projection, 19/16, 107.95)
        horizon = sun.project_screen_ellipse(0, 180, projection, 19/16, 107.95)
        zenith = sun.project_screen_ellipse(90, 180, projection, 19/16, 107.95)
        self.assertEqual((nadir["height"], horizon["height"], zenith["height"]),
                         (1000, 3500, 6000))
        self.assertEqual((nadir["visible"], horizon["visible"], zenith["visible"]),
                         (1, 1, 1))

    def test_moon_phase_split_uses_full_for_gibbous_and_crescent_for_crescent(self):
        self.assertEqual(sun.moon_display_state(0.5, True, -33.45),
                         {"state": 0, "rotation": 0})
        self.assertEqual(sun.moon_display_state(0.49, True, -33.45),
                         {"state": 1, "rotation": 0})
        self.assertEqual(sun.moon_display_state(0.49, False, -33.45),
                         {"state": 1, "rotation": 1})

    def test_moon_rotation_mirrors_between_hemispheres(self):
        self.assertEqual(sun.moon_display_state(0.2, True, 40),
                         {"state": 1, "rotation": 1})
        self.assertEqual(sun.moon_display_state(0.2, False, 40),
                         {"state": 1, "rotation": 0})

    def test_moon_frame_uses_live_position_phase_and_rotation(self):
        class FakeEphemeris:
            def apparent_altaz(self, observer, body, timestamp):
                self.assertions = (observer, body, timestamp)
                return -12.5, 270

            def moon_phase(self, observer, timestamp):
                return {"illumination": 0.25, "waxing": True,
                        "elongation_degrees": 60}

        config = {
            "moon": {"target_kind": "furni", "furni_id": 523624626,
                     "state_variable": "state"},
            "observers": {"1": {"latitude": -33.45}},
            "projection": {"room_size": 56, "diagonal_cells": 54,
                           "diagonal_start_x": 1, "diagonal_start_y": 53},
            "motion_bridge": {"occupation_variable": "occupation",
                              "altitude_variable": "altitude",
                              "screen_aspect_ratio": 19/16,
                              "screen_horizon_height": 3500,
                              "native_height_units_per_tile": 107.95},
        }
        frame = sun.moon_command_frame(FakeEphemeris(), config, 1, 123)
        self.assertEqual((frame["state"], frame["rotation"]), (1, 0))
        self.assertGreater(frame["height"], 0)
        self.assertEqual(frame["x"] + frame["y"], 54)

    def test_east_and_west_map_to_correct_diagonal_ends(self):
        projection = {"room_size": 56, "diagonal_cells": 54,
                      "diagonal_start_x": 1, "diagonal_start_y": 53,
                      "horizon_height": 3500}
        east = sun.project_screen_ellipse(0, 90, projection, 19/16, 107.95)
        west = sun.project_screen_ellipse(0, 270, projection, 19/16, 107.95)
        self.assertEqual((east["index"], east["x"], east["y"]), (53, 54, 0))
        self.assertEqual((west["index"], west["x"], west["y"]), (0, 1, 53))

    def test_54_cell_diagonal_has_two_middle_cells(self):
        projection = {"room_size": 56, "diagonal_cells": 54,
                      "diagonal_start_x": 1, "diagonal_start_y": 53,
                      "horizon_height": 3500}
        midpoint = sun.project_screen_ellipse(0, 0, projection, 19/16, 107.95)
        self.assertEqual((midpoint["x"], midpoint["y"], midpoint["index"]), (28, 26, 27))
        self.assertEqual((1+26, 53-26), (27, 27))

    def test_intrinsic_offsets_keep_visible_body_ranges_aligned(self):
        class FakeEphemeris:
            def apparent_altaz(self, observer, body, timestamp):
                return self.altitude, 180

        config = {
            "bodies": [{"name": "sun", "intrinsic_altitude_offset": 700}],
            "projection": {"room_size": 56, "diagonal_cells": 54,
                           "diagonal_start_x": 1, "diagonal_start_y": 53},
            "motion_bridge": {"occupation_variable": "occupation",
                              "altitude_variable": "altitude",
                              "screen_aspect_ratio": 19/16,
                              "screen_horizon_height": 3500,
                              "native_height_units_per_tile": 107.95},
        }
        ephemeris = FakeEphemeris()
        for altitude, expected in ((-90, 300), (0, 2800), (90, 5300)):
            ephemeris.altitude = altitude
            self.assertEqual(sun.command_frame(ephemeris, config, 1, 0, 0)["height"], expected)

        config["moon"] = {"intrinsic_altitude_offset": 800,
                          "full_moon_illumination_threshold": 0.5}
        config["observers"] = {"1": {"latitude": -33.45}}
        ephemeris.moon_phase = lambda observer, timestamp: {
            "illumination": 0.6, "waxing": True, "elongation_degrees": 100
        }
        for altitude, expected in ((-90, 200), (0, 2700), (90, 5200)):
            ephemeris.altitude = altitude
            self.assertEqual(sun.moon_command_frame(ephemeris, config, 1, 0)["height"], expected)

    def test_moon_writer_updates_only_changed_fields(self):
        current = {"occupation": 986369, "altitude": 3958, "state": 0}
        frame = {"occupation": 986369, "height": 3958, "state": 1}
        written = sun.write_moon_command(self.client, "furni/523624626", self.bridge,
                                         "state", frame, current)
        self.assertEqual(written, ["state"])
        self.assertEqual(self.client.requests, [
            ("PATCH", "/furni/state/furni/523624626", {"value": "1"})
        ])

    def test_lunar_phase_marks_waxing_and_illumination(self):
        phase = sun.lunar_phase(60, 10, 70)
        self.assertAlmostEqual(phase["illumination"], 0.25)
        self.assertTrue(phase["waxing"])
        self.assertAlmostEqual(phase["elongation_degrees"], 60)

    def test_fixed_constellation_frame_uses_fixed_sky_position_offset_and_rotation(self):
        class FakeEphemeris:
            def fixed_star_altaz(self, observer, ra, dec, timestamp):
                self.requested = (observer, ra, dec, timestamp)
                return 0, 90

        config = {
            "projection": {"room_size": 56, "diagonal_cells": 54,
                           "diagonal_start_x": 1, "diagonal_start_y": 53},
            "motion_bridge": {"occupation_variable": "occupation",
                              "altitude_variable": "altitude",
                              "screen_aspect_ratio": 19/16,
                              "screen_horizon_height": 3500,
                              "native_height_units_per_tile": 107.95},
        }
        item = {"name": "capricornio", "ra_degrees": 315, "dec_degrees": -20,
                "intrinsic_altitude_offset": 0}
        ephemeris = FakeEphemeris()
        frame = sun.constellation_command_frame(ephemeris, config, item, 1, 123, 3)
        self.assertEqual(ephemeris.requested, (1, 315, -20, 123))
        self.assertEqual((frame["x"], frame["y"], frame["height"], frame["rotation"]),
                         (54, 0, 3500, 3))
        self.assertEqual(frame["occupation"], 54*65536 + 3)

    def test_fixed_sky_profiles_require_all_three_variables_and_preserve_rotation(self):
        class ProfileClient:
            def request(self, method, path, payload=None, costs=None, resource="variables"):
                self.assertion = (method, path, resource)
                return {"variables": {"occupation": {"value": "986371"},
                                      "altitude": {"value": "3500"},
                                      "state": {"value": "1"}}}

        config = {"constellations": [{"name": "leo", "target_kind": "furni",
                                      "furni_id": 123, "state_variable": "state"}]}
        profiles = sun.read_constellation_profiles(
            ProfileClient(), {"furni": ["occupation", "altitude", "state"]}, config,
            {"occupation_variable": "occupation", "altitude_variable": "altitude"})
        self.assertEqual(profiles[0]["target"], "furni/123")
        self.assertEqual(profiles[0]["rotation"], 3)
        self.assertEqual(profiles[0]["values"],
                         {"occupation": 986371, "altitude": 3500, "state": 1})

    def test_config_contains_all_requested_fixed_sky_targets_and_galaxy_offset(self):
        import json
        from pathlib import Path
        config = json.loads((Path(__file__).parent / "config.habbo-es.json").read_text())
        targets = sun.validate_constellations(config, live=True)
        self.assertEqual(len(targets), 13)
        self.assertEqual({item["furni_id"] for item in targets}, {
            1430121224, 1424879833, 1429360122, 1424371985, 1430121279,
            1424371933, 1424298371, 1289178599, 1430121204, 1289243336,
            1424599474, 1288578525, 1241761742,
        })
        galaxy = next(item for item in targets if item["furni_id"] == 1241761742)
        self.assertEqual(galaxy["intrinsic_altitude_offset"], 500)

    def test_update_request_refresh_builds_chunked_batches_for_every_sky_furni(self):
        import json
        from pathlib import Path

        class FakeEphemeris:
            def apparent_altaz(self, observer, body, timestamp):
                return 12, 90

            def moon_phase(self, observer, timestamp):
                return {"illumination": 0.6, "waxing": True,
                        "elongation_degrees": 120}

            def fixed_star_altaz(self, observer, ra, dec, timestamp):
                return dec, 90

        config = json.loads((Path(__file__).parent / "config.habbo-es.json").read_text())
        config = sun.validate_config(config, live=True)
        sun.validate_constellations(config, live=True)
        bridge = config["motion_bridge"]
        fixed_sky = [{"config": item, "target": f"furni/{item['furni_id']}",
                      "rotation": 0, "values": {}}
                     for item in config["constellations"]]
        planets = [{"config": item, "target": f"furni/{item['furni_id']}",
                    "rotation": 3, "values": {}}
                   for item in sun.validate_planets(config)]
        jobs, frames = sun.build_refresh_jobs(
            FakeEphemeris(), config, config["default_observer"], 1_800_000_000,
            bridge, "furni-bc/0", 0, "furni/523624626", fixed_sky, planets)

        self.assertEqual([job["variable"] for job in jobs],
                         ["state", "altitude", "altitude", "occupation", "occupation"])
        self.assertEqual([len(job["rows"]) for job in jobs], [1, 10, 6, 10, 6])
        self.assertEqual(len(frames), 16)
        self.assertEqual(sum(len(job["rows"]) for job in jobs), 33)
        self.assertEqual([row["target"] for row in jobs[0]["rows"]], ["furni/523624626"])
        saturn_rows = [(job["variable"], row) for job in jobs for row in job["rows"]
                       if row["target"] == "furni/165326448"]
        self.assertEqual([variable for variable, _ in saturn_rows], ["altitude", "occupation"])
        self.assertEqual(int(saturn_rows[1][1]["value"]) % 256, 3)
        self.assertTrue(all(len(job["rows"]) <= 10 for job in jobs))

    def test_saturn_uses_ephemeris_and_preserves_rotation_with_zero_offset(self):
        import json
        from pathlib import Path
        config = sun.validate_config(json.loads(
            Path(__file__).with_name("config.habbo-es.json").read_text()), live=True)
        saturn, = sun.validate_planets(config)
        self.assertEqual((saturn["name"], saturn["furni_id"]),
                         ("saturn barycenter", 165326448))

        class Ephemeris:
            def apparent_altaz(self, observer, body, timestamp):
                self.called = (observer, body, timestamp)
                return self.altitude, 90

        ephemeris = Ephemeris()
        for altitude, height in ((-90, 1000), (0, 3500), (90, 6000)):
            ephemeris.altitude = altitude
            frame = sun.body_command_frame(ephemeris, config, saturn, 1, 123, 3)
            self.assertEqual(ephemeris.called, (1, "saturn barycenter", 123))
            self.assertEqual((frame["height"], frame["visible"]), (height, 1))
            self.assertEqual(frame["occupation"] % 256, 3)
            self.assertEqual(frame["x"] + frame["y"], 54)

    def test_live_loop_updates_saturn_normally_and_on_refresh(self):
        import json
        from pathlib import Path
        for request in (0, 1):
            with self.subTest(update_request=request):
                config = sun.validate_config(json.loads(
                    Path(__file__).with_name("config.habbo-es.json").read_text()), live=True)
                config["constellations"] = []
                sun.validate_planets(config)
                now = [0.0]

                class Ephemeris:
                    def apparent_altaz(self, observer, body, timestamp):
                        return -20 + timestamp / 60, 90

                    def moon_phase(self, observer, timestamp):
                        return {"illumination": 0.6, "waxing": True, "elongation_degrees": 120}

                class Client:
                    def __init__(self):
                        self.flag, self.clears = request, 0
                        self.writes, self.batches = [], []
                        self.values = {target: {"occupation": 65536 + 256*53 + 3,
                                               "altitude": 3500, "state": 2}
                                       for target in ("furni-bc/0", "furni/523624626",
                                                      "furni/165326448")}

                    def controls_with_update_request(self):
                        return True, 1, self.flag

                    def clear_update_request(self):
                        self.flag = 0
                        self.clears += 1

                    def request(self, method, path, payload=None, **kwargs):
                        if path == "":
                            return {"furni": ["occupation", "altitude", "state"]}
                        if method == "GET":
                            return {"variables": {key: {"value": str(value)} for key, value
                                                  in self.values[path.removeprefix("/furni/")].items()}}
                        _, _, variable, kind, furni_id = path.split("/")
                        target = f"{kind}/{furni_id}"
                        self.writes.append((target, variable, int(payload["value"])))
                        self.values[target][variable] = int(payload["value"])
                        return payload

                    def publish_variable(self, variable, rows):
                        self.batches.append((variable, rows))
                        for row in rows:
                            self.values[row["target"]][variable] = int(row["value"])
                        return len(rows)

                client = Client()
                with patch.object(sun.time, "monotonic", lambda: now[0]), \
                     patch.object(sun.time, "time", lambda: now[0]), \
                     patch.object(sun.time, "sleep", lambda delay: now.__setitem__(0, now[0]+delay)), \
                     patch.object(sun, "log") as logger:
                    sun.run(config, client, Ephemeris(), 31)
                self.assertTrue(any(target == "furni/165326448" for target, _, _ in client.writes))
                self.assertTrue(any(call.args[0] == "planet_frame" for call in logger.call_args_list))
                self.assertEqual(client.values["furni/165326448"]["state"], 2)
                self.assertEqual(client.values["furni/165326448"]["occupation"] % 256, 3)
                self.assertEqual(client.clears, request)
                saturn_batches = [variable for variable, rows in client.batches
                                  if any(row["target"] == "furni/165326448" for row in rows)]
                self.assertEqual(saturn_batches, ["altitude", "occupation"] if request else [])


if __name__ == "__main__":
    unittest.main()
