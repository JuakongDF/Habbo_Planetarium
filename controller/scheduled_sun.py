"""Scheduled Sun controller using permanent WIRED commands and a selector pulse."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

from realtime import (ApiError, ConfigurationError, Ephemeris, HabboClient,
                      RetryLater, finite, integer, log, project_screen_ellipse,
                      validate_config)


BRIDGE = {
    "occupation_variable": "occupation",
    "altitude_variable": "altitude",
    "selector_variable": "update_furni_id",
    "furni_in_game_id": 2147418112,
    "screen_aspect_ratio": 19 / 16,
    "native_height_units_per_tile": 32,
    "selector_settle_seconds": 0.2,
}


def read_int(client, path, label, minimum=-(2**63), maximum=2**63-1):
    response = client.request("GET", path)
    if not isinstance(response, dict) or "value" not in response:
        raise ApiError("Malformed " + label + " response")
    return integer(response["value"], minimum, maximum, label)


def command_frame(ephemeris, config, observer, timestamp, rotation):
    altitude, azimuth = ephemeris.apparent_altaz(observer, "sun", timestamp)
    position = project_screen_ellipse(
        altitude, azimuth, config["projection"],
        BRIDGE["screen_aspect_ratio"], BRIDGE["native_height_units_per_tile"])
    occupation = 65536 * position["x"] + 256 * position["y"] + rotation
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": position["height"],
            "visible": position["visible"], "x": position["x"], "y": position["y"]}


def definitions_and_profile(client, config):
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed variable definitions")
    bridge = config.get("motion_bridge", BRIDGE)
    for scope, name in (("furni", bridge["occupation_variable"]),
                        ("furni", bridge["altitude_variable"]),
                        ("global", bridge["selector_variable"])):
        names = definitions.get(scope)
        if not isinstance(names, list) or name not in names:
            raise ConfigurationError("Missing WIRED variable definition: " + scope + "/" + name)
    body = config["bodies"][0]
    target_kind, entity_id = body["target_kind"], body["furni_id"]
    target = f"{target_kind}/{entity_id}"
    profile = client.request("GET", "/furni/" + target, resource="variables_profile",
                             costs={"profile_read": 1})
    variables = profile.get("variables") if isinstance(profile, dict) else None
    required = (bridge["occupation_variable"], bridge["altitude_variable"])
    if not isinstance(variables, dict) or not set(required).issubset(variables):
        raise ConfigurationError("Assign both permanent motion variables to the Sun")
    occupation = integer(variables[required[0]].get("value"), 0, 2**63-1, "occupation")
    altitude = integer(variables[required[1]].get("value"), 0, 2047, "altitude")
    x, y, rotation = occupation // 65536, (occupation // 256) % 256, occupation % 256
    if x > 55 or y > 55:
        raise ConfigurationError("Sun's current occupation is outside the 56-cell room")
    return bridge, target, rotation


def write_command(client, target, bridge, frame, current_selector):
    selector_path = "/global/" + quote(bridge["selector_variable"], safe="")
    if current_selector != 0:
        raise ConfigurationError("Selector is not zero; refusing to overlap motion commands")
    occ_name, alt_name = bridge["occupation_variable"], bridge["altitude_variable"]
    for name, value in ((occ_name, frame["occupation"]), (alt_name, frame["height"])):
        response = client.request("PATCH", f"/furni/{quote(name, safe='')}/{target}",
                                  {"value": str(value)})
        if not isinstance(response, dict) or integer(response.get("value"), -(2**63),
                                                       2**63-1, name) != value:
            raise ApiError(name + " command did not read back as requested")

    target_id = bridge["furni_in_game_id"]
    response = client.request("PATCH", selector_path, {"value": str(target_id)})
    if not isinstance(response, dict) or integer(response.get("value"), 0, 2**63-1,
                                                  "selector") != target_id:
        raise ApiError("Selector trigger did not read back as requested")

    time.sleep(bridge["selector_settle_seconds"])
    selector = read_int(client, selector_path, "selector", 0, 2**63-1)
    if selector == target_id:
        reset = client.request("PATCH", selector_path, {"value": "0"})
        if not isinstance(reset, dict) or integer(reset.get("value"), 0, 2**63-1,
                                                   "selector reset") != 0:
            raise ApiError("Could not clear selector after motion command")
        reset_mode = "controller_reset"
    elif selector == 0:
        reset_mode = "wired_reset"
    else:
        raise ConfigurationError("Selector changed unexpectedly during motion command")
    return reset_mode


def run(config, client, ephemeris, duration):
    enabled, observer = client.controls()
    if not enabled:
        log("room_disabled", observer=observer, writes_performed=False)
        return
    bridge, target, rotation = definitions_and_profile(client, config)
    for key, low, high in (("screen_aspect_ratio", 0.5, 4),
                           ("native_height_units_per_tile", 0.1, 100),
                           ("selector_settle_seconds", 0.05, 2)):
        finite(bridge[key], low, high, key)
    deadline = time.monotonic() + duration
    next_poll = next_update = 0
    last_frame = None
    failures = 0
    last_night_log = None
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
                if frame["visible"] == 0:
                    night_key = datetime.fromtimestamp(sampled_at, timezone.utc).date().isoformat()
                    if last_night_log != night_key:
                        log("sun_below_horizon", utc=datetime.fromtimestamp(
                            sampled_at, timezone.utc).isoformat(), observer=observer,
                            action="holding_last_position")
                        last_night_log = night_key
                elif last_frame != (frame["occupation"], frame["height"]):
                    selector_path = "/global/" + quote(bridge["selector_variable"], safe="")
                    selector = read_int(client, selector_path, "selector", 0, 2**63-1)
                    reset_mode = write_command(client, target, bridge, frame, selector)
                    log("sun_command", utc=datetime.fromtimestamp(
                        sampled_at, timezone.utc).isoformat(), observer=observer,
                        altitude_degrees=round(frame["altitude_degrees"], 4),
                        azimuth_degrees=round(frame["azimuth_degrees"], 4),
                        x=frame["x"], y=frame["y"], occupation=frame["occupation"],
                        height=frame["height"], screen_aspect_ratio=bridge["screen_aspect_ratio"],
                        selector_reset=reset_mode)
                    last_frame = (frame["occupation"], frame["height"])
                    last_night_log = None
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
        finite(args.duration, 1, 5*60*60+45*60, "duration")
        ephemeris = Ephemeris(args.ephemeris, config)
        if args.preview:
            bridge = config.get("motion_bridge", BRIDGE)
            observer = config["default_observer"]
            if os.environ.get("HABBO_READ_KEY"):
                reader = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
                _, observer = reader.controls()
            frame = command_frame(ephemeris, config, observer, time.time(), 0)
            log("scheduled_sun_preview", observer=observer,
                screen_aspect_ratio=bridge["screen_aspect_ratio"],
                native_height_units_per_tile=bridge["native_height_units_per_tile"], **frame)
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
