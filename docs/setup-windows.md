# One-time Windows + iPhone setup (~20 min)

Do these steps once. After this, `python launch.py` is all you need.

> **Shortcut:** `irm https://sidetap.io/install.ps1 | iex` in PowerShell does the
> PC half of this page for you (Python, go-ios, the app, the Apple Devices app,
> a Desktop shortcut) and the viewer's setup guide walks you through the phone
> half. This page is the manual path and the reference when a check stays red.

## Step 1 — iPhone: enable Developer Mode

The Developer Mode menu is **hidden by default**. Apple only shows it after the
phone sees developer tooling. To reveal it: connect the phone over USB and run

```bat
ios devmode reveal
```

(needs go-ios from Step 2; do Step 2 first if `ios` is not installed yet).
Then on the iPhone:

1. Settings → Privacy & Security → scroll down → Developer Mode → On.
2. The phone restarts. Confirm "Turn On" and enter your passcode.

If the menu still does not appear, force-close Settings and open it again.

## Step 2 — Windows: drivers and go-ios

1. Install the **Apple Devices** app from the Microsoft Store (gives the USB driver).
   Plug in the iPhone, unlock it, tap **Trust**.
2. Install [Node.js](https://nodejs.org) if you do not have it, then:
   ```bat
   npm install -g go-ios
   ios list
   ```
   `ios list` must show your device UDID.
3. For the iOS 17+ tunnel, userspace mode usually works with no extra steps
   (`phone-harness up` tries it first). If it fails, doctor will tell you to:
   download `wintun.dll` from https://www.wintun.net, copy it to
   `C:\Windows\System32` (admin), and run
   `$env:GO_IOS_AGENT_PORT=28100; ios tunnel start` in an admin PowerShell.
   SideTap looks the tunnel up on port 28100 (`GO_IOS_AGENT_PORT` in `.env`),
   not go-ios's default 60105, which Hyper-V often reserves at boot.

## Step 3 — Sideload WebDriverAgent (free Apple ID)

1. Install [Sideloadly](https://sideloadly.io).
2. Open the [official Appium WDA releases](https://github.com/appium/WebDriverAgent/releases)
   and download **`WebDriverAgentRunner-Runner.zip`**, the real iOS device build.
   Do not use a `Build-Sim`, tvOS, watchOS, or source-code archive. Appium supplies
   an app ZIP, **not a ready-made IPA** (checked against v16.15.3 on 2026-11-13).
   Extract it, create a new empty `Payload` folder, and copy the extracted
   `WebDriverAgentRunner-Runner.app` folder into it. Compress **the Payload
   folder itself**, not just its contents, into a new ZIP and rename that ZIP
   to `WebDriverAgent.ipa` (enable file extensions in Explorer). Save it in
   this repo's `wda` folder. Inspect the archive before signing: its root must
   contain `Payload/WebDriverAgentRunner-Runner.app/Info.plist`, not another
   outer folder. Renaming the original download alone does not create an IPA.
   This packages the app; it does not sign it or establish device compatibility.
3. In Sideloadly: select the ipa, select your iPhone, sign in with your
   (free) Apple ID, click Start.
4. On the iPhone: Settings → General → VPN & Device Management → trust your
   Apple ID developer profile.

### If Sideloadly fails before installation

Stop here: `fix-input` cannot obtain a new profile from an installation that
never succeeded. [Issue #14](https://github.com/ucsandman/SideTap/issues/14)
reports a signing failure with Sideloadly 0.72. Its reporter describes using
WDA v16.15.1 and excluding `WebDriverAgentRunner.xctest.dSYM` when packaging;
this is a **community-reported workaround, not a SideTap-verified fix**. We have
not verified that combination on the reported iPhone 17 / iOS 26.1. Do not
substitute an IPA from an unknown download site or delete the actual `.xctest`
bundle. Keep the original ZIP and record the WDA release, Sideloadly version,
iOS version and exact error before troubleshooting. Packaging correctness and
successful signing are separate checks; this guide does not promise the latest
WDA release will sign successfully with your Sideloadly version.

### Step 3b — enable touch input

The original tested workflow used Sideloadly 0.60, which left the nested test
bundle unsigned. Do not assume that behavior for every version. One command
re-signs the whole thing (nested bundle included) with go-ios, using a profile
from a successful Sideloadly install - no Apple password re-entry:

```bat
phone-harness fix-input
```

Run it, then click **Start** in Sideloadly. `fix-input` watches for the profile
Sideloadly mints, re-signs WDA locally, installs it, and brings the driver up.
The viewer has the same thing as a **Fix input** button. Keep the phone
**unlocked** during the install step — iOS refuses installs on a locked phone.

Already have a `.mobileprovision` on disk? Pass it directly and skip the wait:
`phone-harness fix-input path\to\profile.mobileprovision`.

**Why it reads the phone, not your PC.** Sideloadly 0.60 signs entirely in
memory and streams the app to the device: it writes no profile to disk at all.
Measured 2026-08-16 across a sign that succeeded, over the whole user profile,
`ProgramData` and `Windows\Temp` — Sideloadly wrote three files, none of them a
profile. So `fix-input` reads the profile from the phone instead, where iOS
keeps every installed one. Two consequences: nothing to race, and a profile
that has not expired yet is found straight away, so mid-week repairs never need
Sideloadly at all.

**Every 7 days** the free profile expires. Fix = run `phone-harness fix-input`
again (click Start in Sideloadly once when it asks). `phone-harness doctor`
detects the expiry and reminds you.

## Step 4 — verify

```bat
pip install -r requirements.txt
phone-harness doctor
```

Fix the first FAIL it prints, run again, until all green. Then:

```bat
python launch.py
```

Your phone screen appears in the browser. Click it — the phone taps.

Day-to-day notes:

- Phone locked? Set `PHONE_PASSCODE` in `.env` (copy `.env.example`), then the
  viewer's **Unlock** button and the agent's `unlock()` helper type it for you.
- Unplugged the phone? Click **Restart link** in the viewer.
- Updated iOS? The update silently unmounts the Developer Disk Image, which
  kills WDA. Unlock the phone and run `phone-harness up` — it re-mounts the
  image itself (the doctor names this too).
