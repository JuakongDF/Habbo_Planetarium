# Habbo Planetarium

Real-time astronomical room prototype for [Habbo.es room 126070270](https://www.habbo.es/room/126070270).

The first workflow is **Habbo connection test**, started manually from Actions. It uses only the existing `HABBO_READ_KEY` secret and does not change any room variable or move furniture. It first checks a public endpoint and the WIRED route without credentials, then makes three authenticated GETs: the supplied variable `test1` on `furni-bc/0`, plus the optional `astra_enabled` and `astra_observer` controls. An unauthenticated WIRED request normally returns a JSON 403 error; this comparison helps distinguish API errors from upstream HTML rejection pages.

The user confirmed the marker is a Builder's Club item. Its in-game ID `2147418112` equals the documented BC offset (`0x7FFFFFFF - 0xFFFF`), so its API binding is `furni-bc/0`. Missing controller globals do not prevent this initial binding check from succeeding.

Open **Actions → Habbo connection test → Run workflow**, and inspect the `connection_probe` JSON in the last step. `unique_binding: true` means exactly one candidate was found. `ready_for_controller` additionally requires the two room controls. HTTP errors are reported without keys or arbitrary response bodies.

If the binding is unavailable, the workflow additionally reads the room's variable definitions and the specified furniture profile. `setup_diagnosis` distinguishes a missing room, furniture entity, definition, or assigned variable. It reports only counts and names matching `test1`, without publishing other variable names or values.

The [controller setup guide](controller/README.md) describes the existing local Sun preview and the later bounded live test. `controller/config.habbo-es.json` contains the confirmed room, variable and BC target. Its observer remains the example Santiago site until a location is selected. The generic example configuration retains placeholders. Live writes require the explicit `--live` option, write key, configured room controls and a prepared WIRED decoder. No recurring schedule or log-commit job is installed at this stage.

The HTTP client identifies itself as `HabboPlanetarium/0.1` with this repository URL. On 16 October 2026, the default Python user agent received HTML HTTP 463 responses on WIRED routes; the explicit application identifier allowed the unauthenticated request to reach the API and receive its normal missing-key JSON response.

Store API keys only under **Settings → Secrets and variables → Actions** as `HABBO_READ_KEY` and `HABBO_WRITE_KEY`. The write key is not passed to this connection-test workflow. No keys belong in source, configuration files, screenshots or chat.

Run local checks with:

```sh
python3 -m unittest discover -s controller -p 'test_*.py' -v
```

API reference: [Habbo's public WIRED Variables documentation](https://sandbox.habbo.com/api/public/api-docs/#/Wired%20Variables). ID conversion follows the supplied Habbo developer notes; actual hotel support is established by the connection test.
