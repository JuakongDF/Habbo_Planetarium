"""Apply one flat diagonal motion command through permanent WIRED variables."""
import json
import os
import argparse
from pathlib import Path
import sys
import time
from urllib.parse import quote

from probe_connection import validate_connection
from realtime import ApiError, ConfigurationError, HabboClient, integer, log


ROOM_FURNI_ID = 2147418112
TARGET_KIND = "furni-bc"
TARGET_ENTITY_ID = 0
OCCUPATION_VARIABLE = "occupation"
ALTITUDE_VARIABLE = "altitude"
TRIGGER_VARIABLE = "update_furni_id"


def read_int(client, path, label):
    result = client.request("GET", path)
    if not isinstance(result, dict) or "value" not in result:
        raise ApiError("Malformed " + label + " response")
    return integer(result["value"], -(2**63), 2**63-1, label)


def require_write_result(result, expected, label):
    if not isinstance(result, dict) or "value" not in result:
        raise ApiError("Malformed " + label + " write response")
    actual = integer(result["value"], -(2**63), 2**63-1, label)
    if actual != expected:
        raise ApiError(label + " write did not read back as requested")


def run(config, client):
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed variable definitions")
    furni_names = definitions.get("furni")
    global_names = definitions.get("global")
    if not isinstance(furni_names, list) or not isinstance(global_names, list):
        raise ApiError("Malformed variable definitions")
    if not {OCCUPATION_VARIABLE, ALTITUDE_VARIABLE}.issubset(furni_names):
        raise ConfigurationError("Create occupation and altitude as furniture variables first")
    if TRIGGER_VARIABLE not in global_names:
        raise ConfigurationError("Create update_furni_id as a global variable first")

    target = f"{TARGET_KIND}/{TARGET_ENTITY_ID}"
    profile = client.request("GET", "/furni/" + target, resource="variables_profile",
                             costs={"profile_read": 1})
    bindings = profile.get("variables") if isinstance(profile, dict) else None
    if not isinstance(bindings, dict) or not {OCCUPATION_VARIABLE, ALTITUDE_VARIABLE}.issubset(bindings):
        raise ConfigurationError("Assign occupation and altitude to the Sun first")
    initial_occupation = integer(bindings[OCCUPATION_VARIABLE].get("value"), 0, 2**63-1, "occupation")
    initial_altitude = integer(bindings[ALTITUDE_VARIABLE].get("value"), -(2**63), 2**63-1, "altitude")
    current_selector = read_int(client, "/global/" + TRIGGER_VARIABLE, "selector")
    if current_selector != 0:
        raise ConfigurationError("Selector must be zero before the motion test")

    x = initial_occupation // 65536
    y = (initial_occupation // 256) % 256
    rotation = initial_occupation % 256
    if x >= 55 or y <= 0:
        raise ConfigurationError("Current occupation has no safe next flat-diagonal tile")
    target_occupation = 65536 * (x + 1) + 256 * (y - 1) + rotation
    values = {OCCUPATION_VARIABLE: target_occupation, ALTITUDE_VARIABLE: initial_altitude}
    paths = {name: f"/furni/{name}/{target}" for name in values}
    written = []
    try:
        for name, value in values.items():
            written.append(name)
            response = client.request("PATCH", paths[name], {"value": str(value)})
            require_write_result(response, value, name)
    except Exception:
        # Restore any command values changed before the trigger was sent.
        originals = {OCCUPATION_VARIABLE: initial_occupation, ALTITUDE_VARIABLE: initial_altitude}
        for name in reversed(written):
            try:
                response = client.request("PATCH", paths[name], {"value": str(originals[name])})
                require_write_result(response, originals[name], name + " rollback")
            except Exception:
                log("motion_test_rollback_failed", variable=name)
        raise

    # Signal last, after both command variables have been accepted and read back.
    trigger_path = "/global/" + TRIGGER_VARIABLE
    trigger_response = client.request("PATCH", trigger_path, {"value": str(ROOM_FURNI_ID)})
    require_write_result(trigger_response, ROOM_FURNI_ID, "selector trigger")

    reset_observed = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if read_int(client, trigger_path, "selector") == 0:
            reset_observed = True
            break
        time.sleep(0.1)

    return {
        "room": config["room_id"], "target": target,
        "initial_pose": {"x": x, "y": y, "rotation": rotation},
        "command_pose": {"x": x + 1, "y": y - 1, "rotation": rotation},
        "occupation_command": str(target_occupation),
        "altitude_command": str(initial_altitude),
        "selector_trigger_id": str(ROOM_FURNI_ID),
        "selector_reset_observed": reset_observed,
        "writes_performed": True,
        "restored": False,
    }


def main():
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--reset-selector", action="store_true",
                            help="clear only the selector left armed by this motion test")
        args = parser.parse_args()
        path = Path(__file__).parent.parent / "connection.json"
        config = validate_connection(json.loads(path.read_text()))
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), os.environ.get("HABBO_WRITE_KEY"))
        if args.reset_selector:
            trigger_path = "/global/" + TRIGGER_VARIABLE
            current = read_int(client, trigger_path, "selector")
            if current == 0:
                log("selector_already_clear", writes_performed=False)
                return 0
            if current != ROOM_FURNI_ID:
                raise ConfigurationError("Selector has a value other than this test's ID; refusing to clear it")
            result = client.request("PATCH", trigger_path, {"value": "0"})
            require_write_result(result, 0, "selector reset")
            log("selector_cleared", previous_value=str(current), value="0", writes_performed=True)
            return 0
        log("motion_api_test", **run(config, client))
        return 0
    except (ApiError, ConfigurationError, OSError, KeyError, ValueError, TypeError) as exc:
        log("motion_api_test_error", message=str(exc) if isinstance(exc, (ApiError, ConfigurationError))
            else "Invalid configuration or malformed API response")
        return 1


if __name__ == "__main__":
    sys.exit(main())
