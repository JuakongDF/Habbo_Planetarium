"""Scheduled celestial controller using permanent WIRED furniture variables."""
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


def command_frame(ephemeris, config, observer, timestamp, rotation, view=-1):
    return body_command_frame(ephemeris, config, config["bodies"][0], observer,
                              timestamp, rotation, view=view)


def body_command_frame(ephemeris, config, body, observer, timestamp, rotation, view=-1):
    """Use the body's changing ephemeris position, including below the horizon."""
    altitude, azimuth = ephemeris.apparent_altaz(observer, body["name"], timestamp)
    bridge = config.get("motion_bridge", BRIDGE)
    projection = {**config["projection"],
                  "horizon_height": bridge["screen_horizon_height"]}
    position = project_screen_ellipse(
        altitude, azimuth, projection,
        bridge["screen_aspect_ratio"], bridge["native_height_units_per_tile"], view=view)
    height = position["height"] - body.get("intrinsic_altitude_offset", 0)
    integer(height, 0, 10000, body["name"] + " command altitude")
    occupation = 65536 * position["x"] + 256 * position["y"] + rotation
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": height,
            "visible": position["visible"], "x": position["x"], "y": position["y"],
            "rotation": rotation}


def validate_planets(config):
    if config["bodies"][0]["name"] != "sun":
        raise ConfigurationError("The scheduled controller's first body must be the Sun")
    planets = config["bodies"][1:]
    for item in planets:
        if item["name"] not in ("mercury", "venus", "mars barycenter",
                                 "jupiter barycenter", "saturn barycenter"):
            raise ConfigurationError("Additional scheduled bodies must be planets")
        if (item["target_kind"], item["furni_id"]) == (
                config["moon"]["target_kind"], config["moon"]["furni_id"]):
            raise ConfigurationError("A planet cannot use the Moon's furniture")
        state_variable = item.setdefault("state_variable", "state")
        if not isinstance(state_variable, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,39}", state_variable):
            raise ConfigurationError("Invalid planet state_variable")
        bridge = config.get("motion_bridge", BRIDGE)
        if state_variable in (bridge["occupation_variable"], bridge["altitude_variable"]):
            raise ConfigurationError("Planet state variable must differ from occupation and altitude")
        finite(item.get("intrinsic_altitude_offset", 0), -10000, 10000,
               item["name"] + " intrinsic altitude offset")
    return planets


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


def moon_command_frame(ephemeris, config, observer, timestamp, view=-1):
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
        bridge["screen_aspect_ratio"], bridge["native_height_units_per_tile"], view=view)
    height = position["height"] - moon_config.get("intrinsic_altitude_offset", 0)
    integer(height, 0, 10000, "Moon command altitude")
    occupation = 65536 * position["x"] + 256 * position["y"] + display["rotation"]
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": height,
            "visible": position["visible"], "x": position["x"], "y": position["y"],
            "state": display["state"], "rotation": display["rotation"],
            "illumination": phase["illumination"], "waxing": phase["waxing"],
            "elongation_degrees": phase["elongation_degrees"]}


def fixed_sky_anchor(config, item):
    """Resolve a linked furni to its celestial anchor and effective sprite offset."""
    if "follow" in item:
        source = next(source for source in config["constellations"]
                      if source["name"] == item["follow"])
        return source, (source.get("intrinsic_altitude_offset", 0)
                        - item["relative_altitude"])
    return item, item.get("intrinsic_altitude_offset", 0)


