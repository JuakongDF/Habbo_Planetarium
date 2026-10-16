# Habbo Planetarium

Real-time astronomical room prototype for [Habbo.es room 126070270](https://www.habbo.es/room/126070270).

The first workflow is **Habbo connection test**, started manually from Actions. It uses only the existing `HABBO_READ_KEY` secret and does not change any room variable or move furniture. It makes one public GET without credentials to check API reachability, then four authenticated GETs: the supplied variable `test1` against two possible furniture bindings, plus the optional `astra_enabled` and `astra_observer` controls.

The supplied furniture ID `2147418112` equals the documented Builder's Club in-game offset (`0x7FFFFFFF - 0xFFFF`). If it is a BC in-game ID, its API binding is `furni-bc/0`. If it is an ordinary API furniture ID, its binding is `furni/2147418112`. The read-only workflow reports which binding exists; it does not guess a write destination. Missing controller globals do not prevent this initial binding check from succeeding.

Open **Actions → Habbo connection test → Run workflow**, and inspect the `connection_probe` JSON in the last step. `unique_binding: true` means exactly one candidate was found. `ready_for_controller` additionally requires the two room controls. HTTP errors are reported without keys or arbitrary response bodies.

The [controller setup guide](controller/README.md) describes the existing local Sun preview and the later bounded live test. The example runtime configuration deliberately retains placeholder room/furniture IDs, so it cannot accidentally write to the supplied room before the target and WIRED decoder are verified. No recurring schedule or log-commit job is installed at this stage.

Store API keys only under **Settings → Secrets and variables → Actions** as `HABBO_READ_KEY` and `HABBO_WRITE_KEY`. The write key is not passed to this connection-test workflow. No keys belong in source, configuration files, screenshots or chat.

Run local checks with:

```sh
python3 -m unittest discover -s controller -p 'test_*.py' -v
```

API reference: [Habbo's public WIRED Variables documentation](https://sandbox.habbo.com/api/public/api-docs/#/Wired%20Variables). ID conversion follows the supplied Habbo developer notes; actual hotel support is established by the connection test.
