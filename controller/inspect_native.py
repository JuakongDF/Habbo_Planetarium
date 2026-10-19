"""Inspect exposed occupation/altitude variables on the Sun. GET requests only."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

from probe_connection import validate_connection
from realtime import ApiError, ConfigurationError, HabboClient, integer, log


def relevant(name):
    return isinstance(name, str) and any(word in name.casefold() for word in ("occupation", "altitude"))


def decode_occupation(value):
    value = integer(value, 0, 2**63-1, "occupation")
    return {"x": value // 65536, "y": (value // 256) % 256, "rotation": value % 256}


def inspect(config, client):
    definitions = client.request("GET", "")
    if not isinstance(definitions, dict):
        raise ApiError("Malformed definitions response")
    names_by_scope = {}
    counts_by_scope = {}
    for scope in ("users", "furni", "global"):
        names = definitions.get(scope, [])
        if not isinstance(names, list):
            raise ApiError("Malformed definition names")
        counts_by_scope[scope] = len(names)
        names_by_scope[scope] = [name for name in names if relevant(name)]
    candidate = config["candidates"][0]
    target = candidate["target_kind"] + "/" + str(candidate["entity_id"])
    profile = client.request("GET", "/furni/" + target, resource="variables_profile",
                             costs={"profile_read": 1})
    variables = profile.get("variables") if isinstance(profile, dict) else None
    if not isinstance(variables, dict):
        raise ApiError("Malformed furniture profile")
    values = {}
    for name, binding in variables.items():
        if not relevant(name):
            continue
        if not isinstance(binding, dict) or "value" not in binding:
            raise ApiError("Malformed native variable binding")
        value = integer(binding["value"], -(2**63), 2**63-1, "native value")
        entry = {"value": str(value)}
        if "occupation" in name.casefold() and value >= 0:
            entry["decoded_using_supplied_formula"] = decode_occupation(value)
        values[name] = entry
    # Check the user-supplied names too, even if a hotel's profile omits them.
    names = set(names_by_scope["furni"]) | {"occupation", "altitude", "@occupation", "@altitude"}
    reads = {}
    for name in sorted(names - set(values)):
        try:
            response = client.request("GET", "/furni/" + quote(name, safe="") + "/" + target)
            value = integer(response["value"], -(2**63), 2**63-1, "native value")
            entry = {"value": str(value)}
            if "occupation" in name.casefold() and value >= 0:
                entry["decoded_using_supplied_formula"] = decode_occupation(value)
            values[name] = entry
            reads[name] = {"found": True}
        except ApiError as exc:
            reads[name] = {"found": False, "error": str(exc)}
    return {"room": config["room_id"], "target": target,
            "definition_counts": counts_by_scope,
            "definition_total": sum(counts_by_scope.values()),
            "matching_definitions": names_by_scope,
            "profile_variable_count": len(variables), "native_values": values,
            "additional_reads": reads, "writes_performed": False}


def main():
    try:
        path = Path(__file__).parent.parent / "connection.json"
        config = validate_connection(json.loads(path.read_text()))
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
        log("native_inspection", **inspect(config, client))
        return 0
    except (ApiError, ConfigurationError, OSError, KeyError, ValueError, TypeError) as exc:
        log("inspection_error", message=str(exc) if isinstance(exc, (ApiError, ConfigurationError))
            else "Invalid configuration or malformed API response")
        return 1


if __name__ == "__main__":
    sys.exit(main())
