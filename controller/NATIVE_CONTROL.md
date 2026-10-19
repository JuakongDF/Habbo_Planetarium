# Native occupation and altitude: next movement test

The user supplied and tested the internal encoding on 19 October 2026:

```
@occupation = 65536*x + 256*y + rotation
```

The existing packed `test1` format is a different application protocol. Never copy its 58-bit value directly into `@occupation`.

For the confirmed Sun, the in-game BC ID is 2147418112 and the API target remains `furni-bc/0`. An ID echo is unnecessary for selecting this known API entity. That does not establish that native occupation or altitude is readable/writable through the API.

## Diagonal motion

The flat west–east line holds `x+y` constant. A step `(x+1,y-1)` with unchanged rotation changes occupation by `65536-256 = 65280`. For the user's example:

| Pose | Occupation |
|---|---:|
| `(14,13,1)` | 920833 |
| X-only step `(15,13,1)` | 986369 |
| Flat diagonal step `(15,12,1)` | 986113 |

For the full 56-cell line `(i,55-i)`, occupation is `65280*i + 14080 + rotation`. Preserve the current rotation; do not assume the example's rotation of 1 applies to the actual marker. A first relative step should preserve the marker's existing `x+y`, without jumping to the full room diagonal. Check the intended neighbouring tile is usable before moving. Native altitude stays unchanged during this first horizontal test.

## Observed API result

The read-only [inspection run](https://github.com/JuakongDF/Habbo_Planetarium/actions/runs/39291364351) found one variable in the Sun's profile and no occupation/altitude names in room definitions or the profile. Direct GETs for `@occupation` and `@altitude` returned `wired.variables.not_found`. No room values were changed. This establishes only that those names are not currently exposed on this binding; it does not rule out differently named aliases or an activation/refresh issue.

The exact names/configuration of the two Echo boxes are the remaining input. If they expose native variables only to WIRED, the straightforward bridge is two separately named permanent furni variables, for example `target_occupation` and `target_altitude`, assigned to the Sun. WIRED would copy those commands into the corresponding native values. This removes the packed position/height decoder while retaining the API's ordinary permanent-variable interface. Do not make this substitution until the copy effects and target selection are verified.

## Next test sequence

1. Establish readable aliases or the permanent-variable bridge, and read the real initial occupation and altitude.
2. Test one valid neighbouring diagonal cell at unchanged altitude and rotation; read back the resulting state.
3. Restore the recorded occupation, provided the marker still has the test state. Do not overwrite a later manual change.
4. Test a separately calibrated small altitude change and restore it.
5. Only then connect repeated astronomical updates. Verify native height units and select the observer site; the current example is Santiago.

A successful API update proves a stored value was accepted. Native readback and the visible room result are still needed to confirm actual movement. A command-variable readback alone does not establish that the WIRED copy ran.
