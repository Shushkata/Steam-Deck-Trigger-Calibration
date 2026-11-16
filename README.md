# Trigger Calibration for Steam Deck

A simple **Decky Loader plugin** for adjusting the Steam Deck's LT and RT analog trigger sensitivity directly from Gaming Mode — no terminal commands required.

Based on the calibration method developed by [kasandravtv](https://github.com/kasandravtv/steam_deck_overwrite_trigger_cal), with a more user-friendly graphical interface.

## Features

- Adjust LT and RT trigger sensitivity independently.
- Read and display current calibration values.
- Fine-tune sensitivity using sliders.
- Apply changes directly from Decky Loader.
- Test trigger response in real time.
- Save and restore your original calibration.
- Remember your preferred settings for easy reapplication.

## Installation

1. Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader).
2. Download the latest plugin ZIP from **Releases**.
3. In Gaming Mode, open **Decky Loader → Settings**.
4. Enable Developer Mode, then choose **Developer → Install Plugin from ZIP**.
5. Open **Trigger Calibration** from Decky Loader.

## How to Use

1. Press **Read Calibration**.
2. On first use, save your original calibration as a backup.
3. Adjust LT and RT using the sliders.
4. Press **Apply**.
5. Use **Trigger Test** to check sensitivity and unwanted input.

Use **Restore Original** to return to your saved calibration.

## Limitations

As with the original tool, custom calibration is not permanent.

- Settings may survive Restart and Sleep/Resume.
- **A full Shutdown resets the applied calibration** (observed on Steam Deck LCD).
- After powering on, use **Read Calibration → Apply** to restore your preferred settings.

The plugin remembers your selected values, so you don't need to adjust the sliders again.

## Credits

Full credit to **kasandravtv** for the original [Steam Deck Trigger Calibration Overwriting Tool](https://github.com/kasandravtv/steam_deck_overwrite_trigger_cal) and its calibration research.

This project provides a more accessible graphical interface for the same calibration functionality.

Additional reference: [Skaifer's calibration wrapper](https://github.com/Skaifer/steamdeck_overwrite_trigger_cal_wrapper).

Built for [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader).

See LICENSE and THIRD_PARTY_NOTICES.md for licensing and third-party credits.

**Disclaimer:** Independent community project, not affiliated with Valve or the original developer. Use at your own risk.
