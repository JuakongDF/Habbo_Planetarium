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

`scheduled_sun.py` calculates the current apparent topocentric Sun position from UTC and the selected observer's latitude/longitude. It projects onto the 54-cell usable diagonal from west `(1,53)` to east `(54,0)` and maps altitude onto a configurable screen-shaped ellipse. The visual horizon is at `3500`, and the unoffset altitude range is about `1000`–`6000`. Per-body intrinsic offsets are subtracted from API altitude writes. Geometry is in `controller/config.habbo-es.json` under `projection` and `motion_bridge`.

Each changed frame updates `altitude` when its value changes and updates `occupation` when the tile changes; it does not write an unchanged variable. The controller will not write while global `astra_enabled` is zero, and it reads the selected `astra_observer` from the room. The current sample locations are Santiago and Greenwich; add or replace sites in the config to match the real observer. Time zones are only needed to label local time; the Sun's position uses UTC and longitude.

## Moon position and phase

The controller also targets the Moon at API address `furni/523624626`. That item must have permanent furni variables `occupation`, `altitude`, and `state`, echoed to the matching internal WIRED variables `@occupation`, `@altitude`, and `@state`. It uses the selected observer coordinates (Santiago is observer `1`) for both the apparent position and phase. At least 50% illumination maps to state `0`; below 50% maps to state `1`. The rotation is `0` for waxing in the Southern Hemisphere and `1` for waning; the Northern Hemisphere reverses those values. Rotation is packed into `occupation`'s low byte. The full/gibbous versus crescent threshold is configurable. The controller validates both furniture profiles before writing either body, so missing Moon bindings stop the run without partially updating the Sun.

The visual horizon is centered at native altitude `3500`; the unoffset projection spans approximately `1000`–`6000`. Since the Sun and Moon furni have intrinsic vertical offsets of `700` and `800`, respectively, the API commands subtract those values and span `300`–`5300` for the Sun and `200`–`5200` for the Moon. These offsets are configured per body; other planets default to zero, while constellation offsets can be set individually when added. The 54 diagonal cells have two middle tiles: `(27,27)` and `(28,26)`; exact center rounding selects `(28,26)`.

The projection follows the Sun through the entire ellipse: negative altitude maps below the horizon and continues to the nadir, rather than holding the last position. The estimated range is about `2000` at nadir to `7000` at zenith. Native height scale still needs in-room calibration.

The workflow starts on a six-hour schedule and runs for up to 5 hours 55 minutes per hosted job, leaving about five minutes for a handoff. Scheduled starts can still be delayed. It also has a manual one-frame preview and a bounded live mode. The monthly heartbeat writes a small timestamp log on the `automation-heartbeat` branch to keep scheduled workflows from going inactive.
