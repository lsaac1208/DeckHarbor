# Steam-native layout review

Run only while Steam and Decky are available on localhost:8080. This review uses actual source components, DFL controls and Steam CSS inside an isolated iframe. All plugin API calls, file picker results and navigation are fixtures; no actual downloads, games or account credentials are used.

1. `node tools/ui-review/build-preview.cjs`
2. `node tools/ui-review/cdp.mjs /tmp/nfd-preview.js`
3. `node tools/ui-review/cdp.mjs tools/ui-review/check-layout.js`
4. `node tools/ui-review/cdp.mjs tools/ui-review/check-input.js`

Run checks sequentially: they share one preview root. The runtime adapter contains QueryClient module IDs observed on the tested Steam build; these may need rediscovery after Steam updates. They are development fixtures, never packaged in the plugin.

`check-layout.js` checks dimensions, card clipping, button overlap and error recovery at Deck resolutions and QAM widths. Horizontal scroll shelves intentionally allow offscreen cards. `check-input.js` dispatches synthetic Steam events; it does not verify physical controller navigation.

SharedJSContext is hidden in desktop mode: blank screenshots from it are not visual verification. Complete game-mode visual and physical-controller review separately.

Cleanup: evaluate `window.__nfdPreviewRoot?.unmount(); document.getElementById('nfd-layout-preview')?.remove()` via CDP.


## Documentation screenshots

`node tools/ui-review/build-preview.cjs --docs` uses the real page components with public catalog entries and synthetic library/emulator state. Load the generated preview as above, then run:

```sh
node tools/ui-review/export-docs.mjs /tmp/deckharbor-doc-preview
```

Render the exported HTML in an isolated Chromium profile with the viewport sizes from pages.json. These are DOM snapshots re-rendered outside Steam, not game-mode captures. The preview adapter replaces navigation/backend actions and renders react-icons with a generic placeholder. Never commit exported Steam CSS/HTML or a personal browser profile. Only the final PNGs and this explanation belong in documentation.

`check-install.js` checks cloud-game install/cancel/more clicks against fixture RPC, duplicate submissions, errors and retries. It invokes the bound gamepad callback directly because this isolated desktop tree does not reproduce physical controller dispatch. It never downloads files.
