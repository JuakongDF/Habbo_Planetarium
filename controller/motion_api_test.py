"""Test direct occupation-variable movement without a global selector."""
import json
import os
from pathlib import Path
import sys

from probe_connection import validate_connection
from realtime import ApiError, ConfigurationError, HabboClient, integer, log


TARGET_KIND = "furni-bc"
TARGET_ENTITY_ID = 0
OCCUPATION_VARIABLE = "occupation"
ALTITUDE_VARIABLE = "altitude"


def run(config, client):
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed variable definitions")
    furni_names = definitions.get("furni")
    if not isinstance(furni_names, list):
        raise ApiError("Malformed furniture variable definitions")
    if not {OCCUPATION_VARIABLE, ALTITUDE_VARIABLE}.issubset(furni_names):
        raise ConfigurationError("Create occupation and altitude as furniture variables first")

    target = f"{TARGET_KIND}/{TARGET_ENTITY_ID}"
    profile = client.request("GET", "/furni/" + target, resource="variables_profile",
                             costs={"profile_read": 1})
    bindings = profile.get("variables") if isinstance(profile, dict) else None
    if not isinstance(bindings, dict) or OCCUPATION_VARIABLE not in bindings:
        raise ConfigurationError("Assign occupation to the Sun first")
    initial = integer(bindings[OCCUPATION_VARIABLE].get("value"), 0, 2**63-1,
                      "occupation")
    x, y, rotation = initial // 65536, (initial // 256) % 256, initial % 256
    if x >= 55 or y <= 0:
        raise ConfigurationError("Current occupation has no safe next flat-diagonal tile")
    target_occupation = 65536 * (x + 1) + 256 * (y - 1) + rotation

    response = client.request("PATCH", f"/furni/{OCCUPATION_VARIABLE}/{target}",
                              {"value": str(target_occupation)})
    if not isinstance(response, dict) or integer(response.get("value"), 0, 2**63-1,
                                                  "occupation") != target_occupation:
        raise ApiError("Occupation command did not read back as requested")
    return {
        "room": config["room_id"], "target": target,
        "initial_pose": {"x": x, "y": y, "rotation": rotation},
        "command_pose": {"x": x + 1, "y": y - 1, "rotation": rotation},
        "occupation_command": str(target_occupation),
        "variables_written": [OCCUPATION_VARIABLE],
        "trigger_variable_used": False,
        "writes_performed": True,
    }


def main():
    try:
        path = Path(__file__).parent.parent / "connection.json"
        config = validate_connection(json.loads(path.read_text()))
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"),
                             os.environ.get("HABBO_WRITE_KEY"))
        if not client.write_key:
            raise ConfigurationError("Set HABBO_WRITE_KEY in GitHub Actions secrets")
        log("direct_motion_api_test", **run(config, client))
        return 0
    except (ApiError, ConfigurationError, OSError, KeyError, ValueError, TypeError) as exc:
        log("direct_motion_api_test_error", message=str(exc) if isinstance(
            exc, (ApiError, ConfigurationError)) else "Invalid configuration or malformed API response")
        return 1


if __name__ == "__main__":
    sys.exit(main())
