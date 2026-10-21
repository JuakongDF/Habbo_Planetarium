# Habbo Planetarium

Real-time astronomical room prototype for [Habbo.es room 126070270](https://www.habbo.es/room/126070270).

## Confirmed in-room bridge

The Sun has permanent furniture variables `occupation` and `altitude`. The Echo boxes link those API-visible variables to internal `@occupation` and `@altitude`. A one-tile live test moved the Sun from `(36,18)` to `(37,17)` along the west-east diagonal; that historical test used a selector, but the current design writes the furniture variables directly and relies on WIRED detecting the changed furni. [Earlier motion test](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39349603144)

The controller targets the Sun at API address `furni-bc/0`. It writes `altitude` first and writes `occupation` only when the projected tile changes. No global selector is required. The direct-variable test run [successfully wrote occupation from `(37,17,0)` to `(38,16,0)`](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39354511068); the API accepted the write, and the resulting in-room movement is the visual confirmation step.

## Sun, Moon, and fixed-sky controller

The scheduled controller uses Skyfield and a local JPL DE440s ephemeris. It calculates apparent topocentric positions for the Sun and Moon from UTC and the observer latitude/longitude selected in the room. The usable diagonal has 54 cells from west `(1,53)` to east `(54,0)`. The two middle cells are `(27,27)` and `(28,26)`. Their visible heights span about `1000`–`6000`, with the horizon at `3500`; the Sun's command altitude subtracts its `700` intrinsic offset, and the Moon's subtracts its `800` offset. The `19:16` screen ratio and these offsets remain room-calibration values.

The Moon uses permanent furni variables `occupation`, `altitude`, and `state` on furni `523624626`. It maps illumination of at least 50% to state `0` and less than 50% to state `1`; crescent rotation mirrors between hemispheres. The 12 constellation furni and the Milky Way center are configured as fixed celestial anchors with RA/Dec coordinates. Their apparent topocentric positions follow Earth’s sidereal rotation for the selected observer, unlike the Sun and Moon, whose coordinates change over time. Before writing, the controller checks all 16 furniture profiles (Sun, Moon, Saturn, and 13 fixed-sky targets). Fixed-sky targets require `occupation`, `altitude`, and `state`; the controller sets their state to `0` and preserves their rotation. Noche Estrellada has a `+1000` intrinsic artwork offset, so its command altitude subtracts `1000`. Sagittarius follows that same galaxy anchor and tile, with a command altitude exactly `1000` above the background furni, instead of using an independent constellation coordinate. Constellation coordinates are initial anchor approximations because a constellation is an area/pattern rather than a single celestial point; calibrate those anchors and the 2×1 furni placement in-room. [IAU explanation of constellation boundaries](https://www.iau.org/IAU/Astronomy-FAQs/Constellations.aspx).

The scheduled job starts every six hours, runs for up to 5 hours 55 minutes, and stops publishing whenever global `astra_enabled` is zero. This leaves about five minutes for a handoff, though GitHub may delay scheduled starts. The manual **Scheduled Sun, Moon, and Constellation controller** workflow defaults to a read-only preview of all bodies; its bounded live mode obeys the same room enable switch. The current observer table contains Santiago and Greenwich examples. The controller uses the selected room observer; observer `1` is Santiago. Updates are spaced 15 seconds apart to leave API write-rate headroom for the extra furniture.

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
