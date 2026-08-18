Viewer /api/phone polling

Problem

Some apps (notably TikTok's For You feed) can cause WDA to enter a "heavy-tail" state: calls that resolve the active application (e.g. /wda/activeAppInfo) may hang indefinitely and queue every other request. The viewer previously polled /api/phone every 10s; that poll itself can trigger a wedge.

Mitigation

- The viewer no longer polls /api/phone by default. Use VIEWER_PHONE_POLL_SECONDS in .env to re-enable periodic polling when you need it (example: VIEWER_PHONE_POLL_SECONDS=10).
- Recommended default: 0 (disabled). The viewer still fetches /api/phone once on page load.

How to test

1. Set VIEWER_PHONE_POLL_SECONDS=0 in .env or leave unset; restart the viewer.
2. Reproduce the TikTok wedge (follow repro script). With the poll disabled, leaving the viewer open should not trigger a wedge by itself.
3. To re-enable polling for comparison, set VIEWER_PHONE_POLL_SECONDS=10, restart the viewer, and observe whether the viewer-originated polls correlate with hangs.

Notes

This is a short-term mitigation. The real upstream fix is to bound WDA's active-application/snapshot calls (see appium/WebDriverAgent#1210). The viewer change reduces accidental triggers while that upstream work proceeds.