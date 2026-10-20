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
            "bodies": [{"intrinsic_altitude_offset": 700}],
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


if __name__ == "__main__":
    unittest.main()
