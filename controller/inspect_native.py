"""Inspect exposed occupation/altitude variables on the Sun. GET requests only."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
from urllib.error import HTTPError

from probe_connection import validate_connection
from realtime import ApiError, ConfigurationError, HabboClient, integer, log


def relevant(name):
    return isinstance(name, str) and any(word in name.casefold() for word in ("occupation", "altitude"))


def decode_occupation(value):
    value = integer(value, 0, 2**63-1, "occupation")
    return {"x": value // 65536, "y": (value // 256) % 256, "rotation": value % 256}


class CacheDiagnosticOpener:
    """Record only cache-related response headers, never credentials or bodies."""

    def __init__(self, opener):
        self.opener = opener
        self.revalidate = False
        self.responses = []

    def open(self, request, timeout):
        if self.revalidate:
            request.add_header("Cache-Control", "no-cache")
            request.add_header("Pragma", "no-cache")
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as exc:
            self.record(request, exc)
            raise
        self.record(request, response)
        return response

    def record(self, request, response):
        allowed = ("Date", "Age", "Cache-Control", "Expires", "CF-Cache-Status", "X-Cache")
        self.responses.append({
            "revalidation_requested": self.revalidate,
            "path": request.selector,
            "status": response.code,
            "headers": {name: response.headers[name][:256] for name in allowed
                        if response.headers.get(name) is not None},
        })


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
        names_by_scope[scope] = [name for name in names
                                 if relevant(name) or (scope == "global" and "id" in name.casefold())]
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
    names = {name for name in names_by_scope["furni"] if relevant(name)}
    names.update({"occupation", "altitude", "@occupation", "@altitude"})
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
    global_ids = [name for name in names_by_scope["global"] if "id" in name.casefold()]
    global_values = {}
    for name in global_ids:
        try:
            response = client.request("GET", "/global/" + quote(name, safe=""))
            value = integer(response["value"], -(2**63), 2**63-1, "global selector value")
            global_values[name] = str(value)
        except ApiError as exc:
            global_values[name] = {"error": str(exc)}
    return {"room": config["room_id"], "target": target,
            "definition_counts": counts_by_scope,
            "definition_total": sum(counts_by_scope.values()),
            "matching_definitions": names_by_scope,
            "profile_variable_count": len(variables), "native_values": values,
            "global_id_values": global_values,
            "additional_reads": reads, "writes_performed": False}


def main():
    try:
        path = Path(__file__).parent.parent / "connection.json"
        config = validate_connection(json.loads(path.read_text()))
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
        if "--check-cache" in sys.argv[1:]:
            observer = CacheDiagnosticOpener(client.opener)
            client.opener = observer
            ordinary = inspect(config, client)
            observer.revalidate = True
            revalidated = inspect(config, client)
            log("cache_inspection", ordinary=ordinary, revalidated=revalidated,
                responses=observer.responses, writes_performed=False)
        else:
            log("native_inspection", **inspect(config, client))
        return 0
    except (ApiError, ConfigurationError, OSError, KeyError, ValueError, TypeError) as exc:
        log("inspection_error", message=str(exc) if isinstance(exc, (ApiError, ConfigurationError))
            else "Invalid configuration or malformed API response")
        return 1


if __name__ == "__main__":
    sys.exit(main())
