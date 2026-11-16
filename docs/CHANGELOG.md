# 0.3.1-uitest.1

- Always render Calibration Settings first; show explicit loading/read/backup errors.
- Remove `current && initial.values && windows` as the all-or-nothing controls gate.
- Derive finite per-trigger viewports without depending on optional backend `windows` or Apply capability.
- Place native SliderField directly inside its own PanelSectionRow; keep marker/text rows separate.
- Check actual slider counts in compiled render tests, backend-state cases and source-map/source equality.
- Add a real-React/Chromium browser check with explicit DOM stand-ins; native SteamUI remains unverified.
- Preserve HID reader, protocol, Trigger Test component and calibration backend byte-for-byte against 0.3.0.

# 0.3.0-hwtest.1

- Continue the 0.2 project; retain actual calibration reading, immutable backup, atomic storage, validation and serialization.
- Fix sliders being disabled by the read-only write gate; selection/preset persistence performs no hardware write.
- Clearly separate Current, Selected and Default Calibration.
- Add controlled explicitly confirmed manual Apply/Restore using the observed original protocol, an independent one-shot helper, inherited locks, durable intent/result and partial-failure recovery.
- Add Last Known Good confirmation after verified read-back and explicit behavior attestation.
- Add on-demand real HID Trigger Test, report-scale bars, extrema, five-second informational idle measurements, reset and close/lease cleanup.
- Add sanitized shareable diagnostics ZIP, persistent bounded event log and startup-failure export.
- Remove automatic-profile controls/RPC from production. No Boot/Resume services or Steam restart are added.
- Keep all user-facing plugin text in English. Hardware write/input compatibility and native control behavior remain device acceptance tests.
