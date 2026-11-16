# Optional browser UI check

This uses real React/Chromium and the compiled plugin, but substitutes ordinary DOM elements for Decky widgets and synthetic RPC data. It is NOT native SteamUI/hardware/gamepad acceptance.

From the project root, build the plugin first. Then:

```sh
cd tools/browser-preview
pnpm install
pnpm exec playwright install chromium
node ../../node_modules/rollup/dist/bin/rollup -c rollup.config.mjs
node check.mjs
```

Optional `PLAYWRIGHT_CHROMIUM_EXECUTABLE` selects an existing browser; `UI_PREVIEW_OUTPUT` selects the output folder. Outputs default to `.preview-output/` and are excluded from packaging. No controller is accessed. Parent project build dependencies must be installed.

Optional `UI_BASELINE_BUNDLE` points to the original 0.3.0 `dist/index.js` extracted from its ZIP. If available, the check first reproduces its missing-windows render gate with the same synthetic data.