def constellation_command_frame(ephemeris, config, constellation, observer, timestamp,
                                rotation, view=-1):
    """Project a fixed celestial anchor; only its local horizon position changes."""
    anchor, offset = fixed_sky_anchor(config, constellation)
    altitude, azimuth = ephemeris.fixed_star_altaz(
        observer, anchor["ra_degrees"], anchor["dec_degrees"], timestamp)
    bridge = config.get("motion_bridge", BRIDGE)
    projection = {**config["projection"],
                  "horizon_height": bridge["screen_horizon_height"]}
    position = project_screen_ellipse(
        altitude, azimuth, projection,
        bridge["screen_aspect_ratio"], bridge["native_height_units_per_tile"], view=view)
    height = position["height"] - offset
    integer(height, 0, 10000, constellation["name"] + " command altitude")
    occupation = 65536 * position["x"] + 256 * position["y"] + rotation
    return {"altitude_degrees": altitude, "azimuth_degrees": azimuth,
            "occupation": occupation, "height": height,
            "visible": position["visible"], "x": position["x"], "y": position["y"],
            "rotation": rotation, "state": 0}


def validate_constellations(config, live=False):
    constellations = config.get("constellations", [])
    if not isinstance(constellations, list) or len(constellations) > 20:
        raise ConfigurationError("Configure a list of at most 20 fixed-sky furniture targets")
    targets = {(body.get("target_kind", "furni"), body["furni_id"])
               for body in config["bodies"]}
    targets.add((config["moon"]["target_kind"], config["moon"]["furni_id"]))
    names = set()
    for item in constellations:
        if not isinstance(item, dict):
            raise ConfigurationError("Each fixed-sky target must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ConfigurationError("Fixed-sky targets need unique non-empty names")
        names.add(name)
        if "follow" not in item:
            finite(item.get("ra_degrees"), 0, 360, name + " right ascension")
            finite(item.get("dec_degrees"), -90, 90, name + " declination")
        kind = item.get("target_kind", "furni")
        if kind not in ("furni", "furni-bc"):
            raise ConfigurationError("Unsupported fixed-sky target kind")
        minimum = 1 if live and kind == "furni" else 0
        item["furni_id"] = integer(item.get("furni_id"), minimum, 2**63-1,
                                   name + " furni_id")
        target = (kind, item["furni_id"])
        if target in targets:
            raise ConfigurationError("Each celestial body must target different furniture")
        targets.add(target)
        state_variable = item.setdefault("state_variable", "state")
        if not isinstance(state_variable, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,39}", state_variable):
            raise ConfigurationError("Invalid state_variable for " + name)
        bridge = config.get("motion_bridge", BRIDGE)
        if state_variable in (bridge["occupation_variable"], bridge["altitude_variable"]):
            raise ConfigurationError("Fixed-sky state variable must differ from position variables")
        finite(item.get("intrinsic_altitude_offset", 0), -10000, 10000,
               name + " intrinsic altitude offset")
    by_name = {item["name"]: item for item in constellations}
    for item in constellations:
        if "follow" not in item:
            continue
        source = item["follow"]
        if (not isinstance(source, str) or source not in by_name
                or "follow" in by_name[source]):
            raise ConfigurationError("Fixed-sky follow must name an independent celestial anchor")
        if any(key in item for key in ("ra_degrees", "dec_degrees", "intrinsic_altitude_offset")):
            raise ConfigurationError("Linked furniture uses its source anchor and relative_altitude only")
        integer(item.get("relative_altitude"), -10000, 10000, "relative_altitude")
    return constellations


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


def read_constellation_profiles(client, definitions, config, bridge):
    return read_motion_profiles(client, definitions, config.get("constellations", []), bridge)


def read_planet_profiles(client, definitions, config, bridge):
    return read_motion_profiles(client, definitions, config["bodies"][1:], bridge)


def read_motion_profiles(client, definitions, items, bridge):
    profiles = []
    for item in items:
        state_variable = item.get("state_variable", "state")
        required = (bridge["occupation_variable"], bridge["altitude_variable"], state_variable)
        target, values = read_furni_profile(
            client, definitions, item.get("target_kind", "furni"),
            item["furni_id"], required)
        occupation = integer(values[bridge["occupation_variable"]], 0,
                             2**63-1, item["name"] + " occupation")
        x, y, rotation = occupation // 65536, (occupation // 256) % 256, occupation % 256
        if x > 55 or y > 55 or rotation > 7:
            raise ConfigurationError(item["name"] + " has an invalid position or rotation")
        integer(values[bridge["altitude_variable"]], 0, 10000,
                item["name"] + " altitude")
        integer(values[state_variable], -(2**63), 2**63-1, item["name"] + " state")
        profiles.append({"config": item, "target": target, "rotation": rotation,
                         "values": values})
    return profiles


def write_command(client, target, bridge, frame, current_values):
    occ_name, alt_name = bridge["occupation_variable"], bridge["altitude_variable"]
    written = []
    # Update altitude first; the final occupation change then exposes the complete frame.
    for name, value in ((alt_name, frame["height"]), (occ_name, frame["occupation"])):
        if current_values[name] == value:
            continue
        response = client.request("PATCH", f"/furni/{quote(name, safe='')}/{target}",
                                  {"value": str(value)}, costs={"write": 1})
        if not isinstance(response, dict) or integer(response.get("value"), -(2**63),
                                                       2**63-1, name) != value:
            raise ApiError(name + " command did not read back as requested")
        current_values[name] = value
        written.append(name)
    return written


def write_display_command(client, target, bridge, state_variable, frame, current_values):
    written = []
    # Set artwork and altitude first; occupation is the final movement command.
    for name, value in ((state_variable, frame["state"]),
                        (bridge["altitude_variable"], frame["height"]),
                        (bridge["occupation_variable"], frame["occupation"])):
        if current_values[name] == value:
            continue
        response = client.request("PATCH", f"/furni/{quote(name, safe='')}/{target}",
                                  {"value": str(value)}, costs={"write": 1})
        if not isinstance(response, dict) or integer(response.get("value"), -(2**63),
                                                       2**63-1, name) != value:
            raise ApiError(name + " command did not read back as requested")
        current_values[name] = value
        written.append(name)
    return written


def build_refresh_jobs(ephemeris, config, observer, timestamp, bridge, sun_target,
                       sun_rotation, moon_target, fixed_sky, planets=()):
    """Build API batches grouped by variable; each batch is limited to 10 furni."""
    state_variable = config["moon"]["state_variable"]
    rows_by_variable = {state_variable: [], bridge["altitude_variable"]: [],
                        bridge["occupation_variable"]: []}
    frames = []

    def add_frame(target_kind, furni_id, frame, state=None, display_variable=state_variable):
        target = f"{target_kind}/{furni_id}"
        if state is not None:
            rows_by_variable.setdefault(display_variable, []).append(
                {"target_kind": target_kind, "furni_id": furni_id, "value": str(state),
                 "target": target})
        rows_by_variable[bridge["altitude_variable"]].append(
            {"target_kind": target_kind, "furni_id": furni_id,
             "value": str(frame["height"]), "target": target})
        rows_by_variable[bridge["occupation_variable"]].append(
            {"target_kind": target_kind, "furni_id": furni_id,
             "value": str(frame["occupation"]), "target": target})
        frames.append((target, frame, state))

    sun_body = config["bodies"][0]
    sun_frame = command_frame(ephemeris, config, observer, timestamp, sun_rotation)
    add_frame(sun_body.get("target_kind", "furni"), sun_body["furni_id"], sun_frame)

    moon_config = config["moon"]
    moon_frame = moon_command_frame(ephemeris, config, observer, timestamp)
    add_frame(moon_config["target_kind"], moon_config["furni_id"], moon_frame,
              state=moon_frame["state"])

    for profile in planets:
        item = profile["config"]
        frame = body_command_frame(
            ephemeris, config, item, observer, timestamp, profile["rotation"])
        add_frame(item.get("target_kind", "furni"), item["furni_id"], frame)

    for profile in fixed_sky:
        item = profile["config"]
        frame = constellation_command_frame(
            ephemeris, config, item, observer, timestamp, profile["rotation"])
        add_frame(item.get("target_kind", "furni"), item["furni_id"], frame,
                  state=0, display_variable=item.get("state_variable", "state"))

    jobs = []
    position_variables = (bridge["altitude_variable"], bridge["occupation_variable"])
    display_variables = [name for name in rows_by_variable if name not in position_variables]
    for variable in (*display_variables, *position_variables):
        rows = rows_by_variable[variable]
        for start in range(0, len(rows), 10):
            jobs.append({"variable": variable, "rows": rows[start:start+10]})
    return jobs, frames


def run(config, client, ephemeris, duration):
    enabled, observer, update_request = client.controls_with_update_request()
    if not enabled:
        log("room_disabled", observer=observer, writes_performed=False,
            update_request=update_request)
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
    fixed_sky = read_constellation_profiles(client, definitions, config, bridge)
    planets = read_planet_profiles(client, definitions, config, bridge)
    for key, low, high in (("screen_aspect_ratio", 0.5, 4),
                           ("screen_horizon_height", 0, 10000),
                           ("native_height_units_per_tile", 0.1, 200)):
        finite(bridge[key], low, high, key)
    vertical_radius = ((config["projection"]["room_size"] - 1) /
                       (2 * bridge["screen_aspect_ratio"]) *
                       bridge["native_height_units_per_tile"])
    offsets = (config["bodies"][0].get("intrinsic_altitude_offset", 0),
               moon_config.get("intrinsic_altitude_offset", 0),
               *(fixed_sky_anchor(config, item["config"])[1] for item in fixed_sky),
               *(item["config"].get("intrinsic_altitude_offset", 0) for item in planets))
    for offset in offsets:
        finite(offset, -10000, 10000, "intrinsic_altitude_offset")
        if not (0 <= bridge["screen_horizon_height"] - vertical_radius - offset and
                bridge["screen_horizon_height"] + vertical_radius - offset <= 10000):
            raise ConfigurationError("Projected command altitudes must fit the supported 0…10000 range")
    deadline = time.monotonic() + duration
    next_poll = next_update = 0
    pending_batch_jobs = None
    pending_batch_index = 0
    pending_request_value = None
    refresh_again = False
    refresh_after_enable = False
    last_seen_update_request = 0
    update_request_ack_pending = False
    current_values = {bridge["occupation_variable"]: current_occupation,
                      bridge["altitude_variable"]: current_altitude}
    last_frame = (current_occupation, current_altitude)
    moon_current = {**moon_values, moon_config["state_variable"]: moon_state}
    failures = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        try:
            if now >= next_poll:
                controls = client.controls_with_update_request()
                new_enabled, new_observer, update_request = controls
                if (new_enabled, new_observer) != (enabled, observer):
                    enabled, observer = new_enabled, new_observer
                    log("room_controls", enabled=enabled, observer=observer)
                    next_update = 0
                    if pending_batch_jobs is not None:
                        refresh_again = True
                if update_request == 0:
                    last_seen_update_request = 0
                elif enabled and update_request != last_seen_update_request:
                    last_seen_update_request = update_request
                    update_request_ack_pending = True
                    if pending_batch_jobs is None:
                        pending_batch_jobs, _ = build_refresh_jobs(
                            ephemeris, config, observer, time.time(), bridge, target,
                            rotation, moon_target, fixed_sky, planets)
                        pending_batch_index = 0
                        pending_request_value = update_request
                        log("update_request_received", value=update_request,
                            batches=len(pending_batch_jobs), observer=observer)
                    else:
                        refresh_again = True
                        log("update_request_coalesced", value=update_request)
                if update_request_ack_pending and enabled:
                    client.clear_update_request()
                    update_request_ack_pending = False
                next_poll = time.monotonic() + config["poll_seconds"]
            if not enabled and pending_batch_jobs is not None:
                pending_batch_jobs = None
                pending_batch_index = 0
                pending_request_value = None
                refresh_again = False
                refresh_after_enable = True
                log("resync_paused", reason="room_disabled")
            if enabled and refresh_after_enable and pending_batch_jobs is None:
                pending_batch_jobs, _ = build_refresh_jobs(
                    ephemeris, config, observer, time.time(), bridge, target,
                    rotation, moon_target, fixed_sky, planets)
                pending_batch_index = 0
                pending_request_value = "room_reenabled"
                refresh_after_enable = False
                log("resync_started", reason="room_reenabled",
                    batches=len(pending_batch_jobs), observer=observer)
            if enabled and pending_batch_jobs is not None:
                if pending_batch_index < len(pending_batch_jobs):
                    job = pending_batch_jobs[pending_batch_index]
                    count = client.publish_variable(job["variable"], job["rows"])
                    for row in job["rows"]:
                        current = current_values if row["target"] == target else (
                            moon_current if row["target"] == moon_target else
                            next(profile["values"] for profile in fixed_sky + planets
                                 if profile["target"] == row["target"]))
                        current[job["variable"]] = int(row["value"])
                    log("celestial_batch", variable=job["variable"], furni_count=count,
                        batch_number=pending_batch_index+1,
                        batches=len(pending_batch_jobs), request=pending_request_value)
                    pending_batch_index += 1
                elif refresh_again:
                    pending_batch_jobs, _ = build_refresh_jobs(
                        ephemeris, config, observer, time.time(), bridge, target,
                        rotation, moon_target, fixed_sky, planets)
                    pending_batch_index = 0
                    pending_request_value = "coalesced"
                    refresh_again = False
                    log("resync_recomputed", batches=len(pending_batch_jobs), observer=observer)
                else:
                    log("resync_complete", request=pending_request_value,
                        batches=len(pending_batch_jobs))
                    pending_batch_jobs = None
                    pending_batch_index = 0
                    pending_request_value = None
                    last_frame = (current_values[bridge["occupation_variable"]],
                                  current_values[bridge["altitude_variable"]])
                    next_update = time.monotonic() + config["update_seconds"]
            elif enabled and time.monotonic() >= next_update:
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
                    written = write_display_command(client, moon_target, bridge,
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
                for profiles, frame_function, event in (
                        (planets, body_command_frame, "planet_frame"),
                        (fixed_sky, constellation_command_frame, "fixed_sky_frame")):
                    for profile in profiles:
                        item = profile["config"]
                        frame = frame_function(
                            ephemeris, config, item, observer, sampled_at, profile["rotation"])
                        current_star = profile["values"]
                        expected_star = (frame["occupation"], frame["height"])
                        actual_star = (current_star[bridge["occupation_variable"]],
                                       current_star[bridge["altitude_variable"]])
                        state_variable = item.get("state_variable", "state")
                        if "state" in frame:
                            expected_star += (frame["state"],)
                            actual_star += (current_star[state_variable],)
                        if expected_star != actual_star:
                            if "state" in frame:
                                written = write_display_command(
                                    client, profile["target"], bridge, state_variable,
                                    frame, current_star)
                            else:
                                written = write_command(client, profile["target"], bridge,
                                                        frame, current_star)
                            log(event, name=item["name"], furni_id=item["furni_id"],
                                utc=datetime.fromtimestamp(sampled_at, timezone.utc).isoformat(),
                                observer=observer,
                                altitude_degrees=round(frame["altitude_degrees"], 4),
                                azimuth_degrees=round(frame["azimuth_degrees"], 4),
                                x=frame["x"], y=frame["y"], occupation=frame["occupation"],
                                height=frame["height"], rotation=frame["rotation"],
                                state=frame.get("state", current_star[state_variable]),
                                variables_written=written)
                            failures = 0
                next_update = time.monotonic() + config["update_seconds"]
            wake = min(next_poll, next_update) if enabled else next_poll
            if enabled and pending_batch_jobs is not None:
                wake = time.monotonic()
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
    parser.add_argument("--camera", action="store_true",
                        help="enable directional views; requires astra_view and visible bindings")
    parser.add_argument("--view", type=int, choices=range(-1, 9),
                        help="preview a particular view without changing room controls")
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
        constellations = validate_constellations(config, live=not args.preview)
        planet_configs = validate_planets(config)
        finite(args.duration, 1, 5*60*60+55*60, "duration")
        ephemeris = Ephemeris(args.ephemeris, config)
        if args.view is not None and not args.preview:
            raise ConfigurationError("--view is read-only; use room astra_view for live changes")
        if args.camera or config.get("camera", {}).get("enabled", False):
            from camera_controller import preview_camera, run_camera, validate_camera
            validate_camera(config)
            client = HabboClient(config, os.environ.get("HABBO_READ_KEY"),
                                 None if args.preview else os.environ.get("HABBO_WRITE_KEY"))
            if args.preview:
                preview_camera(config, client, ephemeris, args.view)
            else:
                if not client.write_key:
                    raise ConfigurationError("Set HABBO_WRITE_KEY in GitHub Actions secrets")
                run_camera(config, client, ephemeris, args.duration)
            return 0
        if args.preview:
            bridge = config.get("motion_bridge", BRIDGE)
            observer = config["default_observer"]
            planets = []
            if os.environ.get("HABBO_READ_KEY"):
                reader = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
                _, observer, update_request = reader.controls_with_update_request()
                definitions = reader.request("GET", "")
                sun = config["bodies"][0]
                _, sun_values = read_furni_profile(
                    reader, definitions, sun["target_kind"], sun["furni_id"],
                    (bridge["occupation_variable"], bridge["altitude_variable"]))
                _, moon_values = read_furni_profile(
                    reader, definitions, moon_config["target_kind"], moon_config["furni_id"],
                    (bridge["occupation_variable"], bridge["altitude_variable"],
                     moon_config["state_variable"]))
                fixed_sky = read_constellation_profiles(reader, definitions, config, bridge)
                planets = read_planet_profiles(reader, definitions, config, bridge)
                for profile in fixed_sky:
                    log("fixed_sky_preflight", name=profile["config"]["name"],
                        furni_id=profile["config"]["furni_id"], variables=profile["values"])
                for profile in planets:
                    log("planet_preflight", name=profile["config"]["name"],
                        furni_id=profile["config"]["furni_id"],
                        variables=profile["values"], rotation=profile["rotation"])
                log("furni_preflight", sun_variables=sorted(sun_values),
                    moon_variables=sorted(moon_values), moon_state=moon_values[moon_config["state_variable"]],
                    update_request=update_request,
                    planet_targets=len(planets),
                    fixed_sky_targets=len(fixed_sky),
                    fixed_sky_variables=sorted((bridge["occupation_variable"],
                                                 bridge["altitude_variable"], "state")))
            preview_time = time.time()
            preview_view = -1 if args.view is None else args.view
            frame = command_frame(ephemeris, config, observer, preview_time, 0, view=preview_view)
            log("scheduled_sun_preview", observer=observer,
                screen_aspect_ratio=bridge["screen_aspect_ratio"],
                native_height_units_per_tile=bridge["native_height_units_per_tile"], **frame)
            moon_frame = moon_command_frame(ephemeris, config, observer, preview_time, view=preview_view)
            log("scheduled_moon_preview", observer=observer, **moon_frame)
            for item in planet_configs:
                rotation = next((profile["rotation"] for profile in planets
                                 if profile["config"] is item), 0)
                frame = body_command_frame(ephemeris, config, item, observer,
                                           preview_time, rotation=rotation, view=preview_view)
                log("scheduled_planet_preview", observer=observer,
                    name=item["name"], furni_id=item["furni_id"], **frame)
            for item in constellations:
                frame = constellation_command_frame(
                    ephemeris, config, item, observer, preview_time, rotation=0, view=preview_view)
                anchor, _ = fixed_sky_anchor(config, item)
                log("scheduled_fixed_sky_preview", observer=observer,
                    name=item["name"], furni_id=item["furni_id"],
                    ra_degrees=anchor["ra_degrees"], dec_degrees=anchor["dec_degrees"], **frame)
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
