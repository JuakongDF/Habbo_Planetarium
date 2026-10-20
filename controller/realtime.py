"""Bounded real-time Habbo prototype. Preview by default; --live enables API writes.

Keys are read only from HABBO_READ_KEY/HABBO_WRITE_KEY. No implicit downloads.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "HabboPlanetarium/0.1 (+https://github.com/JuakongDF/Habbo_Planetarium)"


class ConfigurationError(ValueError):
    pass


class ApiError(RuntimeError):
    pass


class RetryLater(ApiError):
    def __init__(self, message, delay=1):
        super().__init__(message)
        self.delay = delay


def integer(value, minimum, maximum, label):
    # Accept exact JSON integers or decimal strings, never rounded floats/bools.
    if isinstance(value, bool) or not (
        isinstance(value, int) or
        isinstance(value, str) and re.fullmatch(r"-?\d+", value)
    ):
        raise ConfigurationError(f"{label} must be an exact integer")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ConfigurationError(f"{label} out of range")
    return result


def finite(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{label} must be a number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{label} out of range")
    return value


def validate_config(c, live=False):
    origin = urlsplit(c["hotel_origin"])
    if (origin.scheme != "https" or not origin.hostname or origin.username or
            origin.password or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise ConfigurationError("hotel_origin must be a plain HTTPS origin")
    c["hotel_origin"] = c["hotel_origin"].rstrip("/")
    c["room_id"] = integer(c["room_id"], 1 if live else 0, 2**63-1, "room_id")
    for key in ("state_variable", "observer_variable", "enabled_variable"):
        if not isinstance(c[key], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", c[key]):
            raise ConfigurationError(f"Invalid {key}")
    if len({c[k] for k in ("state_variable", "observer_variable", "enabled_variable")}) != 3:
        raise ConfigurationError("Variable names must be distinct")
    finite(c["poll_seconds"], 1, 60, "poll_seconds")
    finite(c["update_seconds"], 1, 60, "update_seconds")
    if not isinstance(c["observers"], dict) or not c["observers"]:
        raise ConfigurationError("Configure at least one observer")
    for key, observer in c["observers"].items():
        if str(integer(key, 1, 255, "observer ID")) != key:
            raise ConfigurationError("Observer IDs must be canonical decimal strings")
        finite(observer["latitude"], -90, 90, "latitude")
        finite(observer["longitude"], -180, 180, "longitude")
        finite(observer.get("elevation_m", 0), -500, 10000, "elevation")
    c["default_observer"] = integer(c["default_observer"], 1, 255, "default_observer")
    if str(c["default_observer"]) not in c["observers"]:
        raise ConfigurationError("Unknown default observer")
    p = c["projection"]
    p["room_size"] = integer(p["room_size"], 2, 56, "room_size")
    diagonal_geometry(p)
    finite(p["horizon_height"], 0, 2047, "horizon_height")
    finite(p["height_per_degree"], 0.001, 100, "height_per_degree")
    if not (0 <= p["horizon_height"] - 90*p["height_per_degree"] and
            p["horizon_height"] + 90*p["height_per_degree"] <= 2047):
        raise ConfigurationError("Entire altitude range must fit native heights 0…2047")
    if not 1 <= len(c["bodies"]) <= 10:
        raise ConfigurationError("This first prototype supports 1…10 bodies")
    targets = []
    for body in c["bodies"]:
        if body["name"] not in ("sun", "moon", "mercury", "venus", "mars barycenter",
                                 "jupiter barycenter", "saturn barycenter"):
            raise ConfigurationError("Unsupported ephemeris target")
        kind = body.setdefault("target_kind", "furni")
        if kind not in ("furni", "furni-bc"):
            raise ConfigurationError("Unsupported furniture target kind")
        minimum = 1 if live and kind == "furni" else 0
        body["furni_id"] = integer(body["furni_id"], minimum, 2**63-1, "furni_id")
        targets.append((kind, body["furni_id"]))
    if len(targets) != len(set(targets)):
        raise ConfigurationError("Each body must have a different furni_id")
    return c


def pack_state(index, height, visible, observer, timestamp):
    integer(index, 0, 55, "index")
    integer(height, 0, 2047, "height")
    integer(visible, 0, 1, "visible")
    integer(observer, 1, 255, "observer")
    integer(timestamp, 0, 2**32-1, "timestamp")
    # p:6, h:11, visible:1, observer:8, UTC timestamp:32 = 58 bits.
    return index + (height << 6) + (visible << 17) + (observer << 18) + (timestamp << 26)


def unpack_state(value):
    integer(value, 0, 2**58-1, "state")
    result = dict(index=value % 64, height=(value >> 6) % 2048,
                  visible=(value >> 17) % 2, observer=(value >> 18) % 256,
                  timestamp=value >> 26)
    if result["index"] > 55 or result["observer"] == 0:
        raise ConfigurationError("Invalid state fields")
    return result


def project(altitude, azimuth, p):
    finite(altitude, -90, 90, "altitude")
    finite(azimuth, 0, 360, "azimuth")
    # Orthographic east component; northern/southern directions overlap.
    east = math.cos(math.radians(altitude)) * math.sin(math.radians(azimuth))
    # The diagonal index increases eastward, from (1,53) to (54,0).
    cells, start_x, start_y = diagonal_geometry(p)
    index = math.floor((cells-1) * (1+east) / 2 + 0.5)
    height = math.floor(p["horizon_height"] + p["height_per_degree"]*altitude + 0.5)
    return dict(index=index, x=start_x+index, y=start_y-index,
                height=height, visible=int(altitude >= 0))


def diagonal_geometry(p):
    room_size = p["room_size"]
    cells = integer(p.get("diagonal_cells", room_size), 2, room_size, "diagonal_cells")
    start_x = integer(p.get("diagonal_start_x", 0), 0, room_size-1, "diagonal_start_x")
    start_y = integer(p.get("diagonal_start_y", cells-1), 0, room_size-1, "diagonal_start_y")
    if start_x + cells > room_size or start_y - cells + 1 < 0:
        raise ConfigurationError("Configured diagonal endpoints fall outside the room")
    return cells, start_x, start_y


def lunar_phase(separation_degrees, sun_ecliptic_longitude, moon_ecliptic_longitude):
    """Calculate a simple illuminated fraction and waxing/waning flag."""
    separation = finite(separation_degrees, 0, 180, "Sun-Moon separation")
    sun_longitude = finite(sun_ecliptic_longitude, 0, 360, "Sun ecliptic longitude")
    moon_longitude = finite(moon_ecliptic_longitude, 0, 360, "Moon ecliptic longitude")
    elongation = (moon_longitude - sun_longitude) % 360
    return {
        "illumination": (1 - math.cos(math.radians(separation))) / 2,
        "waxing": elongation < 180,
        "elongation_degrees": elongation,
    }


def project_screen_ellipse(altitude, azimuth, p, screen_aspect_ratio, native_height_units_per_tile):
    """Project the full celestial direction, including below-horizon arcs, onto an ellipse."""
    finite(altitude, -90, 90, "altitude")
    finite(azimuth, 0, 360, "azimuth")
    aspect = finite(screen_aspect_ratio, 0.5, 4, "screen_aspect_ratio")
    units = finite(native_height_units_per_tile, 0.1, 200, "native_height_units_per_tile")
    east = math.cos(math.radians(altitude)) * math.sin(math.radians(azimuth))
    # The room's diagonal index increases eastward.
    cells, start_x, start_y = diagonal_geometry(p)
    index = math.floor((cells-1) * (1+east) / 2 + 0.5)
    horizontal_radius_tiles = (p["room_size"]-1) / 2
    vertical_radius_tiles = horizontal_radius_tiles / aspect
    height = math.floor(p["horizon_height"] + vertical_radius_tiles * units *
                        math.sin(math.radians(altitude)) + 0.5)
    integer(height, 0, 10000, "projected height")
    return dict(index=index, x=start_x+index, y=start_y-index,
                height=height, visible=1)


class Ephemeris:
    def __init__(self, path, config):
        from skyfield.api import load, load_file, wgs84
        if not Path(path).is_file():
            raise ConfigurationError("Local ephemeris file not found; see controller/README.md")
        self.timescale = load.timescale(builtin=True)
        self.kernel = load_file(str(path))
        self.observers = {
            int(key): self.kernel["earth"] + wgs84.latlon(
                site["latitude"], site["longitude"], elevation_m=site.get("elevation_m", 0))
            for key, site in config["observers"].items()
        }
        self.config = config

    def apparent_altaz(self, observer_id, body_name, timestamp):
        instant = self.timescale.from_datetime(datetime.fromtimestamp(timestamp, timezone.utc))
        observer = self.observers[observer_id].at(instant)
        alt, az, _ = observer.observe(self.kernel[body_name]).apparent().altaz()
        return float(alt.degrees), float(az.degrees)

    def moon_phase(self, observer_id, timestamp):
        """Return illuminated fraction and waxing status for the selected site."""
        from skyfield.framelib import ecliptic_frame
        instant = self.timescale.from_datetime(datetime.fromtimestamp(timestamp, timezone.utc))
        observer = self.observers[observer_id].at(instant)
        sun = observer.observe(self.kernel["sun"]).apparent()
        moon = observer.observe(self.kernel["moon"]).apparent()
        separation = float(sun.separation_from(moon).degrees)
        sun_longitude = float(sun.frame_latlon(ecliptic_frame)[1].degrees)
        moon_longitude = float(moon.frame_latlon(ecliptic_frame)[1].degrees)
        return lunar_phase(separation, sun_longitude, moon_longitude)

    def frame(self, observer_id, timestamp):
        rows = []
        for body in self.config["bodies"]:
            altitude, azimuth = self.apparent_altaz(observer_id, body["name"], timestamp)
            position = project(altitude, azimuth, self.config["projection"])
            word = pack_state(position["index"], position["height"], position["visible"],
                              observer_id, int(timestamp))
            rows.append(dict(body=body["name"], furni_id=body["furni_id"], target_kind=body["target_kind"],
                             altitude_degrees=altitude, azimuth_degrees=azimuth,
                             **position, value=str(word)))
        return rows


class Budget:
    """Conservative rolling windows, retaining 20% headroom for other room work.

    Batched writes charge both each constituent and one request to the write
    bucket until the hotel establishes its exact accounting.
    """
    LIMITS = {"read": ((60, 240), (10, 48)), "profile_read": ((60, 96), (10, 16)), "write": ((60, 96), (10, 24)),
              "batch": ((60, 24),), "batch_write": ((60, 400),)}

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.events = {key: deque() for key in self.LIMITS}

    def acquire(self, costs):
        now = self.clock()
        wait = 0
        for key, cost in costs.items():
            events = self.events[key]
            while events and events[0][0] <= now-60:
                events.popleft()
            for window, limit in self.LIMITS[key]:
                if cost > limit:
                    raise ConfigurationError("A request exceeds the local rate budget")
                active = [(t, n) for t, n in events if t > now-window]
                total = sum(n for _, n in active) + cost
                for t, n in active:
                    if total <= limit:
                        break
                    total -= n
                    wait = max(wait, t + window - now + 0.001)
        if wait:
            raise RetryLater("local_rate_budget", wait)
        for key, cost in costs.items():
            self.events[key].append((now, cost))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward room credentials to a redirected host.


def retry_after(value, now=None):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return 1
    return max(1, delay) if math.isfinite(delay) else 1


class HabboClient:
    def __init__(self, config, read_key, write_key, opener=None, budget=None):
        if not read_key:
            raise ConfigurationError("Set HABBO_READ_KEY locally")
        self.config, self.read_key, self.write_key = config, read_key, write_key
        self.opener = opener or build_opener(NoRedirect())
        self.budget = budget or Budget()
        self.base = f'{config["hotel_origin"]}/api/public/rooms/{config["room_id"]}/variables'

    def request(self, method, path, payload=None, costs=None, resource="variables"):
        if resource not in ("variables", "variables_profile"):
            raise ConfigurationError("Unsupported API resource")
        self.budget.acquire(costs or {"read": 1})
        headers = {"X-Wired-Read-Key": self.read_key, "Accept": "application/json", "User-Agent": USER_AGENT}
        if method != "GET":
            if not self.write_key:
                raise ConfigurationError("Set HABBO_WRITE_KEY locally before a live test")
            headers.update({"X-Wired-Write-Key": self.write_key, "Content-Type": "application/json"})
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        base = self.base if resource == "variables" else self.base.removesuffix("/variables") + "/variables_profile"
        request = Request(base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise RetryLater(f"HTTP {exc.code}", retry_after(exc.headers.get("Retry-After"))) from None
            detail = ""
            try:
                error = json.loads(exc.read(8192)).get("error")
                if isinstance(error, dict):
                    error = error.get("code")
                if error in ("wired.variables.key_missing", "wired.variables.key_invalid",
                             "wired.variables.api_disabled", "wired.variables.room_not_found",
                             "wired.variables.variable_not_found", "wired.variables.invalid_target",
                             "room.not_found", "wired.variables.not_found", "wired.variables.entity_not_found"):
                    detail = f"; {error}"
            except (ValueError, AttributeError, TypeError):
                pass
            content_type = exc.headers.get_content_type() if hasattr(exc.headers, "get_content_type") else "unknown"
            raise ApiError(f"HTTP {exc.code}; response_type={content_type}{detail}") from None
        except (URLError, TimeoutError, OSError):
            raise RetryLater("network_error") from None
        except (ValueError, UnicodeError):
            raise ApiError("Invalid JSON response") from None

    def controls(self):
        values = {}
        for name in ("enabled_variable", "observer_variable"):
            result = self.request("GET", "/global/" + quote(self.config[name], safe=""))
            if not isinstance(result, dict) or "value" not in result:
                raise ApiError("Malformed control response")
            values[name] = result["value"]
        enabled = integer(values["enabled_variable"], 0, 1, "room enable control")
        observer = integer(values["observer_variable"], 1, 255, "room observer control")
        if str(observer) not in self.config["observers"]:
            raise ConfigurationError("Room selected an unknown observer; no frame sent")
        return bool(enabled), observer

    def preflight(self):
        enabled, observer = self.controls()
        for body in self.config["bodies"]:
            kind = body.get("target_kind", "furni")
            path = "/furni/" + quote(self.config["state_variable"], safe="") + f'/{kind}/{body["furni_id"]}'
            result = self.request("GET", path)
            if not isinstance(result, dict) or "value" not in result:
                raise ApiError("Malformed furni variable response")
            integer(result["value"], 0, 2**58-1, "initial state")
        return dict(enabled=enabled, observer=observer, bindings=len(self.config["bodies"]))

    def publish(self, rows):
        requests = [dict(op_id=f'{row.get("target_kind", "furni")}/{row["furni_id"]}', method="PATCH",
                         path=f'{row.get("target_kind", "furni")}/{row["furni_id"]}', body={"value": row["value"]})
                    for row in rows]
        n = len(requests)
        if not 1 <= n <= 10 or len({r["op_id"] for r in requests}) != n:
            raise ConfigurationError("Invalid prototype batch")
        result = self.request("POST", "/furni/" + quote(self.config["state_variable"], safe="") + "/batch",
                              {"requests": requests}, {"write": n+1, "batch": 1, "batch_write": n})
        statuses = {}
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise ApiError("Malformed batch response")
        for item in result["results"]:
            if not isinstance(item, dict) or item.get("op_id") in statuses:
                raise ApiError("Malformed or duplicate batch result")
            statuses[item.get("op_id")] = item.get("status")
        if set(statuses) != {r["op_id"] for r in requests}:
            raise ApiError("Missing or unexpected batch operation result")
        failures = [s for s in statuses.values() if type(s) is not int or not 200 <= s < 300]
        if failures:
            if all(type(s) is int and (s == 429 or 500 <= s < 600) for s in failures):
                raise RetryLater("batch_transient_failure")
            raise ApiError("Batch operation failed; check furni bindings and permissions")
        return n


def log(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def run_loop(config, ephemeris, client, duration):
    deadline = time.monotonic() + duration
    next_poll = next_write = 0
    enabled, observer = False, config["default_observer"]
    failures = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        try:
            if now >= next_poll:
                control = client.controls()
                if control != (enabled, observer):
                    log("controls", enabled=control[0], observer=control[1])
                    next_write = 0
                enabled, observer = control
                next_poll = time.monotonic() + config["poll_seconds"]
            if enabled and time.monotonic() >= next_write:
                rows = ephemeris.frame(observer, time.time())
                if time.monotonic() >= deadline:
                    break
                count = client.publish(rows)
                log("published", bodies=count, observer=observer,
                    sampled_at=unpack_state(int(rows[0]["value"]))["timestamp"])
                next_write = time.monotonic() + config["update_seconds"]
                failures = 0
            wake = min(next_poll, next_write) if enabled else next_poll
            time.sleep(max(0, min(wake-time.monotonic(), deadline-time.monotonic())))
        except RetryLater as exc:
            failures += 1
            delay = max(exc.delay, min(60, 2**min(failures-1, 6)))
            log("cooldown", reason=str(exc), seconds=delay)
            time.sleep(max(0, min(delay, deadline-time.monotonic())))
            # Re-read controls and recompute UTC; never replay missed/stale frames.
            next_poll = next_write = 0
    log("stopped", reason="duration_reached")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.example.json"))
    parser.add_argument("--ephemeris", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--probe", action="store_true", help="Read controls and bindings only; no writes")
    parser.add_argument("--duration", type=float, default=600, help="Live test seconds (max 1800)")
    parser.add_argument("--at", help="Preview only: ISO timestamp with explicit timezone")
    args = parser.parse_args()
    try:
        config = validate_config(json.loads(args.config.read_text()), args.live or args.probe)
        finite(args.duration, 1, 1800, "duration")
        if (args.live or args.probe) and args.at:
            raise ConfigurationError("--at is preview-only; live always uses current UTC")
        client = None
        if args.live or args.probe:
            client = HabboClient(config, os.environ.get("HABBO_READ_KEY"), os.environ.get("HABBO_WRITE_KEY"))
        if args.probe:
            log("probe_ok", **client.preflight())
            return 0
        if args.live and not client.write_key:
            raise ConfigurationError("Set HABBO_WRITE_KEY locally before a live test")
        if args.ephemeris is None:
            raise ConfigurationError("Provide --ephemeris pointing to a local DE440s file")
        ephemeris = Ephemeris(args.ephemeris, config)
        if not args.live:
            instant = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(timezone.utc)
            if instant.tzinfo is None:
                raise ConfigurationError("--at must specify a timezone")
            rows = ephemeris.frame(config["default_observer"], instant.timestamp())
            log("preview", utc=instant.astimezone(timezone.utc).isoformat(),
                observer=config["default_observer"], projection="east_component_and_altitude", bodies=rows)
            return 0
        # One writer per room/config directory. Keep the file to avoid inode races.
        import fcntl
        import hashlib
        identity = hashlib.sha256(f'{config["hotel_origin"]}/{config["room_id"]}'.encode()).hexdigest()[:24]
        lock_path = args.config.resolve().parent / f".astra-{identity}.lock"
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigurationError("Another writer is using this configuration directory") from None
            log("preflight_ok", **client.preflight())
            log("started", mode="live", room=config["room_id"], duration_seconds=args.duration)
            run_loop(config, ephemeris, client, args.duration)
        return 0
    except KeyboardInterrupt:
        log("stopped", reason="interrupted")
        return 130
    except (ConfigurationError, ApiError, KeyError, TypeError, ValueError, OSError, ImportError) as exc:
        # Do not print API response bodies, request headers or environment values.
        log("error", message=str(exc) if isinstance(exc, (ConfigurationError, ApiError)) else
            "Invalid configuration, missing dependency/file, or ephemeris date out of range; see README")
        return 1


if __name__ == "__main__":
    sys.exit(main())
