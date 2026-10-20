"""Scheduled Sun controller using permanent WIRED furniture variables."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote

from realtime import (ApiError, ConfigurationError, Ephemeris, HabboClient,
                      RetryLater, finite, integer, log, lunar_phase, project_screen_ellipse,
                      validate_config)


BRIDGE = {
    "occupation_variable": "occupation",
    "altitude_variable": "altitude",
    "screen_aspect_ratio": 19 / 16,
    "screen_horizon_height": 3500,
    "native_height_units_per_tile": 107.95,
}


def command_frame(ephemeris, config, observer, timestamp, rotation):
    altitude, azimuth = ephemeris.apparent_altaz(observer, "sun", timestamp)
    bridge = config.get("motion_bridge", BRIDGE)
    projection = {**config["projection"],
                  "horizon_height": bridge["screen_horizon_height"]}
    position = project_screen_ellipse(
        altitude, azimuth, projection,
        bridge["screen_aspect_ratio"], bridge["native_height_units_per_tile"])
    height = position["height"] - config["bodies"][0].get("intrinsic_altitude_offset", 0)
    integer(height, 0, 10000, "Sun command altitude")
    occupation = 65536 * position["x"] + 256 * position["y"] + rotation
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": height,
            "visible": position["visible"], "x": position["x"], "y": position["y"]}


def moon_display_state(illumination, waxing, latitude, full_threshold=0.5):
    """Map unavailable lunar artwork to full/gibbous or crescent states.

    State 0 represents illumination at or above one half; state 1 represents
    illumination below one half. Rotation mirrors crescent direction between
    the northern and southern hemispheres.
    """
    finite(illumination, 0, 1, "lunar illumination")
    finite(latitude, -90, 90, "observer latitude")
    finite(full_threshold, 0, 1, "full_moon_illumination_threshold")
    if not isinstance(waxing, bool):
        raise ConfigurationError("waxing must be a boolean")
    is_southern = latitude < 0
    rotation = 0 if bool(waxing) == is_southern else 1
    return {"state": 0 if illumination >= full_threshold else 1, "rotation": rotation}


def moon_command_frame(ephemeris, config, observer, timestamp):
    moon_config = config["moon"]
    latitude = config["observers"][str(observer)]["latitude"]
    altitude, azimuth = ephemeris.apparent_altaz(observer, "moon", timestamp)
    phase = ephemeris.moon_phase(observer, timestamp)
    display = moon_display_state(
        phase["illumination"], phase["waxing"], latitude,
        moon_config.get("full_moon_illumination_threshold", 0.5))
    bridge = config.get("motion_bridge", BRIDGE)
    projection = {**config["projection"],
                  "horizon_height": bridge["screen_horizon_height"]}
    position = project_screen_ellipse(
        altitude, azimuth, projection,
        bridge["screen_aspect_ratio"], bridge["native_height_units_per_tile"])
    height = position["height"] - moon_config.get("intrinsic_altitude_offset", 0)
    integer(height, 0, 10000, "Moon command altitude")
    occupation = 65536 * position["x"] + 256 * position["y"] + display["rotation"]
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": height,
            "visible": position["visible"], "x": position["x"], "y": position["y"],
            "state": display["state"], "rotation": display["rotation"],
            "illumination": phase["illumination"], "waxing": phase["waxing"],
            "elongation_degrees": phase["elongation_degrees"]}


def definitions_and_profile(client, config):
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed variable definitions")
    bridge = config.get("motion_bridge", BRIDGE)
    body = config["bodies"][0]
    target_kind, entity_id = body["target_kind"], body["furni_id"]
    required = (bridge["occupation_variable"], bridge["altitude_variable"])
    target, variables = read_furni_profile(client, definitions, target_kind, entity_id, required)
    occupation = integer(variables[required[0]], 0, 2**63-1, "occupation")
    altitude = integer(variables[required[1]], 0, 10000, "altitude")
    x, y, rotation = occupation // 65536, (occupation // 256) % 256, occupation % 256
    if x > 55 or y > 55:
        raise ConfigurationError("Sun's current occupation is outside the 56-cell room")
    return bridge, target, rotation, occupation, altitude, definitions


def read_furni_profile(client, definitions, target_kind, entity_id, required):
    for name in required:
        names = definitions.get("furni")
        if not isinstance(names, list) or name not in names:
            raise ConfigurationError("Missing WIRED variable definition: furni/" + name)
    target = f"{target_kind}/{entity_id}"
    profile = client.request("GET", "/furni/" + target, resource="variables_profile",
                             costs={"profile_read": 1})
    variables = profile.get("variables") if isinstance(profile, dict) else None
    if not isinstance(variables, dict) or not set(required).issubset(variables):
        missing = sorted(set(required) - set(variables or {}))
        raise ConfigurationError(f"Assign permanent furni variables {', '.join(missing)} to {target}")
    return target, {name: integer(variables[name].get("value"), -(2**63),
                                  2**63-1, name) for name in required}


def write_command(client, target, bridge, frame, current_values):
    occ_name, alt_name = bridge["occupation_variable"], bridge["altitude_variable"]
    written = []
    # Update altitude first; the final occupation change then exposes the complete frame.
    for name, value in ((alt_name, frame["height"]), (occ_name, frame["occupation"])):
        if current_values[name] == value:
            continue
        response = client.request("PATCH", f"/furni/{quote(name, safe='')}/{target}",
                                  {"value": str(value)})
        if not isinstance(response, dict) or integer(response.get("value"), -(2**63),
                                                       2**63-1, name) != value:
            raise ApiError(name + " command did not read back as requested")
        current_values[name] = value
        written.append(name)
    return written


def write_moon_command(client, target, bridge, state_variable, frame, current_values):
    written = []
    # Set artwork and altitude first; occupation is the final movement command.
    for name, value in ((state_variable, frame["state"]),
                        (bridge["altitude_variable"], frame["height"]),
                        (bridge["occupation_variable"], frame["occupation"])):
        if current_values[name] == value:
            continue
        response = client.request("PATCH", f"/furni/{quote(name, safe='')}/{target}",
                                  {"value": str(value)})
        if not isinstance(response, dict) or integer(response.get("value"), -(2**63),
                                                       2**63-1, name) != value:
            raise ApiError(name + " command did not read back as requested")
        current_values[name] = value
        written.append(name)
    return written


def run(config, client, ephemeris, duration):
    enabled, observer = client.controls()
    if not enabled:
        log("room_disabled", observer=observer, writes_performed=False)
        return
    bridge, target, rotation, current_occupation, current_altitude, definitions = definitions_and_profile(client, config)
    moon_config = config["moon"]
    moon_target, moon_values = read_furni_profile(
        client, definitions, moon_config["target_kind"], moon_config["furni_id"],
        (bridge["occupation_variable"], bridge["altitude_variable"],
         moon_config["state_variable"]))
    moon_occupation = integer(moon_values[bridge["occupation_variable"]], 0,
                              2**63-1, "moon occupation")
    if moon_occupation // 65536 > 55 or (moon_occupation // 256) % 256 > 55:
        raise ConfigurationError("Moon's current occupation is outside the 56-cell room")
    moon_state = integer(moon_values[moon_config["state_variable"]], 0, 3, "moon state")
    for key, low, high in (("screen_aspect_ratio", 0.5, 4),
                           ("screen_horizon_height", 0, 10000),
                           ("native_height_units_per_tile", 0.1, 200)):
        finite(bridge[key], low, high, key)
    vertical_radius = ((config["projection"]["room_size"] - 1) /
                       (2 * bridge["screen_aspect_ratio"]) *
                       bridge["native_height_units_per_tile"])
    offsets = (config["bodies"][0].get("intrinsic_altitude_offset", 0),
               moon_config.get("intrinsic_altitude_offset", 0))
    for offset in offsets:
        finite(offset, -10000, 10000, "intrinsic_altitude_offset")
        if not (0 <= bridge["screen_horizon_height"] - vertical_radius - offset and
                bridge["screen_horizon_height"] + vertical_radius - offset <= 10000):
            raise ConfigurationError("Projected command altitudes must fit the supported 0…10000 range")
    deadline = time.monotonic() + duration
    next_poll = next_update = 0
    current_values = {bridge["occupation_variable"]: current_occupation,
                      bridge["altitude_variable"]: current_altitude}
    last_frame = (current_occupation, current_altitude)
    moon_current = {**moon_values, moon_config["state_variable"]: moon_state}
    failures = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        try:
            if now >= next_poll:
                controls = client.controls()
                if controls != (enabled, observer):
                    enabled, observer = controls
                    log("room_controls", enabled=enabled, observer=observer)
                    next_update = 0
                next_poll = time.monotonic() + config["poll_seconds"]
            if enabled and time.monotonic() >= next_update:
                sampled_at = time.time()
                frame = command_frame(ephemeris, config, observer, sampled_at, rotation)
                if last_frame != (frame["occupation"], frame["height"]):
                    written = write_command(client, target, bridge, frame, current_values)
                    log("sun_frame", utc=datetime.fromtimestamp(
                        sampled_at, timezone.utc).isoformat(), observer=observer,
                        altitude_degrees=round(frame["altitude_degrees"], 4),
                        azimuth_degrees=round(frame["azimuth_degrees"], 4),
                        x=frame["x"], y=frame["y"], occupation=frame["occupation"],
                        height=frame["height"], screen_aspect_ratio=bridge["screen_aspect_ratio"],
                        variables_written=written)
                    last_frame = (frame["occupation"], frame["height"])
                    failures = 0
                moon_frame = moon_command_frame(ephemeris, config, observer, sampled_at)
                expected_moon = (moon_frame["occupation"], moon_frame["height"], moon_frame["state"])
                current_moon = (moon_current[bridge["occupation_variable"]],
                                moon_current[bridge["altitude_variable"]],
                                moon_current[moon_config["state_variable"]])
                if expected_moon != current_moon:
                    written = write_moon_command(client, moon_target, bridge,
                                                  moon_config["state_variable"], moon_frame,
                                                  moon_current)
                    log("moon_frame", utc=datetime.fromtimestamp(
                        sampled_at, timezone.utc).isoformat(), observer=observer,
                        altitude_degrees=round(moon_frame["altitude_degrees"], 4),
                        azimuth_degrees=round(moon_frame["azimuth_degrees"], 4),
                        x=moon_frame["x"], y=moon_frame["y"],
                        occupation=moon_frame["occupation"], height=moon_frame["height"],
                        illumination=round(moon_frame["illumination"], 4),
                        waxing=moon_frame["waxing"], state=moon_frame["state"],
                        rotation=moon_frame["rotation"], variables_written=written)
                    failures = 0
                next_update = time.monotonic() + config["update_seconds"]
            wake = min(next_poll, next_update) if enabled else next_poll
            time.sleep(max(0, min(wake-time.monotonic(), deadline-time.monotonic())))
        except RetryLater as exc:
            failures += 1
            delay = max(exc.delay, min(60, 2**min(failures-1, 6)))
            log("cooldown", reason=str(exc), seconds=delay)
            time.sleep(max(0, min(delay, deadline-time.monotonic())))
            next_poll = next_update = 0
        except ApiError as exc:
            failures += 1
            delay = min(60, 2**min(failures-1, 6))
            log("api_error", message=str(exc), retry_seconds=delay)
            time.sleep(max(0, min(delay, deadline-time.monotonic())))
            next_poll = next_update = 0
    log("scheduler_stopped", reason="duration_reached")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.habbo-es.json"))
    parser.add_argument("--ephemeris", type=Path, required=True)
    parser.add_argument("--preview", action="store_true", help="calculate one position without API access")
    parser.add_argument("--duration", type=float, default=600)
    args = parser.parse_args()
    try:
        config = validate_config(json.loads(args.config.read_text()), live=not args.preview)
        moon_config = config["moon"]
        if moon_config.get("target_kind") not in ("furni", "furni-bc"):
            raise ConfigurationError("Unsupported Moon target kind")
        moon_config["furni_id"] = integer(
            moon_config["furni_id"], 1 if moon_config["target_kind"] == "furni" else 0,
            2**63-1, "Moon furni_id")
        if not isinstance(moon_config.get("state_variable"), str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,39}", moon_config["state_variable"]):
            raise ConfigurationError("Invalid Moon state_variable")
        finite(moon_config.get("full_moon_illumination_threshold", 0.5), 0, 1,
               "full_moon_illumination_threshold")
        bridge = config.get("motion_bridge", BRIDGE)
        if moon_config["state_variable"] in (bridge["occupation_variable"], bridge["altitude_variable"]):
            raise ConfigurationError("Moon state variable must differ from occupation and altitude")
        finite(args.duration, 1, 5*60*60+55*60, "duration")
        ephemeris = Ephemeris(args.ephemeris, config)
        if args.preview:
            bridge = config.get("motion_bridge", BRIDGE)
            observer = config["default_observer"]
            if os.environ.get("HABBO_READ_KEY"):
                reader = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
                _, observer = reader.controls()
                definitions = reader.request("GET", "")
                sun = config["bodies"][0]
                _, sun_values = read_furni_profile(
                    reader, definitions, sun["target_kind"], sun["furni_id"],
                    (bridge["occupation_variable"], bridge["altitude_variable"]))
                _, moon_values = read_furni_profile(
                    reader, definitions, moon_config["target_kind"], moon_config["furni_id"],
                    (bridge["occupation_variable"], bridge["altitude_variable"],
                     moon_config["state_variable"]))
                log("furni_preflight", sun_variables=sorted(sun_values),
                    moon_variables=sorted(moon_values), moon_state=moon_values[moon_config["state_variable"]])
            frame = command_frame(ephemeris, config, observer, time.time(), 0)
            log("scheduled_sun_preview", observer=observer,
                screen_aspect_ratio=bridge["screen_aspect_ratio"],
                native_height_units_per_tile=bridge["native_height_units_per_tile"], **frame)
            moon_frame = moon_command_frame(ephemeris, config, observer, time.time())
            log("scheduled_moon_preview", observer=observer, **moon_frame)
            return 0
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), os.environ.get("HABBO_WRITE_KEY"))
        if not client.write_key:
            raise ConfigurationError("Set HABBO_WRITE_KEY in GitHub Actions secrets")
        log("scheduler_started", room=config["room_id"], duration_seconds=args.duration,
            schedule="current UTC; observer latitude/longitude from room selection")
        run(config, client, ephemeris, args.duration)
        return 0
    except (ConfigurationError, ApiError, KeyError, TypeError, ValueError, OSError, ImportError) as exc:
        log("scheduler_error", message=str(exc) if isinstance(exc, (ConfigurationError, ApiError))
            else "Invalid configuration, missing dependency/file, or ephemeris error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
