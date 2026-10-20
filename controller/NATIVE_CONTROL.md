# Permanent WIRED command bridge

The room exposes two ordinary permanent furniture variables on the Sun, `occupation` and `altitude`, plus global `update_furni_id`. The Echo boxes connect those values to the internal `@occupation` and `@altitude` variables. The Web API reads and writes the permanent variables; the Echo add-ons and WIRED selector perform the in-room movement.

The Sun's in-game BC ID is `2147418112`; the API target is `furni-bc/0`. The global selector carries the in-game ID expected by WIRED.

## Confirmed movement test

The [one-tile API test](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349603144) read `(36,18,0)`, wrote the next flat-diagonal occupation `(37,17,0)`, kept altitude at `0`, and signaled the Sun ID. The user saw the Sun move one tile east along the diagonal.

Occupation follows the user's encoding:

```
occupation = 65536*x + 256*y + rotation
```

So a flat step `(x+1,y-1)` changes occupation by `65280` and preserves rotation. The test established that the API command variables, selector, Echo boxes, and movement chain work together for this horizontal move.

The API test did not observe WIRED resetting `update_furni_id` to zero. The test runner cleared the selector after confirming it still held this test's Sun ID. The scheduled controller waits briefly for WIRED to clear the selector, then clears it itself if needed. The follow-up [cleanup run](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349755783) verified zero. The test left the permanent occupation command at `(37,17,0)`; the user confirmed the physical motion.

A later room probe found `astra_enabled=0`, but the global `update_furni_id` definition returned 404 after cleanup. The controller checks the enable switch before requiring motion variables, so scheduled runs remain harmless while disabled. Before enabling motion, confirm that the global selector is visible again and defined as a permanent writable variable.

Direct reads of `@occupation` and `@altitude` still return `wired.variables.not_found`. Their Echo values are not separate API bindings; they are the WIRED bridge into the internal furni state.

## Scheduled Sun projection

`scheduled_sun.py` calculates the current apparent topocentric Sun position from UTC and the selected observer's latitude/longitude. It projects the eastward component onto the 56-cell west-east diagonal and maps altitude onto a configurable screen-shaped ellipse. The provisional screen ratio is `19:16`; following the user's estimate, the active projection uses a horizon height of `4500` and a vertical radius of about `2500`, giving nadir/zenith values near `2000`/`7000`. These are calibration estimates, not yet visually confirmed. They are in `controller/config.habbo-es.json` under `motion_bridge`.

Each changed frame updates the permanent `occupation` and `altitude` values, then signals `update_furni_id`. The controller will not write while global `astra_enabled` is zero, and it reads the selected `astra_observer` from the room. The current sample locations are Santiago and Greenwich; add or replace sites in the config to match the real observer. Time zones are only needed to label local time; the Sun's position uses UTC and longitude.

Below the horizon, the first version holds the last position. It does not yet hide or park the Sun. Native height scale and night behavior need in-room calibration before leaving the room enabled for a full day.

The workflow starts on a six-hour schedule and runs for at most 5 hours 45 minutes per hosted job. It also has a manual one-frame preview and a bounded live mode. Leave `astra_enabled=0` until the projected height and location have been reviewed. The monthly heartbeat writes a small timestamp log on the `automation-heartbeat` branch to keep scheduled workflows from going inactive.
