# Third-party notices and provenance

0.3.0-hwtest.1 continues the delivered 0.2.0-validation.1 application rather than
forking an upstream project. The retained 0.2 source archive SHA-256 is
`5f6e92975617152905500971aa7c9db3e87b619eccacf604a2c1dcaa11b5ff67`.

The user supplied `Decky_Trigger_Calibration_v0.1.0_SOURCE.zip`, SHA-256
`2d499f9d9e912b20604954088b0a3e26faba1b86cdbd8f4f55398ac075a895b3`.
The implementation continues its Python/React project structure and replaces the
interactive executable adapter. Its original notice is retained in LICENSE and
`licenses/PROTOTYPE-v0.1.0.txt`.

## Calibration research references

- [kasandravtv / kasvtv's original tool](https://github.com/kasandravtv/steam_deck_overwrite_trigger_cal), commit `4ef252c4bef7b0bdea62d107d5c7420a2c79319f`.
- [Skaifer's wrapper](https://github.com/Skaifer/steamdeck_overwrite_trigger_cal_wrapper), commit `6122fa558a9794503285b62437b3f9342591672a`, release v1.0.0. Its MIT notice is retained in `licenses/SKAIFER-MIT.txt` for attribution; its script is not shipped or executed.
- [Official Decky template](https://github.com/SteamDeckHomebrew/decky-plugin-template), commit `90d0780e882a17f5714fc6de044c645f22608290`. BSD-3-Clause notice retained in `licenses/DECKY-TEMPLATE-BSD-3-Clause.txt`.
- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader), commit `ffd103b9c1a4ce5bf305e7c0998eda6e05457dad`, used to verify settings constants, root flag, RPC and ZIP installation.
- [Lapin/deckbind](https://github.com/Lapin/deckbind/tree/92fa2e7474b9953e20674e6846b7994bc9551da0), report layout/live-input research reference. Its MIT notice is retained in `licenses/DECKBIND-MIT.txt`; its SSH/server/browser architecture is not installed or executed.
- Linux `hid-steam` and SDL Steam Deck HIDAPI are primary documentation references for report fields/axis scale. Their C implementations are not bundled or copied into this application.

The original repository does not include a standalone LICENSE in the inspected
tree. Its README permits alterations, but that does not establish permission to
redistribute the Valve libraries extracted from `/usr/bin/trigger_cal`. Neither
that binary, the supplied overwrite executable, nor its extracted/decompiled
libraries are included in either delivery archive. The native reader is newly
written against the observed feature-report format and Linux hidraw API.

The supplied Linux reference executable was inspected statically only. SHA-256:
`8b39060beaa370897f7c18b02cbe8a01d2de7dbbc93c7e278fa6efd3f2999b29`.

## Runtime/frontend libraries

- `@decky/api` 1.1.3, SteamDeckHomebrew Team, LGPL-2.1. Unmodified portions are bundled by Rollup into `dist/index.js`. License: `licenses/DECKY-API-LGPL-2.1.txt`; corresponding installed package source: `licenses/decky-api-source/`; package metadata: `licenses/decky-api-package.json`. [Upstream source](https://github.com/SteamDeckHomebrew/loader-api).
- `@decky/ui` 4.16.5, LGPL-2.1. Referenced through Decky's `DFL` external; its implementation is supplied by Decky, not embedded in this plugin bundle. Notice: `licenses/DECKY-UI-LGPL-2.1.txt`. [Upstream source](https://github.com/SteamDeckHomebrew/decky-frontend-lib).
- React/ReactDOM are supplied by Steam/Decky as external globals, not redistributed here.
- `tslib` 2.9.1, Microsoft Corporation; applicable notice retained in `licenses/TSLIB.txt`.
- `@decky/rollup` 1.0.2, BSD-3-Clause; a build tool, not a runtime dependency.

No modifications were made to the bundled Decky API source. Full plugin source,
Rollup configuration and pinned dependencies are supplied to permit rebuilding
and substituting/relinking the library. No restriction on reverse engineering
for debugging modifications to LGPL components is imposed by this delivery.
The ordinary warranty disclaimer remains applicable.
