# Hardware: tablets and TVs

Which device to put Dana on, which build to install, and what the wake word ("Hey Dana") needs. Each device is paired from Dana's web Devices page ("Pair a device" → Tablet or TV) and then shows its own dashboard, chosen on the same page (server `docs/dashboard.md`). The phone and the watch are covered in `phone.md` and `wear.md`.

## Tested on

| Device | Build (flavor) | Notes |
|---|---|---|
| A 10-inch Android 10 tablet (64-bit ARM), wall-mounted, always on its charger | `kiosk` ("Dana Kiosk"), `bin/deploy-kiosk.sh --set-home` | The tablet becomes the home screen: its dashboard full screen, the assistant sheet on "Hey Dana" or the page's mic button, night mode from its light sensor (`kiosk.md`). |
| A Google TV with Android 14 and a 32-bit ARM userspace | `tv` ("Dana TV"), `bin/deploy-tv.sh` | Replaces the TV's home screen: apps, Home Assistant tiles, cameras with picture-in-picture, the Dashboard row, remote-button bindings (`tv.md`). |
| The Android emulator (x86_64) | either | Every build also carries x86_64 for the emulator. A phone or tablet emulator runs the TV build too (no leanback needed), driven with D-pad key events; the remote's accessibility key filter only sees real remote keys (`tv.md`). |

Nothing else is tested; any Android tablet or Android TV / Google TV that meets the requirements below should work.

## Which build

- **A tablet on a wall or a counter**: `kiosk`. It needs a 64-bit ARM device (`arm64-v8a`); a 32-bit-only tablet won't install it.
- **A TV**: `tv`. It is built for `armeabi-v7a` (many TVs run a 32-bit userspace even on a 64-bit chip); a TV whose Android only runs 64-bit apps won't install it.
- **A phone, foldable or a tablet you carry**: `phone` (`phone.md`), not the kiosk.

Both run Android 8 or later (the app's minimum); a device without Google Play is fine (sideloaded or from your server: `permissions.md` → Distribution).

## What the wake word needs

- **A microphone the app can keep open**: a built-in mic on the tablet; on a TV one that Android exposes as an ordinary audio input (some TVs only open the remote's mic while its button is held, so "Hey Dana" can't hear there; the TV build installs without a mic and the remote's Ask Dana binding still works).
- **Always-on power**: the mic stays open in a foreground service (`WakeWordService`), about a third of one core on a TV-class chip; a tablet stays on its charger with the screen kept on.
- **Android 8 or later** with the microphone permission granted (the app asks on first start).
- **For spoken replies**: a text-to-speech engine with voices for the user's languages (`kiosk.md` → Flow).

What is heard is sent to your Dana server only after "Hey Dana"; detection runs on the device (`kiosk.md` → Wake word).
