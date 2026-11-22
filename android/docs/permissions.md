# Permissions and special access

What each build asks Android for, what uses it, and in which flavor. The manifests are split by flavor (`AGENTS.md` → flavor rules): `app/src/main/AndroidManifest.xml` holds what all three `:app` flavors declare, `src/phone`, `src/kiosk` and `src/tv` add their own, `wear/src/main/AndroidManifest.xml` is the watch. Libraries add a few more (marked *library*). Keep this page in step with the manifests: a new permission gets a row here in the same change.

## Distribution

The apps are not on Google Play. They are installed by sideloading (`adb install`, `bin/deploy-*.sh`) or through your own Dana server's update channel: the phone downloads a published APK from `/api/app/version` and hands it to the system installer (`docs/release.md`). That is why the phone holds `REQUEST_INSTALL_PACKAGES`, and why it declares a few permissions Play restricts (`READ_CALL_LOG`, `USE_FULL_SCREEN_INTENT`, `ACCESS_BACKGROUND_LOCATION`, `PACKAGE_USAGE_STATS`, the `specialUse` foreground service) without a Play declaration form.

## Permissions

P = phone, K = kiosk, T = TV, W = watch.

| Permission | Flavors | What uses it |
|---|---|---|
| `INTERNET` | P K T W | The server's REST / SSE / WebSocket APIs (`ApiService`, `HttpClients`). The watch declares it but reaches the server only through the phone (Data Layer). |
| `ACCESS_NETWORK_STATE` | P K T (*library*: WorkManager) | WorkManager's network constraints; `ServerStatusMonitor` re-probes the server on network changes (VPN up / down). |
| `RECORD_AUDIO` | P K T W | Push-to-talk and realtime voice (`MicHub`, `RealtimeCallEngine`), the system assistant session (phone), "Hey Dana" (kiosk, TV), the watch's push-to-talk (streamed to the phone). |
| `MODIFY_AUDIO_SETTINGS` | P K T | Realtime voice calls route audio (speaker / communication device: `RealtimeCallController`). |
| `POST_NOTIFICATIONS` | P K T W | Reminders, suggestions, urgent alerts, call results, the foreground-service notifications; on the watch, reminder alarms. Asked at first launch on Android 13+. |
| `FOREGROUND_SERVICE` | P K T | Every foreground service below. |
| `FOREGROUND_SERVICE_SPECIAL_USE` | P | `CallRecordingObserverService` (`specialUse`, subtype in the manifest), only while call recordings are on. |
| `FOREGROUND_SERVICE_DATA_SYNC` | P | WorkManager's `SystemForegroundService` (`dataSync`) for long uploads (call recordings). |
| `FOREGROUND_SERVICE_MICROPHONE` | K T | `WakeWordService` ("Hey Dana", `src/wake`). |
| `RECEIVE_BOOT_COMPLETED` | P K T | Phone: `BootReceiver` registers the geofences (places on) and the activity transitions (driving detection on) again and restarts the call-recording observer (when on). TV: `TvSystemReceiver` switches the button service back on (`KeyServiceGuard`) and, with the home override on, opens Dana TV over Google TV's launcher. The kiosk declares it through `main` but registers no receiver (it is the home screen, so Android relaunches it). |
| `SYSTEM_ALERT_WINDOW` | P K T | "Display over other apps", granted by the user in system settings. Phone: urgent-alert and suggestion screens opened from the background (`NotificationAlertReceiver`, `SuggestionsReceiver`, `ReminderReceiver`), the caller card over the dialer (`CallerCard`). TV: the camera picture-in-picture over other apps (`CameraPip`). The kiosk declares it through `main` and does not use it. |
| `VIBRATE` | P K W | Reminder and urgent alerts (`ReminderActivity`, `NotificationAlertActivity`), haptics; the watch's reminder alarm. |
| `WAKE_LOCK` | P (*library*) K T W | WorkManager / Firebase (phone); `WakeWordService` keeps the CPU while listening (kiosk, TV); the watch's alarm. |
| `USE_FULL_SCREEN_INTENT` | P W | Reminders ring as a full-screen alarm over the lock screen (`ReminderActivity`, `WearReminderActivity`). On Android 14+ this is a special access the user may have to allow (Settings → Apps → Special app access → Full screen intents). |
| `POST_PROMOTED_NOTIFICATIONS` | P | Live Updates (Android 16): the coding-agent session status (`push/AgentSessions.kt`). |
| `READ_MEDIA_AUDIO` (Android 13+) / `READ_EXTERNAL_STORAGE` (≤ 12, `maxSdkVersion=32`) | P | Reading call recordings from `Recordings/Call` (`CallRecordingObserverService`, "Upload past recordings"). Asked only when the user turns call recordings on. |
| `READ_CALL_LOG` | P | Who a recorded call was with, its number and direction (`RecordingInspector`). Asked with call recordings, or from Settings → Call recordings. |
| `READ_PHONE_STATE` | P | The caller card: notice the phone ringing (`CallerCardReceiver`). Asked at first launch. |
| `BLUETOOTH_CONNECT` (Android 12+) / `BLUETOOTH` (≤ 11, `maxSdkVersion=30`) | P | Signals → car: list the paired devices for "This is my car" and receive the car's `ACL_CONNECTED` / `ACL_DISCONNECTED` (`CarSignals`, `CarBluetoothReceiver`). Asked on Android 12+ only when the user picks the car ("Nearby devices"). |
| `ACCESS_FINE_LOCATION` + `ACCESS_COARSE_LOCATION` | P | Signals → places: Google Play services geofences for the user's zones need precise location (`Places`). Asked when the user turns "Place reminders" on. The app never reads a location itself; the server only hears arrive / leave with a zone id. |
| `ACCESS_BACKGROUND_LOCATION` (Android 10+) | P | Places: geofences fire only with location "all the time". Asked as the second step, after a short explanation (Android 11+ grants it on its own settings page). |
| `ACTIVITY_RECOGNITION` (Android 10+) / `com.google.android.gms.permission.ACTIVITY_RECOGNITION` (≤ 9, `maxSdkVersion=28`, granted at install) | P | Signals → car → "Detect driving": Google Play services' in-a-vehicle transitions count as getting in and out of a car without Bluetooth (`Driving`). Asked ("Physical activity") when the user turns it on. No location is read. |
| `health.READ_SLEEP` | P | Signals → "Wake from sleep data": the sleep sessions a health app writes to Health Connect (`HealthSleep`); only start and end are sent. Asked on Health Connect's own permission screen when the user turns it on; Health Connect opens `HealthPermissionsActivity` (why Dana asks). |
| `health.READ_HEALTH_DATA_IN_BACKGROUND` | P | The same, read by the periodic run while Dana is closed. Asked with `READ_SLEEP` only where Health Connect has background reads; without it sleep is read while Dana is open. |
| `REQUEST_INSTALL_PACKAGES` | P | In-app updates from your server (`UpdateManager` → the system installer; FileProvider serves the APK). The user allows "Install unknown apps" for Dana once. |
| `WRITE_SECURE_SETTINGS` | T | Granted over adb only (`bin/deploy-tv.sh`: `pm grant`): `KeyServiceGuard` switches the accessibility button service back on after an update. Without the grant the TV works; the service must then be re-enabled by hand after updates. |
| `com.google.android.c2dm.permission.RECEIVE` | P (*library*: Firebase) | Direct push (FCM, `DanaMessagingService`); only does something when the build has a `google-services.json`. |
| `<applicationId>.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION` | P K T W (*library*: AndroidX core) | Keeps runtime-registered receivers private to the app. |

