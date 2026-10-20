# Habbo Planetarium

Real-time astronomical room prototype for [Habbo.es room 126070270](https://www.habbo.es/room/126070270).

## Confirmed in-room bridge

The Sun has permanent furniture variables `occupation` and `altitude`. The Echo boxes link those API-visible variables to internal `@occupation` and `@altitude`. A one-tile live test moved the Sun from `(36,18)` to `(37,17)` along the west-east diagonal; that historical test used a selector, but the current design writes the furniture variables directly and relies on WIRED detecting the changed furni. [Earlier motion test](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349603144)

The controller targets the Sun at API address `furni-bc/0`. It writes `altitude` first and writes `occupation` only when the projected tile changes. No global selector is required. The direct-variable test run [successfully wrote occupation from `(37,17,0)` to `(38,16,0)`](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39354511068); the API accepted the write, and the resulting in-room movement is the visual confirmation step.

## Sun and Moon controller

The scheduled controller uses Skyfield and a local JPL DE440s ephemeris. It calculates apparent topocentric positions for the Sun and Moon from UTC and the observer latitude/longitude selected in the room. Their east components map to the 56-cell west-east diagonal; altitude maps to the same screen-shaped ellipse with a provisional `19:16` ratio, horizon height `4500`, and estimated nadir/zenith values near `2000`/`7000`.

The Moon uses the permanent furni variables `occupation`, `altitude`, and `state`. Assign them to furni `523624626` and Echo them to `@occupation`, `@altitude`, and `@state` in WIRED. Before any live writes, the controller checks that both the Sun and Moon have their required variables. It maps illumination of at least 50% to Moon state `0` and less than 50% to state `1`; the crescent rotation is `0` for waxing and `1` for waning in the Southern Hemisphere, reversed in the Northern Hemisphere. States `2` and `3` are not used. The 50% boundary is configurable in `controller/config.habbo-es.json`.

The scheduled job starts every six hours, runs for up to 5 hours 55 minutes, and stops publishing whenever global `astra_enabled` is zero. This leaves about five minutes for a handoff, though GitHub may delay scheduled starts. The manual **Scheduled Sun and Moon controller** workflow defaults to a read-only preview of both bodies; its bounded live mode obeys the same room enable switch. The current observer table contains Santiago and Greenwich examples. The controller uses the selected room observer; observer `1` is Santiago.

The projection follows the full daily arc, including the below-horizon half down to the estimated nadir; the horizon is not a visibility cutoff. The height scale and direct-change trigger path still need in-room verification. GitHub-hosted jobs have a six-hour limit, and scheduled starts can be delayed or dropped during high load, so this is a prototype scheduler rather than a guaranteed always-on service. [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits), [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

A monthly heartbeat writes a timestamp to the `automation-heartbeat` branch to keep scheduled workflows from going inactive after prolonged repository inactivity.

## Credentials

Store `HABBO_READ_KEY` and `HABBO_WRITE_KEY` only under **Settings → Secrets and variables → Actions**. The scheduled live step reads both from GitHub Secrets; neither key belongs in source, configuration files, screenshots, or chat.

## Existing prototype tools

- **One-tile WIRED motion test:** changes only permanent furniture variable `occupation`; it tests direct change detection without a global selector.
- **Inspect Sun echo variables:** read-only inspection of room definitions and the Sun profile.
- **Real-time Sun test:** legacy packed-state preview for `test1`; its live mode is not part of the new occupation/altitude scheduler.
- **Connection test:** read-only API access and variable binding diagnostics.

For the current bridge and calibration notes, see [controller/NATIVE_CONTROL.md](controller/NATIVE_CONTROL.md). The older packed-state prototype remains in [controller/README.md](controller/README.md) for reference.
