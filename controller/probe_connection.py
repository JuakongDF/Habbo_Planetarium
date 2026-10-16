"""Read-only binding discovery. No writes and no astronomy dependencies.

The given ID equals the BC offset; query both explicit candidate bindings.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request

from realtime import ApiError, ConfigurationError, HabboClient, integer, log


def validate_connection(config):
    url = urlsplit(config["hotel_origin"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or
            url.path not in ("", "/") or url.query or url.fragment):
        raise ConfigurationError("Expected a plain HTTPS hotel origin")
    config["hotel_origin"] = config["hotel_origin"].rstrip("/")
    integer(config["room_id"], 1, 2**63-1, "room ID")
    if not isinstance(config["variable"], str) or not config["variable"]:
        raise ConfigurationError("Expected a variable name")
    if not 1 <= len(config["candidates"]) <= 4:
        raise ConfigurationError("Expected 1…4 explicit candidates")
    for candidate in config["candidates"]:
        if candidate["target_kind"] not in ("furni", "furni-bc"):
            raise ConfigurationError("Unsupported candidate target kind")
        integer(candidate["entity_id"], 0 if candidate["target_kind"] == "furni-bc" else 1,
                2**63-1, "entity ID")
    if len(config.get("controls", [])) > 4 or any(
            not isinstance(name, str) or not name for name in config.get("controls", [])):
        raise ConfigurationError("Invalid controls list")
    return config


def read_value(client, path):
    try:
        result = client.request("GET", path)
        if not isinstance(result, dict) or "value" not in result:
            raise ApiError("Malformed variable response")
        value = integer(result["value"], -(2**63), 2**63-1, "variable value")
        # Report existence and numeric validity, not arbitrary room data.
        return {"found": True, "valid_int64": True, "is_zero": value == 0}
    except ApiError as exc:
        return {"found": False, "error": str(exc)}


def public_api_check(client, origin, path="/api/public/achievements"):
    """Compare a public endpoint without sending any room credentials."""
    request = Request(origin + path, headers={"Accept": "application/json"})
    try:
        with client.opener.open(request, timeout=10) as response:
            return {"status": response.status, "content_type": response.headers.get_content_type()}
    except HTTPError as exc:
        return {"status": exc.code, "content_type": exc.headers.get_content_type()}
    except (URLError, TimeoutError, OSError):
        return {"status": None, "error": "network_error"}


def probe(config, client):
    candidates = []
    for candidate in config["candidates"]:
        path = "/furni/" + quote(config["variable"], safe="") + "/" + candidate["target_kind"] + "/" + str(candidate["entity_id"])
        candidates.append({**candidate, **read_value(client, path)})
    controls = {name: read_value(client, "/global/" + quote(name, safe=""))
                for name in config.get("controls", [])}
    matches = [row for row in candidates if row["found"]]
    return dict(room=config["room_id"], variable=config["variable"], candidates=candidates,
                controls=controls, unique_binding=len(matches) == 1,
                ready_for_controller=len(matches) == 1 and all(v["found"] for v in controls.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent.parent / "connection.json")
    args = parser.parse_args()
    try:
        config = validate_connection(json.loads(args.config.read_text()))
        client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), None)
        log("public_api_check", **public_api_check(client, config["hotel_origin"]))
        candidate = config["candidates"][0]
        unauthenticated_path = (f'/api/public/rooms/{config["room_id"]}/variables/furni/' +
                                quote(config["variable"], safe="") + "/" +
                                candidate["target_kind"] + "/" + str(candidate["entity_id"]))
        log("wired_route_without_key", **public_api_check(client, config["hotel_origin"], unauthenticated_path))
        result = probe(config, client)
        log("connection_probe", **result)
        return 0 if result["unique_binding"] else 1
    except (ApiError, ConfigurationError, OSError, ValueError, TypeError, KeyError) as exc:
        log("probe_error", message=str(exc) if isinstance(exc, (ApiError, ConfigurationError))
            else "Invalid configuration or unreadable file")
        return 1


if __name__ == "__main__":
    sys.exit(main())
