# Legacy packed-state prototype

This guide describes the earlier `test1` packed-state design. The active room bridge uses permanent `occupation`/`altitude` furniture variables and direct WIRED change detection. See [the current bridge notes](NATIVE_CONTROL.md) and the scheduled controller in `scheduled_sun.py`. The old packed-state live writer is no longer exposed in GitHub Actions; this page is retained for its decoder and astronomy notes.

# First real-time room test

This prototype polls two room controls and sends current astronomical positions in a batch. It uses Skyfield + a local DE440s kernel, independently of the approximate browser simulator. No trajectory buffer or dummy memory furniture is required. Default execution is a local preview and performs no network calls. Only `--live` writes to Habbo; tests are bounded to 30 minutes maximum.

## What is ready

- Current UTC, apparent topocentric positions without atmospheric refraction.
- A 56-cell west–east diagonal, with independent native height.
- One Sun by default; up to ten configured body markers for early tests.
- Room controls polled every five seconds; batched position writes every ten seconds.
- Exact integer encoding with decimal-string API payloads.
- Per-operation response checks, conservative rolling rate budgets, HTTP 429/5xx backoff and Retry-After support.
- Missed frames are discarded. After a cooldown, controls are reread and positions recalculated at the current UTC time.
- A read-only `--probe` for controls and existing furniture bindings.

At the time this earlier prototype was written, no authenticated write or in-room movement had been tested. The newer Echo bridge and visible one-tile motion test are documented in [Native control](NATIVE_CONTROL.md).

## Install and preview

Run these commands from the `habbo-astronomical-room` directory:

```bash
python3 -m venv controller/.venv
controller/.venv/bin/python -m pip install -r controller/requirements.txt
mkdir -p controller/data
curl -fL https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440s.bsp -o controller/data/de440s.bsp
controller/.venv/bin/python controller/realtime.py --ephemeris controller/data/de440s.bsp
```

The kernel is approximately 32 MB. Downloads are an explicit setup step; the program does not download dependencies or astronomy data while running. It uses Skyfield's bundled time-scale data for this initial test. Dates outside the loaded kernel's coverage fail rather than extrapolate.

For a reproducible preview:

```bash
controller/.venv/bin/python controller/realtime.py \
  --ephemeris controller/data/de440s.bsp --at 2026-10-15T16:00:00Z
```

With the example Santiago observer, this produces Sun altitude approximately 64.383591°, azimuth 16.431419°, diagonal index 31, `(x,y)=(31,24)`, native height 1644, visible 1. Its payload is the decimal string `120264452997618463`. Those are computed reference values, not a measured Habbo result.

## Prepare one Sun marker and the controls

Copy `config.example.json` to `config.json` in this directory. The latter is gitignored. Set the actual HTTPS hotel origin, room ID and positive API furni ID. The zero IDs in the example deliberately prevent live execution. Choose the observer coordinates; the two sites in the example are demonstration presets, not a confirmed user preference. Never put keys in either JSON file.

Configure these room variables and allow the required API access:

| Scope/name | Initial value | Purpose |
|---|---:|---|
| Global `astra_enabled` | 0 | User-controlled master enable switch |
| Global `update_request` | 0 | Optional WIRED refresh signal; any positive value requests a batched resync |
| Global `astra_observer` | 1 | User-controlled site ID from the configured presets |
| Permanent furni `astra_state` | 0 | Assign to the Sun marker; updated by batch PATCH |

The process does not create definitions or bindings. PATCH intentionally fails if the state variable is absent, so first assign it through WIRED. Each body supports `target_kind: "furni"` or `"furni-bc"`. For BC items supply the already converted API ID, including zero; do not supply the offset in-game ID. Wall items are not supported by this movement prototype.

The API reads `astra_enabled` and `astra_observer`; it never overwrites those visitor controls. It also polls `update_request` every five seconds. When that value is positive and the room is enabled, the controller computes one current snapshot for the Sun, Moon, and all fixed-sky targets, then sends grouped batches for `state`, `altitude`, and `occupation` (at most ten furni per batch). It clears `update_request` back to zero after accepting the signal so WIRED can request another refresh. Repeated signals during a batch are coalesced into one fresh snapshot. The WIRED consumer must check the enable and observer controls locally to stop immediately when disabled and reject old-location packets. These global reads are not an atomic snapshot. Once a future occupancy detector is verified, it can control the enable switch. Empty-room detection is not implemented by this prototype.

During ordinary operation, the controller samples positions every 15 seconds and writes only variables whose values changed. The three control globals account for 36 reads per minute at the five-second poll interval; a refresh writes only when `update_request` is nonzero. The scheduled Actions workflow runs for 5 hours 55 minutes every six hours, so the monitor is not literally continuous: there is a short gap between scheduled jobs, plus any Actions startup delay.

## Decode the current-state packet in WIRED

This is a new **current-state** format for direct updates, not the 60-bit three-sample trajectory format in the planning document. Do not use the old decoder with `astra_state`.

```
value = index
      + 64 * height
      + 131072 * visible
      + 262144 * observer_id
      + 67108864 * sampled_unix_seconds
```

| Field | Decode with integer division and remainder |
|---|---|
| diagonal index | `value % 64` |
| native height | `(value // 64) % 2048` |
| above-horizon flag | `(value // 131072) % 2` |
| observer ID | `(value // 262144) % 256` |
| sampled UTC seconds | `value // 67108864` |

