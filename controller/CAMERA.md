# Shared directional sky views

The controller supports eight horizontal directions and a north-up zenith map.
It uses the same celestial coordinates, room diagonal, intrinsic artwork offsets,
Moon phase, constellation state 0, and linked Sagittarius/galaxy anchor as the
existing scheduler. One view is shared by the entire room: a selected viewer's
rotation, buttons, telescopes, or a bed can drive the same global control.

## Room bindings

Create a user-created, permanent, API-readable global variable `astra_view`.
WIRED should write the chosen view to it. It is a semantic compass code, **not
an assumed copy of Habbo's avatar rotation number**; calibrate that mapping at
the viewing platform.

| Value | View | Left horizon | Right horizon |
| --- | --- | --- | --- |
| -1 | Original whole-sky overlay | West | East |
| 0 | North | West | East |
| 1 | Northeast | Northwest | Southeast |
| 2 | East | North | South |
| 3 | Southeast | Northeast | Southwest |
| 4 | South | East | West |
| 5 | Southwest | Southeast | Northwest |
| 6 | West | South | North |
| 7 | Northwest | Southwest | Northeast |
| 8 | Zenith | West | East |

For every managed furni (Sun, Moon, Saturn, 12 constellations, and Noche Estrellada),
add a permanent, API-writable furni variable `visible`, initialized to `1`.
Wire `visible = 0` to hide the **selected furni**, and `visible = 1` to show it.
This is a user-defined command; the code makes no assumption that `@visible` or
another writable internal visibility variable exists. The in-room hide/show
mechanism must be supplied and tested. Keep the existing occupation, altitude,
and state wiring. State 0 is artwork selection, not invisibility.

Only the active viewer/platform should write `astra_view`; allowing every guest
to drive it will make the whole room alternate between their directions.
The API does not directly follow avatar orientation or bed posture: WIRED must
translate those events to the global variable. On leaving the bed, restore the
selected horizontal mode. On leaving the platform, either hold the last view
or write `-1`, according to the room's desired behavior.

## Geometry

Azimuth A runs clockwise from north; altitude h is positive above the horizon.
For a horizontal mode with heading H = 45 degrees times the view code:

```
screen_right = cos(h) * sin(A - H)
screen_up    = sin(h)
front_depth  = cos(h) * cos(A - H)
visible      = front_depth >= 0
```

This includes the forward hemisphere above **and below** the horizon. The
boundaries (including zenith and nadir) remain visible; a small numerical
tolerance prevents floating-point roundoff from hiding exact boundary points.
The opposite hemisphere no longer overlays it.

For zenith mode, screen_right = cos(h)*sin(A), screen_up = cos(h)*cos(A), and
visible = sin(h) >= 0. Zenith is at the display center, north at the top,
south at the bottom, east at the right, and west at the left. The horizon is
the ellipse boundary; below-horizon objects are hidden. This is a north-up,
east-right sky **map** convention. Furni artwork remains unwarped and retains
its configured/stored rotation; this is not a full perspective renderer.

Both projections stay on the existing flat floor diagonal. Screen vertical
position still uses furni altitude. Center is visible height 3500, with a
roughly 2500-unit vertical radius; each sprite's intrinsic offset is subtracted.
Sagittarius and Noche Estrellada share position and visibility, and Sagittarius's
command altitude stays 1000 units above the background's command altitude.

## Updates and limits

The directional controller targets a control poll every 5 seconds. A changed
view, observer, or re-enable starts a full refresh automatically; no
`update_request` signal is necessary for turning. That signal still forces a
full refresh when requested. Ordinary sky motion writes only changed values.

Refreshes hide back-facing objects first, update state/altitude/occupation,
then reveal front-facing objects. Each API batch contains at most ten furni
for one variable. They are **not atomic**. For the current sixteen targets, a
full refresh has 62 variable operations in eight batches (70 local write-budget
units). With the current 24-units-per-10-seconds and 96-per-minute budget, allow
tens of seconds for a complete change; recent traffic or server throttling can
extend that. Furniture already visible can move at different times during a turn.

New view requests replace unfinished older batches rather than forming a queue
of intermediate directions. Polling continues during write cooldowns. Partial
batch failures invalidate affected cached values and retry the batch before
revealing arriving furniture. API acceptance confirms the permanent variables,
not physical placement. Corner limitations for 2x1 furni still apply.

## Activation

The new camera is opt-in while the room's visibility wiring is being prepared.
The checked-in configuration has `camera.enabled = false`, so existing scheduled
runs retain the original projection. Merely creating the variables does not turn
the camera on.

1. Finish and verify the room bindings above.
2. Dispatch the scheduled-controller workflow with `mode=preview`, `camera=true`
   to check **all** bindings without writing. `preview_view` can override the
   projection for that preview without changing the room's global value.
3. Run a short live test with `camera=true`. All required profiles are checked
   before the first write; a missing `visible` binding stops this mode.
4. Once the in-room behavior is confirmed, set `camera.enabled=true` in
   `config.habbo-es.json` and restart the live workflow. Six-hour scheduled runs
   then use the directional controller too.

For geometry-only previews before visibility wiring is ready, use `camera=false`
and choose `preview_view` from `0` through `8`. Existing bindings are checked,
positions and intended visibility are logged, and no writes occur.

To return to the old overlay after the camera has hidden objects, first set
`astra_view=-1` **while the camera controller is still active** and let its full
refresh finish. That restores all `visible` values to 1. Disabling the camera
configuration alone does not unhide objects.
