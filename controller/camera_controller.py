"""Opt-in shared-room directional sky controller using permanent WIRED variables."""
import re
import time
from urllib.parse import quote

from realtime import ApiError, ConfigurationError, RetryLater, finite, integer, log
from scheduled_sun import (BRIDGE, body_command_frame, constellation_command_frame,
                           fixed_sky_anchor, moon_command_frame, read_furni_profile)

VIEW_NAMES = {-1: "whole-sky overlay", 0: "north", 1: "northeast", 2: "east",
              3: "southeast", 4: "south", 5: "southwest", 6: "west",
              7: "northwest", 8: "zenith"}


def validate_camera(config):
    camera = config.get("camera", {})
    if not isinstance(camera, dict) or not isinstance(camera.get("enabled", False), bool):
        raise ConfigurationError("Invalid camera configuration")
    bridge = config.get("motion_bridge", BRIDGE)
    for key in ("view_variable", "visibility_variable"):
        if not isinstance(camera.get(key), str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,39}", camera[key]):
            raise ConfigurationError("Configure camera " + key)
    if camera["view_variable"] in (config["enabled_variable"], config["observer_variable"],
                                    config["update_request_variable"]):
        raise ConfigurationError("Camera view needs a distinct global variable")
    if camera["visibility_variable"] in {
            bridge["occupation_variable"], bridge["altitude_variable"],
            config["moon"]["state_variable"],
            *(item.get("state_variable", "state")
              for item in config.get("constellations", []) + config["bodies"][1:])}:
        raise ConfigurationError("Camera visibility needs a distinct furni variable")
    finite(bridge["screen_aspect_ratio"], .5, 4, "screen aspect ratio")
    finite(bridge["native_height_units_per_tile"], .1, 200, "native height units per tile")
    finite(bridge["screen_horizon_height"], 0, 10000, "screen center height")
    radius = ((config["projection"]["room_size"] - 1) / 2 /
              bridge["screen_aspect_ratio"] * bridge["native_height_units_per_tile"])
    offsets = [item.get("intrinsic_altitude_offset", 0)
               for item in config["bodies"] + [config["moon"]]]
    offsets += [fixed_sky_anchor(config, item)[1] for item in config.get("constellations", [])]
    for offset in offsets:
        finite(offset, -10000, 10000, "intrinsic altitude offset")
        if not (0 <= bridge["screen_horizon_height"] - radius - offset and
                bridge["screen_horizon_height"] + radius - offset <= 10000):
            raise ConfigurationError("Camera command altitudes must fit 0…10000")
    return camera


def read_camera_controls(client, config):
    enabled, observer, request = client.controls_with_update_request()
    result = client.request("GET", "/global/" + quote(config["camera"]["view_variable"], safe=""))
    if not isinstance(result, dict) or "value" not in result:
        raise ApiError("Malformed camera view response")
    return enabled, observer, request, integer(result["value"], -1, 8, "camera view")


def read_camera_profiles(client, config):
    """Validate every binding before any live write, including visibility."""
    camera = validate_camera(config)
    bridge = config.get("motion_bridge", BRIDGE)
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed variable definitions")
    profiles = []
    items = [("body", item) for item in config["bodies"]]
    items += [("moon", config["moon"])]
    items += [("fixed", item) for item in config.get("constellations", [])]
    for kind, item in items:
        required = [bridge["occupation_variable"], bridge["altitude_variable"],
                    camera["visibility_variable"]]
        if kind != "body" or item["name"] != "sun":
            required.append(item.get("state_variable", "state"))
        target, values = read_furni_profile(client, definitions,
                                            item.get("target_kind", "furni"),
                                            item["furni_id"], required)
        occupation = integer(values[required[0]], 0, 2**63-1, "occupation")
        rotation = occupation % 256
        if occupation // 65536 > 55 or (occupation // 256) % 256 > 55 or rotation > 7:
            raise ConfigurationError("Invalid camera furni occupation: " + target)
        integer(values[required[1]], 0, 10000, "altitude")
        integer(values[required[2]], 0, 1, "visible")
        profiles.append({"kind": kind, "config": item, "target": target,
                         "rotation": rotation, "values": values})
    return profiles


def sample_frames(config, ephemeris, profiles, observer, view, timestamp):
    bridge = config.get("motion_bridge", BRIDGE)
    frames = []
    for profile in profiles:
        item = profile["config"]
        if profile["kind"] == "moon":
            frame = moon_command_frame(ephemeris, config, observer, timestamp, view=view)
        else:
            function = constellation_command_frame if profile["kind"] == "fixed" else body_command_frame
            frame = function(ephemeris, config, item, observer, timestamp,
                             profile["rotation"], view=view)
        values = {}
        if "state" in frame:
            values[item.get("state_variable", "state")] = frame["state"]
        values[bridge["altitude_variable"]] = frame["height"]
        values[bridge["occupation_variable"]] = frame["occupation"]
        values[config["camera"]["visibility_variable"]] = frame["visible"]
        frames.append({"profile": profile, "frame": frame, "values": values})
    return frames


