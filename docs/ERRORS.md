# ERRORS.md

Recurring failures, their root causes, and the fix that actually worked. Short
entries only. Newest first.

---

## 2026-08-11 — Home Screen bulk reorganisation: the load-bearing gesture was never proved

**Failure.** A request to "organise the whole Home Screen" (8 Home Screen pages,
~160 loose icons, 132 installed apps) was surveyed, mocked, costed and *approved*
before the one gesture the whole plan depended on was tested. Two assumptions
then failed on contact with the device.

**1. The page I numbered 1 was not a Home Screen page at all — it was Today
View.** The dedup sweep walks until the icon signature repeats, and its leftmost
end stop is Today View ("page 0"): always present, not hideable, and **absent
from the page editor**. I counted it as page 1, which shifted every later page
number by one and made the real folder page "page 2". Had the page-hiding step
run on that numbering it would have unchecked the wrong thumbnails, including the
user's only organised page. The user caught it, not the tooling.

**The fix, found the next day:** the `PageIndicator`'s `value` states the
position outright — `Page 4 of 8` on a Home Screen page, `Page 0 of 8` on Today
View, `Page 9 of 8` in the App Library. One read gives index, total and zone, so
there is nothing to detect and no end stop to miscount. It went unnoticed because
`ocr()` cannot see it: `collect_texts` prefers `label`, which is null on that
element, so it falls back to `name` (`"Page control"`) and drops `value`. Shipped
as `helpers.current_page()`.

`press_home()` is **not** an anchor either: `/wda/homescreen` only exits an app
to the springboard. From page 4 two consecutive calls both stayed on page 4, and
from the App Library it does not even leave. Use `helpers.goto_home_page(1)`.

Secondary, and still true: **`ocr()` reports widgets with `type == "Icon"`,
identical to apps.** The tell is geometry — widget centres sit *between* the four
icon columns (x≈120 and 320, versus 69/170/270/371) — and because Today View is a
`ScrollView`, a large widget reported y=1124 on a 956pt screen and coordinates
shifted between reads.

**2. Cross-page drag does not work with the obvious gesture**, and nearly every
planned move needed it. Two failure modes, neither of which raises:

- A static `{"type":"pause"}` at the left edge (x=14–16) flips **no page at all**.
  The icon simply stays put.
- Gliding to the edge fast (5 segments × 130ms across ~260pt) then jittering
  *does* flip the page — but the icon was never picked up, so the gesture was
  only ever a swipe. The post-gesture read shows a different page, which looks
  exactly like success. A later full sweep found the icon still at its origin.

**Root cause of 2 (hypothesis, untested).** Pickup depends on the speed of the
first movement after `pointerDown`. The verified same-page drag uses ~180ms per
segment over short hops; the failing one used 130ms per segment across ~260pt.
Fast initial motion gets classified as a swipe, not a drag. Same-page drags never
hit this because they are short by nature.

**Verified working.** Same-page drag (icon moved, confirmed by coordinates).
Folder creation by dropping icon A onto icon B — and iOS auto-names the folder
from its *own* App Library category guess, not from the apps inside — two AI apps
landed in a folder called `Productivity`.

**Also cost a run.** `retry()` wrapped `ocr()` but not `swipe()`. One
`RemoteDisconnected` inside a mid-script swipe killed the script and lost every
buffered print, leaving the phone a step ahead of the last reported state.

**Result / what to do instead.** Bulk reorganisation is not economical over WDA:
~160 icons × ~10–15s per verified drag, against WDA dropping roughly 5× per 25
minutes of Home Screen work, with a real chance of stranding half-sorted (worse
than the starting state). Lead with page-hiding — `PageIndicator` → uncheck,
~10 taps, instant, reversible, apps stay installed and stay in App Library +
Spotlight — and hand-drag only the few icons that must live on page 1. App
Library already categorises everything for free.

**Alternatives rejected.** Dragging out of App Library (the drop slot is not
controllable; iOS drops it in the first free slot, usually the last page).
Multi-touch icon stacking (needs a second simultaneous pointer; unproven on WDA).

**Captured in** `skills/phone-gotchas/SKILL.md` (traps table, cross-page drag
section, and the "price a bulk reorganisation" costing section) and
`skills/phone/SKILL.md`. Both re-copied to `~/.claude/skills/`.
