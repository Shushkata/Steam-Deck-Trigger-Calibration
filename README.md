# Trigger Calibration 0.3.7

Continues the working 0.3.6 version. The native hardware adapter, independent
Apply/Restore worker, HID protocol, calibration core, Trigger Test reader and
slider components are unchanged. No Boot/Resume application or new background
service is added.

## Locked Original Calibration

The primary archive remains `DECKY_PLUGIN_SETTINGS_DIR/initial.json`.
The independent recovery copy is
`DECKY_PLUGIN_SETTINGS_DIR/original-recovery/initial.json`.
Decky provides this persistent settings directory outside the replaceable plugin
installation. With the standard DeckyTriggerCalibration folder it is normally
`/home/deck/homebrew/settings/DeckyTriggerCalibration/`; Diagnostics displays the
actual full primary and recovery paths. Updates must retain this settings folder.

Both files contain the same LMAX, LMIN, RMAX, RMIN, existing negative-range fields,
controller identity and provenance. Each has a SHA-256 envelope, value/schema
validation and independent disk read-back. Creation is atomic and create-only,
with separate fully flushed temporary files; the primary and recovery copies
are not hard links to one another. Files are private (0600), directories 0700.
The lifetime/global writer locks are retained. `initial-created.json` records
capture history even if archive files later disappear.

For an existing valid older Original, the missing recovery copy is created only
from that stored Original payload. The primary bytes are never rewritten and
current hardware values are never adopted as a replacement. Valid-file ownership
repair after Decky startup changes permissions/ownership only, not backup bytes.

If primary is invalid or missing, the valid recovery copy is used without replacing
or deleting the damaged historical file. If recovery is damaged, the valid primary
is used; damaged recovery is preserved. If both valid copies disagree on identity
or calibration values, writes are refused rather than guessing. Missing/corrupt
archives cannot expose Save again after a previous capture. A damaged existing
file is not automatically replaced. Unknown originals already overwritten by an
older release cannot be reconstructed or labelled factory calibration.

## First capture and export

On a genuinely new setup with no Original/history, first fully shut down and power
on the Deck. Choose Read Calibration, then Save Original Calibration. Confirm the
cold start and the warning that the currently read values may already be modified
and are not guaranteed factory values. The backend reads and repeats the hardware
read after confirmation, creates and verifies both archives, and removes Save
Original Calibration from the normal interface. Repeated RPC capture is refused.
The saved Original is read-only and shown with a lock and all four archived fields.

Use Export Original Backup to write a verified, uniquely named JSON into
`<Steam user home>/Downloads/TriggerCalibrationBackups/` (normally
`/home/deck/Downloads/TriggerCalibrationBackups/`). Copy that JSON to another
computer or storage device. Both automatic copies are on the Deck; export is the
way to protect against loss of the device/storage. Export never reads Current or
replaces either archive. It also works through the recovery copy and is available
after a failed explicit Read when an Original is known.

If both copies are lost/damaged, stop the plugin, preserve the entire settings
folder and recover a known exported backup. An administrator can preserve the
old files under dated names and place the exact exported JSON at the primary and
recovery paths, then reload and let validation check it. Never use Current or
Selected as a guessed replacement. There is no reset/recapture operation in the
normal UI. Re-establishing a different baseline is a separate administrative
procedure requiring preserved history and explicit acknowledgement that the new
values may already be modified.

## Current and Selected

Before an explicit successful Read in the opened panel, only the title, short
instruction and Read Calibration button are shown. A known Original permits
emergency Restore Original even when hardware reading fails. Hardware identity,
compatibility, conflicts and the existing write prerequisites are still checked.
After Read, the existing sliders, numbers, Fine Adjustment and Trigger Test appear.
A failed subsequent Read retains Last Read data and visible sliders.

Current is the latest successful hardware read/read-back. Selected values are
stored separately in `preferences.json`; Read, first Original capture and Restore
never replace a saved selection. Slider changes save only Selected; Apply writes
the selected MAX values through the existing mechanism and confirms read-back.
Restore Original writes all archived fields, without changing Selected preferences.
Both sliders retain normal endpoint 1880 and Caution 1820, with the real archived
Default marker and no color zones. Legacy known values outside the normal range
remain visible, as before. Invalid Selected for a new MIN is retained and must be
adjusted before Apply; it is not silently replaced with Current.

The user reports that Restart and Sleep/Resume retain applied hardware calibration,
while full Shutdown resets it. This version does not alter or assume a guaranteed
reset. After cold start, Read shows whatever the controller actually returns;
Selected stays ready for explicit Apply. Nothing applies calibration automatically.

Live Trigger Test still starts only through Open Trigger Test. Close/unmount stops
the reader and polling; the existing session lease handles lost clients. No reader
runs continuously after the test is closed.

## Install and verification

Install `Trigger_Calibration_0.3.7_INSTALL.zip` with Decky. Keep the stable plugin
folder DeckyTriggerCalibration and its settings. Do not delete or recreate backups
when updating. No user calibration data is shipped in the ZIPs.

Local tests cover immutable independent copies, damaged-copy fallback, conflicting
copies, export, update migration, hardware-write regression paths, Selected through
Restart/cold-start/Restore, initial UI and compiled-source consistency. Native
SteamUI/touch/gamepad and physical behavior of 0.3.7 still need device confirmation;
local hardware tests substitute transport and do not certify a new protocol.

Build: pnpm install --frozen-lockfile; pnpm run typecheck; pnpm run build.
Package: python tools/package.py --output <directory> --baseline <0.3.6 SOURCE.zip>.
Packaging compares the unchanged hardware files and RPC paths. Restore is allowed
only its intentional removal of Selected-preference replacement; native hardware
steps must remain identical. Short report: docs/REPORT_037_BG.md.

## Source control

Source repository: https://github.com/Shushkata/Steam-Deck-Trigger-Calibration.
Keep `main` stable and use focused `feature/` or `fix/` branches for later changes.
See [Git workflow](docs/GIT_WORKFLOW.md). Runtime calibration data, personal device
reports, credentials, dependencies and generated release archives are excluded
from Git. Public tests use synthetic calibration fixtures only.
