# Habbo Planetarium

Real-time astronomical room prototype for [Habbo.es room 126070270](https://www.habbo.es/room/126070270).

## Confirmed in-room bridge

The Sun has permanent furniture variables `occupation` and `altitude`, and a global selector variable `update_furni_id`. The Echo boxes link those API-visible variables to the internal `@occupation` and `@altitude` values. A one-tile live test moved the Sun from `(36,18)` to `(37,17)` along the west-east diagonal; the user's visual check confirmed the movement. [Motion test run](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349603144)

The controller writes `occupation`, writes `altitude`, and sends the Sun's in-game BC ID (`2147418112`) through `update_furni_id` last. The API address for that same item is `furni-bc/0`. The test showed that WIRED did not reset the global selector, so the controller now clears it after a short settle delay when WIRED leaves it set. It refuses to overlap updates while the selector is nonzero.

## Sun controller

The scheduled controller uses Skyfield and a local JPL DE440s ephemeris. It calculates the Sun's apparent topocentric position from UTC and the observer latitude/longitude selected in the room. The east component maps to the 56-cell west-east diagonal. Altitude maps to a screen-shaped ellipse with a provisional `19:16` ratio, horizon height `4500`, and estimated nadir/zenith values near `2000`/`7000`; calibrate them in-room before relying on the mapping.

The scheduled job starts every six hours, runs for at most 5 hours 45 minutes, and stops publishing whenever global `astra_enabled` is zero. The last room check found `astra_enabled=0`, so the scheduler currently starts safely disabled. That check also found the `update_furni_id` global missing from the API after selector cleanup; confirm that this permanent writable global is available before enabling motion. The manual **Scheduled Sun controller** workflow defaults to a read-only preview; its bounded live mode obeys the same room enable switch. The current observer table contains Santiago and Greenwich examples. Update it with the intended latitude/longitude before relying on the projection for a specific location.

At night, this first version holds the last position; it does not yet hide or park the Sun. Leave `astra_enabled=0` until the altitude scale and night behavior have been calibrated. GitHub-hosted jobs have a six-hour limit, and scheduled starts can be delayed or dropped during high load, so this is a prototype scheduler rather than a guaranteed always-on service. [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits), [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

A monthly heartbeat writes a timestamp to the `automation-heartbeat` branch to keep scheduled workflows from going inactive after prolonged repository inactivity.

## Credentials

Store `HABBO_READ_KEY` and `HABBO_WRITE_KEY` only under **Settings → Secrets and variables → Actions**. The scheduled live step reads both from GitHub Secrets; neither key belongs in source, configuration files, screenshots, or chat.

## Existing prototype tools

- **One-tile WIRED motion test:** verifies the permanent variable bridge; its selector cleanup mode clears only the Sun ID used by that test.
- **Inspect Sun echo variables:** read-only inspection of room definitions and the Sun profile.
- **Real-time Sun test:** legacy packed-state preview for `test1`; its live mode is not part of the new occupation/altitude scheduler.
- **Connection test:** read-only API access and variable binding diagnostics.

For the current bridge and calibration notes, see [controller/NATIVE_CONTROL.md](controller/NATIVE_CONTROL.md). The older packed-state prototype remains in [controller/README.md](controller/README.md) for reference.