TV features: `leanback`, `touchscreen` and `microphone` are declared not required, so the TV build installs on TVs without a mic remote.

Package visibility (phone): `<queries>` names Health Connect's app (`com.google.android.apps.healthdata`) so the app can tell on Android 9–13 whether it is installed and up to date.

## Special access (granted in system settings, not a runtime prompt)

| Access | Flavor | What uses it | How it is granted |
|---|---|---|---|
| Notification access | P | `DanaNotificationListener`: the notification Inbox, triage and digests (`docs/notifications.md`). Nothing is read until the user grants the access; the Inbox switch in Dana's Settings turns capture off again. | Settings → Notifications → "Notification access" (Dana's Settings → Inbox links there). |
| Default digital assistant | P | `DanaVoiceInteractionService` / `DanaRecognitionService`: long-press Home or the side key opens Dana's sheet. | Settings → Apps → Default apps → Digital assistant app. |
| Display over other apps | P T | See `SYSTEM_ALERT_WINDOW`. | Settings → Apps → Special app access. |
| Install unknown apps | P | See `REQUEST_INSTALL_PACKAGES`. | Prompted on the first in-app update. |
| Usage access (`PACKAGE_USAGE_STATS`) | P | Signals → "Wake from screen time": the screen's on / off history (`ScreenTime`, Android 9+; never which apps were used), sent as "screen" stretches the wake flow confirms with (`docs/phone.md` → Signals). | Settings → Apps → Special app access → Usage access; the switch on Dana's Signals page opens it. |
| Full screen intents | P W | See `USE_FULL_SCREEN_INTENT` (Android 14+). | Settings → Apps → Special app access. |
| Accessibility service | T | `DanaKeyService`: remote buttons open Dana and camera picture-in-picture (`docs/tv.md`). | Settings → Accessibility, or `bin/deploy-tv.sh` (with `WRITE_SECURE_SETTINGS`). |
| Home app | K T | `KioskActivity` / `TvHomeActivity` are `HOME` activities, so Android relaunches them after boot, crashes and Home presses. | Chosen once when Home is pressed, or set over adb. |

## Call recordings (phone)

Android does not let apps record calls. Dana uploads the recordings the phone's own dialer makes, and it only knows one place for them: `Recordings/Call` on shared storage, where **Samsung's built-in call recorder** saves them. On other phones there is nothing for it to find.

The feature is **off by default**. Settings → Call recordings → "Watch for call recordings" turns it on; that asks for audio-file access (`READ_MEDIA_AUDIO` / `READ_EXTERNAL_STORAGE`) and the call log, and starts `CallRecordingObserverService`. Until then the observer never starts (not from the app, `BootReceiver`, the notification listener or the hourly sync: all go through `CallUploadSettings.canWatch`) and those permissions are not requested. Turning it off stops the observer. Manual uploads (sharing an audio file to Dana, the Calls screen's file picker) work either way and need no storage permission. The flow after a recording is found: `docs/phone.md` → Call recordings.
