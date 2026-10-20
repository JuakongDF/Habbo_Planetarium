# Permanent WIRED command bridge

The room exposes two ordinary permanent furniture variables on the Sun, `occupation` and `altitude`. The Echo boxes connect those values to the internal `@occupation` and `@altitude` variables. The Web API reads and writes the permanent variables; WIRED detects which furni variable changed and applies it to the internal furni state. No global selector is part of the current design.

The Sun's in-game BC ID is `2147418112`; the API target is `furni-bc/0`.

## Confirmed movement test

The historical [one-tile API test](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349603144) read `(36,18,0)`, wrote the next flat-diagonal occupation `(37,17,0)`, kept altitude at `0`, and signaled the Sun ID. The user saw the Sun move one tile east along the diagonal. The updated workflow tests whether writing `occupation` alone now triggers the same movement.

Occupation follows the user's encoding:

```
occupation = 65536*x + 256*y + rotation
```

So a flat step `(x+1,y-1)` changes occupation by `65280` and preserves rotation. The earlier test established that this command moves the Sun when the selector chain is used. The current direct-variable design is being checked separately by the updated one-tile workflow.

The earlier selector-based test left permanent occupation at `(37,17,0)`; the user confirmed that physical motion. The new [direct-variable test run](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39354511068) successfully wrote the next tile `(38,16,0)` without any selector read or write. The API accepted the direct write; visual confirmation of the new WIRED event path is still useful.

Direct reads of `@occupation` and `@altitude` still return `wired.variables.not_found`. Their Echo values are not separate API bindings; they are the WIRED bridge into the internal furni state.

## Scheduled Sun projection

`scheduled_sun.py` calculates the current apparent topocentric Sun position from UTC and the selected observer's latitude/longitude. It projects the eastward component onto the 56-cell west-east diagonal and maps altitude onto a configurable screen-shaped ellipse. The provisional screen ratio is `19:16`; following the user's estimate, the active projection uses a horizon height of `4500` and a vertical radius of about `2500`, giving nadir/zenith values near `2000`/`7000`. These are calibration estimates, not yet visually confirmed. They are in `controller/config.habbo-es.json` under `motion_bridge`.

Each changed frame updates `altitude` when its value changes and updates `occupation` when the tile changes; it does not write an unchanged variable. The controller will not write while global `astra_enabled` is zero, and it reads the selected `astra_observer` from the room. The current sample locations are Santiago and Greenwich; add or replace sites in the config to match the real observer. Time zones are only needed to label local time; the Sun's position uses UTC and longitude.

Below the horizon, the first version holds the last position. It does not yet hide or park the Sun. Native height scale and night behavior need in-room calibration before leaving the room enabled for a full day.

The workflow starts on a six-hour schedule and runs for up to 5 hours 55 minutes per hosted job, leaving about five minutes for a handoff. Scheduled starts can still be delayed. It also has a manual one-frame preview and a bounded live mode. The monthly heartbeat writes a small timestamp log on the `automation-heartbeat` branch to keep scheduled workflows from going inactive.
