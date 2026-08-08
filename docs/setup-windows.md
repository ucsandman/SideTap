# One-time Windows + iPhone setup (~20 min)

Do these steps once. After this, `python launch.py` is all you need.

## Step 1 — iPhone: enable Developer Mode

1. Settings → Privacy & Security → Developer Mode → On.
2. The phone restarts. Confirm "Turn On".

(If you do not see Developer Mode: plug the phone into the PC first with the
Apple Devices app installed, then check again.)

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
   `C:\Windows\System32` (admin), and run `ios tunnel start` in an admin terminal.

## Step 3 — Sideload WebDriverAgent (free Apple ID)

1. Install [Sideloadly](https://sideloadly.io).
2. Download the newest prebuilt WDA ipa from
   https://github.com/appium/WebDriverAgent/releases
   (file like `WebDriverAgentRunner-Runner-*.ipa`).
3. In Sideloadly: select the ipa, select your iPhone, sign in with your
   (free) Apple ID, click Start.
4. On the iPhone: Settings → General → VPN & Device Management → trust your
   Apple ID developer profile.

**Every 7 days** the free signature expires. Fix = repeat step 3.3 (two clicks).
`phone-harness doctor` detects this and reminds you.

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