It uses 58 nonnegative bits and must retain exact integer precision, including in intermediate WIRED operations. Use integer division rather than floating-point division followed by rounding. Verify the known reference payload above before attempting movement. Python integers and the API's string representation preserve the exact value; room arithmetic precision still needs confirmation.

On a periodic WIRED trigger (do not assume external writes trigger Variable Changed):

1. Snapshot this marker's `astra_state` once into its execution Context.
2. Decode the fields into that same Context.
3. Reject unless enabled is 1, observer matches the global selection, index is within the configured diagonal and height is in the calibrated range.
4. Using a verified absolute UTC clock, accept only `0 <= now - sampled_time <= 30` seconds. Tune this expiry if the update interval changes. Mark expired packets stale; do not continue to describe the display as live.
5. For an accepted visible marker, set `x=index`, `y=55-index` together, then set height. Verify whether the actual movement effect applies these together or introduces a visible transient.
6. For a below-horizon marker, use the room's verified hide/parking mechanism, or retain it below the horizon during an explicitly labelled calibration test. The visibility bit is not itself a native hide command.

Movement, absolute time, hiding and per-marker Context isolation remain in-room capability tests. This program writes only the permanent application variable; it does not assume direct API writes to native position or altitude variables. There is no cross-body atomicity guarantee.

For the first projection, horizontal position is proportional to the local **east component** `cos(altitude)*sin(azimuth)`, with west at index 0 and east at 55. Height is `1000 + 10*altitude_degrees`. Thus northern/southern directions can overlap. This is a deliberate Sun-test projection, not a complete 360° panorama. Its height mapping is angular, not a spherical dome. Check the full diagonal, permitted native heights and orientation against the actual room before using its whole range.

## Read-only connection check, then a bounded live test

Set `HABBO_READ_KEY` and `HABBO_WRITE_KEY` in the launching process's private environment. Do not paste them into chat, commit them, put them into command arguments, or expose them to the browser simulator. The probe needs only the read key.

```bash
controller/.venv/bin/python controller/realtime.py --config controller/config.json --probe
```

Expected result: `probe_ok`, with enabled status, observer ID and one existing binding. This verifies reads and setup; it does not prove write access or motion. HTTP failures are reported without response bodies or credentials.

After the decoder and marker are prepared, enable the room control and run:

```bash
controller/.venv/bin/python controller/realtime.py \
  --config controller/config.json --ephemeris controller/data/de440s.bsp \
  --live --duration 600
```

The process checks controls and bindings before entering the loop. Stop it with Ctrl+C or let the ten-minute duration expire. Test these observations:

| Action | Expected result |
|---|---|
| Observe for several updates | New timestamp roughly every ten seconds; correct integer height/diagonal |
| Set enabled to 0 | WIRED stops immediately; external writes stop after the next control poll |
| Change observer to 2 and enable | A fresh observer-2 state appears after polling; old observer-1 state is rejected locally |
| Stop the process | WIRED marks the state stale after its age limit |
| Restart | Fresh current UTC positions, without replaying missed frames |

The ten-second interval produces six trajectory-state writes/minute for one body, or sixty for ten bodies, plus 24 simple control reads/minute at the five-second poll interval. One batch contains all configured bodies; batching reduces HTTP requests, not constituent write cost. The local limiter uses 80% of the supplied room limits, counts each constituent write plus the batch request against the ordinary write bucket conservatively, and separately tracks batch calls and batch-write cost. Other programs in the same room can still cause 429 responses.

Run only one writer for this room. A local lock prevents overlapping processes using the same configuration directory on macOS/Linux, but does not coordinate separate machines or directories. Unexpected permanent API errors stop the process; transient errors cool down and retry a fresh frame. Partial batches are not atomic: successful bodies may move while failed bodies retain their previous state. After a transient partial failure, the next frame updates all bodies with current values. This is acceptable for the first test but must be measured before larger displays.

## GitHub Actions and the next milestone

The five-minute cron minimum is a limit on scheduled starts. A job can execute this loop and make multiple API requests while running. However, a GitHub-hosted **job has a six-hour execution limit**; the 35-day workflow limit does not make a single hosted job perpetual. Self-hosted jobs have a five-day limit and still require your own running computer. [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)

A manually started, bounded development test is sufficient for this milestone. No workflow, repository, secrets or deployment has been created here. When using Actions for a test, keep the script duration below the job timeout and serialize room writers. Restart scheduling, runtime billing and the published restrictions on using Actions as an application backend still need to be considered before unattended production use. [GitHub Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)

Once direct movement is verified, measure height stepping and actual rate-limit headers/errors. If updates are too coarse or expensive, replace `astra_state` with a separately versioned short interpolation segment. The controller loop and control polling remain; this does not require an in-game memory bank. Smooth movement during an API cooldown requires the future segment to have been sent **before** the cooldown begins. It cannot be recovered from a single current-state packet after contact is lost.

## Automated checks

```bash
python3 -m unittest discover -s controller -p 'test_*.py' -v
```

Twelve tests cover exact large-integer packets, cardinal geometry, invalid configuration, read-only probing, request format, partial/missing batch results, retry headers, redirect handling, rolling rate budgets, and fresh-frame behavior after a cooldown and observer change. These tests use mocked HTTP; the astronomy preview above uses the actual local ephemeris. Neither substitutes for an authenticated room test.