def build_jobs(config, frames, force=False):
    """Hide the back hemisphere first; show arriving objects after their positions.

    API batches are per variable, capped at ten targets, and are not atomic.
    A forced refresh rewrites every controlled value, even if the cache matches.
    """
    visibility = config["camera"]["visibility_variable"]
    hide, show, changes = [], [], {}
    for entry in frames:
        profile = entry["profile"]
        item = profile["config"]
        for variable, value in entry["values"].items():
            if not force and profile["values"].get(variable) == value:
                continue
            row = {"target_kind": item.get("target_kind", "furni"), "furni_id": item["furni_id"],
                   "target": profile["target"], "value": str(value)}
            if variable == visibility:
                (show if value else hide).append(row)
            else:
                changes.setdefault(variable, []).append(row)
    # All state variables precede altitude and occupation, even if names differ.
    bridge = config.get("motion_bridge", BRIDGE)
    position = (bridge["altitude_variable"], bridge["occupation_variable"])
    order = [(visibility, hide)]
    order += [(variable, rows) for variable, rows in changes.items() if variable not in position]
    order += [(variable, changes.get(variable, [])) for variable in position]
    order += [(visibility, show)]
    return [{"variable": variable, "rows": rows[start:start+10]}
            for variable, rows in order for start in range(0, len(rows), 10)]


def preview_camera(config, client, ephemeris, view_override=None):
    profiles = read_camera_profiles(client, config)
    enabled, observer, request, view = read_camera_controls(client, config)
    if view_override is not None:
        view = integer(view_override, -1, 8, "camera preview view")
    log("camera_preflight", enabled=enabled, observer=observer, view=view,
        view_name=VIEW_NAMES[view], targets=len(profiles), update_request=request,
        writes_performed=False)
    frames = sample_frames(config, ephemeris, profiles, observer, view, time.time())
    for entry in frames:
        log("camera_preview", target=entry["profile"]["target"],
            name=entry["profile"]["config"].get("name", "moon"), view=view, **entry["frame"])


def run_camera(config, client, ephemeris, duration):
    profiles = read_camera_profiles(client, config)
    by_target = {profile["target"]: profile["values"] for profile in profiles}
    deadline = time.monotonic() + duration
    next_poll = next_frame = write_ready = 0
    control = None
    controls_current = False
    jobs = []
    last_request = 0
    ack_pending = False
    force = True
    failures = 0
    log("camera_started", targets=len(profiles), duration_seconds=duration)
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_poll:
            try:
                enabled, observer, request, view = read_camera_controls(client, config)
                new_control = (enabled, observer, view)
                if control != new_control:
                    # Discard stale batches before any further write for the new view.
                    jobs = []
                    force = True
                    next_frame = 0
                    control = new_control
                    log("camera_controls", enabled=enabled, observer=observer,
                        view=view, view_name=VIEW_NAMES[view])
                if not request:
                    last_request = 0
                elif enabled and request != last_request:
                    jobs = []
                    force = True
                    next_frame = 0
                    ack_pending = True
                    last_request = request
                    log("camera_refresh_requested", value=request)
                controls_current = True
                next_poll = time.monotonic() + config["poll_seconds"]
            except (ApiError, ConfigurationError) as exc:
                # Unknown views/observers must never result in a guessed live view.
                controls_current = False
                delay = max(config["poll_seconds"], exc.delay if isinstance(exc, RetryLater) else 5)
                next_poll = time.monotonic() + delay
                log("camera_control_error", message=str(exc), retry_seconds=delay)
        if controls_current and control[0] and time.monotonic() >= write_ready:
            try:
                if ack_pending:
                    client.clear_update_request()
                    ack_pending = False
                if not jobs and time.monotonic() >= next_frame:
                    frames = sample_frames(config, ephemeris, profiles, control[1], control[2], time.time())
                    jobs = build_jobs(config, frames, force=force)
                    log("camera_frame", observer=control[1], view=control[2], full_refresh=force,
                        visible=sum(entry["frame"]["visible"] for entry in frames), batches=len(jobs))
                    force = False
                    next_frame = time.monotonic() + config["update_seconds"]
                if jobs:
                    job = jobs[0]
                    try:
                        count = client.publish_variable(job["variable"], job["rows"])
                    except ApiError:
                        # Partial success is possible. Never trust those cached values.
                        for row in job["rows"]:
                            by_target[row["target"]][job["variable"]] = None
                        raise
                    for row in job["rows"]:
                        by_target[row["target"]][job["variable"]] = int(row["value"])
                    jobs.pop(0)
                    log("camera_batch", view=control[2], variable=job["variable"],
                        furni_count=count, batches_remaining=len(jobs))
                    if not jobs:
                        next_frame = time.monotonic() + config["update_seconds"]
                        log("camera_frame_complete", observer=control[1], view=control[2])
                failures = 0
            except ApiError as exc:
                failures += 1
                delay = max(exc.delay if isinstance(exc, RetryLater) else 1,
                            min(60, 2**min(failures-1, 6)))
                write_ready = time.monotonic() + delay
                log("camera_write_retry", message=str(exc), retry_seconds=delay)
        wake = next_poll
        if controls_current and control[0]:
            wake = min(wake, max(write_ready, time.monotonic() if jobs or ack_pending else next_frame))
        time.sleep(max(0, min(wake-time.monotonic(), deadline-time.monotonic())))
    log("camera_stopped", reason="duration_reached", pending_batches=len(jobs))
