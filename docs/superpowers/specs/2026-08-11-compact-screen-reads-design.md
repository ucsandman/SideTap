# Compact screen reads at the MCP boundary

**Date:** 2026-08-11
**Status:** implemented
**Files:** `src/phone_harness/mcp_server.py`, `tests/test_mcp_server.py`

## Problem

An LLM driving the phone pays for every byte of every screen read. A full
`ocr()` of the Settings root returned 50 elements and ~1,605 tokens when the
agent needed one row. Across a real session (~70 tool calls) reads dominated
the bill, and the agent reached for screenshots because the JSON dumps were
too unwieldy to scan.

The backlog framed this as **spd-1**, a *latency* problem: "replace the full
/source tree fetch with a targeted /elements predicate query and/or cache the
tree; ~3s/214KB per call." That framing is now stale in two ways:

1. The tree cache **already landed** (`helpers._invalidate_tree`, ~2s TTL).
2. `/elements` predicate queries were **measured slower** than `/source` on
   device and are explicitly warned against in the repo's CLAUDE.md.

What remained was never latency. It was **context cost**, which caching does
not touch — a cached tree is exactly as expensive once serialized into the
model's context.

## Measurements

Settings root, 50 elements, before any change:

| Read | Tokens |
|---|---|
| `ocr()` | ~1,605 |
| text + x + y only | ~615 |
| one screenshot (post-resize billing) | ~1,500 |

Of the 50 elements, 17 were tappable. The rest were containers and
`StaticText` labels duplicating the text of their parent button.

Screenshots are billed after Claude resizes to a 1568px long edge, so
downscaling before upload saves nothing. Rejected on that basis.

## Decision

Compact `ocr` and `find_text` **at the MCP boundary**, not in `helpers`.

`mcp_server.py` already carries the untrusted-content envelope for exactly this
reason — its own comment says *"the wrapper belongs here because this is where
the model's context begins."* Compaction is the same concern, so it belongs in
the same place. `viewer.py`, `send_message`, and the tests keep the full tree
and do not move.

`_compact()` drops:

- **`_NOISE_TYPES`** — `Application`, `Window`. Whole-screen wrappers.
- **`rect`** — `x`/`y` is what a tap needs.
- **A label enclosed by a control whose text already contains it.** Keeping the
  enclosing control is also the correct tap target; hitting the inner
  `StaticText` instead of its `Button` is the classic mis-tap.
- **Identical text overlapping itself** — collapsed to the highest `_rank`
  (actionable type first, then larger target).

`ocr(full=True)` restores the raw tree with rects.

### `Other` is not noise

The first cut listed `Other` in `_NOISE_TYPES`. That was wrong and shipped
briefly. The Home Screen search affordance is an `Other`, so dropping the type
silently removed the only way to tap search. Non-obvious because the failure is
an absence: nothing errors, the agent just cannot find a control that is
plainly on screen.

`Other` now survives and instead acts as an *encloser*, which collapses its own
redundant children — the same screen returns one `Other "Search"` rather than
three overlapping entries. Pinned by
`test_compact_keeps_an_other_that_is_a_real_target`.

### The safety rule that shaped the implementation

The first prototype dropped **any** enclosed element whose text was a substring
of its container. That is unsafe: a `Switch` inside its row, or a `checkmark`
reporting which option is selected, is independently tappable or carries state
the agent needs. During this session the `checkmark` was the only way to read
which "Show Previews" option was active.

So only `_LABEL_TYPES` (`StaticText`, `Image`) are ever droppable. Anything
else survives regardless of enclosure. This costs ~5 percentage points of
savings (67% → 62%) and is worth it. Both cases are pinned by tests.

`_compact` preserves every key except `rect`, rather than rebuilding a fixed
dict, so rows lacking `x`/`y` (as in existing tests) do not raise.

## Results

Measured with the shipped code across five real screens:

| Screen | Elements | Tokens | Saving |
|---|---|---|---|
| Home screen | 36 → 30 | 1,096 → 494 | 55% |
| Shortcuts library | 75 → 59 | 2,385 → 1,014 | 57% |
| Reminders | 16 → 11 | 510 → 193 | 62% |
| Messages list | 81 → 36 | 3,089 → 819 | 73% |
| **Total** | | **7,082 → 2,522** | **64%** |

Duplicate collapsing more than paid for keeping `Other`: the Messages list went
from 61% to 73%.

Full suite: 252 passed. Compaction tests verified non-vacuous by neutering
`_compact` and confirming the behavioral tests fail.

## Round-trip helpers (same session)

`helpers.scroll_until_found()` and `helpers.find_on_home_screen()` collapse two
loops the agent otherwise hand-rolls every time. Both are registered as MCP
tools inside the untrusted-content envelope, since they return phone content.

`scroll_until_found` refuses to call a hit "found" while it sits under the nav
bar or behind the bottom toolbar (`_REACH_TOP`/`_REACH_BOTTOM`, 0.17–0.86 of
screen height), because tapping there hits chrome instead of the row.

`find_on_home_screen("Brain Dump")` located an icon on page 8 in one call.
**It took 65s**, roughly 8s per page, because every page needs a real tree
fetch. It is one round trip, not a fast one. Tracked as rt-1.

## Alternatives rejected

- **New `screen()` tool, leave `ocr()` alone.** Zero risk to callers, but two
  near-identical tools means choosing correctly on every call, and the agent
  will sometimes choose wrong. The ambiguity cost recurs forever; the
  compatibility risk was one-time and is covered by tests.
- **Server-side predicate query** (`find(role=..., text~=...)`). Cheapest
  possible, but only helps when the agent already knows what it wants.
  Orientation on an unfamiliar screen still needs a full read, so the expensive
  case survives. Worth revisiting as an addition, never as the fix.
- **Screenshot downscaling.** No token win; images are billed post-resize.
- **Trimming the untrusted-content envelope.** ~1k tokens per session, but it
  is load-bearing for prompt-injection defense. Not worth weakening.

## Follow-ups not taken

- `wait_for_text` returns a single element; compacting it saves ~40 chars. Left
  alone to keep the blast radius small.
- Round-trip reduction (scroll-until-found, find-across-Home-Screen-pages) is a
  separate and still-open win. Sweeping Home Screen pages for one icon cost ~15
  calls in this session.
