# Viewer zero-scroll layout — final spec

**Date:** 2026-08-10
**File touched:** `src/phone_harness/viewer.html` (single file, inline CSS/JS, no libraries, dark theme)
**Backend touched:** none. `viewer.py` has zero DOM-id coupling (verified: it serves `/api/*` JSON only).
**Test gate:** `tests/test_viewer.py:203` asserts element-id uniqueness only. Every rename below is free.

---

## 1. Winner and how the tournament resolved

**Winner: "Dashboard Grid (kill the tabs)"** — the head judge's pick.

Vote math: head judge (weighted most) picked Dashboard Grid; the other two picked
viewport-adaptive-fit. Weighting the head judge at 2× produces a 2–2 tie, and the head
judge breaks ties. Dashboard Grid also wins the axis the user actually complained about:
he has 1600px of width and only 900px of height, and today ~1000px of width is wasted
while the agent activity feed — the thing he wants to *watch* — hides behind a tab click.

**Grafted in (the steals the judges named), all compatible:**

| Steal | From | Status |
|---|---|---|
| Delete the `--banner-h` JS hack; size the phone pane with flex `1fr` + `[hidden]{display:none}` instead. Delete `height:min(100vh - 260px, 1180px)`. | viewport-adaptive-fit (head judge steal #1) | **Accepted** — §4.1 |
| Global-keydown guard: overlays must swallow Escape/typing before it reaches the phone | Overlay-based (head judge steal #2) | **Accepted** — §7.10 |
| Split `fix-panel.hidden = inputEnabled` (line 363) into "button visibility is poll-driven" vs "modal open state is user-driven" | Overlay-based (head judge steal #2) | **Accepted** — §7.7 |
| One-shot auto-open on the ok→fail transition instead of re-expanding every 5s poll (line 677) | HUD / Horizontal Bands (head judge steal #3) | **Accepted** — §7.2 |
| Fold battery / lock / front-app glance bits into the header row | HUD (judge 2 steal #1) | **Accepted** — `#strip-info` stays, moves into `#dash-header` |
| Fix-input becomes a modal, not an in-flow row | Overlay-based + Horizontal Bands (judge 2 steal #2) | **Accepted** — already Dashboard Grid's plan, §5.5 |
| Hard render cap inside the checks overlay (`+N more failing`) instead of "10 should fit" | Overlay-based (judge 3 steal #1) | **Accepted and extended to every overlay** — §5 |
| Compact dot row as the health signal, cheaper to read than prose | HUD (judge 3 steal #2) | **Accepted** — dots *and* text on one line, §4.2 |
| Promote banners to a full-width row 0 spanning both columns | Horizontal Bands (judge 3 steal #3) | **Declined** — see §1.1 |

### 1.1 Declined steal, with reason

Judge 3 wanted the LAN/signature banners moved out of `#phone-pane` into a page-wide
row 0, on the grounds that they are "coupled to the phone column height." That coupling
is exactly what steal #1 removes: once `#screen-slot` is `flex:1 1 auto; min-height:0`,
a banner appearing simply makes the phone shorter, with zero JS and zero risk. Moving
the banners requires converting `<body>` to a page-level grid — the single biggest
regression risk in the whole tournament — to buy nothing. Banners stay inside
`#phone-pane` as flex children (`flex:0 0 auto`), which is where the two existing
`Lock ports` / `Fix input` buttons already live.

### 1.2 The "hero inversion" objection, resolved

Judges 2 and 3 worried that widening `#side` would shrink the phone. It cannot: the
phone image is **height-limited, not width-limited**. Its aspect is 440×956pt (0.4603),
so at 900px of viewport height the image is ~634px tall and therefore only ~292px wide
no matter how much horizontal room exists. Dropping the 520px `#side` cap costs the
phone nothing and hands ~1180px to the dashboard. Numbers in §3.

---

## 2. Final ASCII mockup — 1600×900, healthy state

```
┌ #phone-pane ─────────────┬ #side ──────────────────────────────────────────────────────────────────────┐
│ flex:0 0 auto            │ flex:1 1 auto  min-width:0  max-width:1400px  height:100vh  overflow:hidden  │
│ width:clamp(340px,46vh,  │                                                                               │
│ 560px)  height:100vh     │ ┌ #dash-header  flex:0 0 auto  ~48px ───────────────────────────────────────┐ │
│ display:flex column      │ │ phone-harness   ●●●●●●●●●● All 10 checks pass ▾    🔋87% 🔓 Messages     │ │
│ gap:10  overflow:hidden  │ │ ^h1, 17px       ^#checks-dots ^#checks-text        ^#strip-info (right)   │ │
│                          │ └───────────────────────────────────────────────────────────────────────────┘ │
│ [#lan-banner  hidden]    │ ┌ #dash-grid  flex:1 1 auto  min-height:0  788px ───────────────────────────┐ │
│ [#sig-banner  hidden]    │ │ ┌ #col-actions ─────┐┌ #col-agent ───────┐┌ #col-phone  (row auto) ──────┐│ │
│ ┌ #screen-slot ────────┐ │ │ │ TEXT SOMEONE      ││ ACTIVITY          ││ PHONE                        ││ │
│ │ flex:1 1 auto        │ │ │ │ [Mom★][Dad][Sam]  ││ 2s  tap 214,600   ││ Battery      87% · charging  ││ │
│ │ min-height:0         │ │ │ │ [Contact_______]  ││ 9s  swipe         ││ Lock state   Unlocked        ││ │
│ │  ┌ #screen-wrap ─┐   │ │ │ │ [Message__][Send] ││ 18s type 6 chars  ││ Front app    Messages        ││ │
│ │  │ height:100%   │   │ │ │ │                   ││ 41s tap_text(…)   ││ Screen       440 × 956 pt    ││ │
│ │  │ ┌───────────┐ │   │ │ │ │ OPEN APP          ││ 1m  open_app(…)   ││ Session      3f9a2c1e        ││ │
│ │  │ │  #screen  │ │   │ │ │ │ [Messages][Maps]  ││ … up to 10 rows   ││ Input sig.   6d 4h left      ││ │
│ │  │ │ height:   │ │   │ │ │ │ [Camera][Notes]   ││ [+12 earlier ▸]   │└──────────────────────────────┘│ │
│ │  │ │ 100%      │ │   │ │ │ │ [Settings][Mail]  ││                   │┌ #col-console  (row 1fr) ─────┐│ │
│ │  │ │ width:    │ │   │ │ │ │ [Photos][Files]   ││ RECENT SENDS      ││ CONSOLE                      ││ │
│ │  │ │ auto      │ │   │ │ │ │ [Clock][Weather]  ││ Mom: on my way ✓  ││ [tap_text("General")][Run]   ││ │
│ │  │ │           │ │   │ │ │ │                   ││ Dad: call me ✓    ││ > ocr()                      ││ │
│ │  │ │ ~292×634  │ │   │ │ │ │                   ││ … up to 6 rows    ││ ["General","Wi-Fi", …]       ││ │
│ │  │ └───────────┘ │   │ │ │ │                   ││ [+4 earlier ▸]    ││ > tap_text("Wi-Fi")          ││ │
│ │  │ #tap-dot abs  │   │ │ │ └───────────────────┘└───────────────────┘│ true                         ││ │
│ │  └───────────────┘   │ │ │                                            │ [+2 earlier ▸]               ││ │
│ └──────────────────────┘ │ │  #col-actions and #col-agent span BOTH     │ Whitelisted: ocr(), tap_text ││ │
│ ┌ #controls flex:0 0 ──┐ │ │  grid rows. #col-phone is the auto row,    └──────────────────────────────┘│ │
│ │ [■ STOP            ] │ │ │  #col-console takes the 1fr row.                                          │ │
│ │ [⌂ Home][Unlock]     │ │ └───────────────────────────────────────────────────────────────────────────┘ │
│ │ [Lock][▾ Notifs]     │ │                                                                               │
│ │ [▾ Control][Save …]  │ └───────────────────────────────────────────────────────────────────────────────┘
│ │ [Refresh checks]     │
│ │ [paste box…][Send]   │   ── #ov (position:fixed; inset:0; hidden by default) ─────────────────────────
│ └──────────────────────┘   Dim backdrop rgba(0,0,0,.55). One shell, five bodies, exactly one visible.
│ Click to tap · drag to     ┌ #ov-panel  width:min(760px,86vw)  max-height:82vh  overflow:HIDDEN ───────┐
│ swipe · type to send keys  │ #ov-title: Checks                                          [×] #ov-close │
└──────────────────────────  │ ─────────────────────────────────────────────────────────────────────── │
                             │ #ov-body → shows ONE of: #doctor | #fix-panel | #full-activity |         │
                             │            #full-sends | #full-console                                   │
                             │                                                                          │
                             │ PASS  go-ios present            2.5.1              ← one line, always     │
                             │ PASS  tunnel process            pid 8812 alive                            │
                             │ FAIL  LAN exposure              :8100 reachable from 192.168.1.0/24       │
                             │       fix: run scripts\lock_ports.ps1                        [⧉ copy]     │
                             │ FAIL  input signature (7-day)   expires in 6h                             │
                             │       fix: click Fix input                                   [⧉ copy]     │
                             │ +2 more failing — see `phone-harness doctor`   ← only past 8 expanded     │
                             └──────────────────────────────────────────────────────────────────────────┘
```

Nothing on this page scrolls. Not the body, not a pane, not a column, **not even the
overlay** (§5.6). The mouse wheel over `#screen` still drives the phone, unchanged.

---

## 3. Height and width budgets at 1600×900

### 3.1 Width

| Region | Rule | Computed at 1600×900 |
|---|---|---|
| `#phone-pane` | `flex:0 0 auto; width:clamp(340px, 46vh, 560px)` | 414px (46vh of 900) |
| phone content box | minus `padding:var(--s4)` ×2 | 366px |
| phone image | height-limited by aspect 0.4603 | ~292px wide, centered, ~37px slack each side |
| `#side` | `flex:1 1 auto; min-width:0; max-width:1400px` | 1186px |
| `#side` content box | minus `padding:var(--s4)` ×2 | 1138px |
| `#dash-grid` columns | `1.1fr 1fr 0.9fr`, `gap:var(--s3)` (16px ×2) | 405 / 369 / 332px |

**Why the phone pane is width-clamped instead of shrink-wrapped.** If the pane shrink-wraps
the image while the image's height comes from a flex leftover, the layout is circular:
pane width → button wrapping → controls height → leftover height → image height → image
width → pane width. `height:min(100vh - 260px, 1180px)` on line 45 exists precisely to
break that cycle, and it is the bug (it does not know about banners). Clamping the pane's
width on `vh` breaks the cycle from the other end with pure CSS, monotonic in viewport
height, banner-proof, zero JS. The leftover width inside the pane is not wasted: `#controls`
and `#click-hint` already use `width:0; min-width:100%` (line 49) so the button row stretches
to the full pane width — which makes it wrap to **fewer** rows, which gives the phone **more**
height. Net win.

Sanity at other viewport heights: 1080 → pane 497px, image ~360×782 (width does not bind).
1440 → pane 662px, image ~540×1174 (does not bind). 700 → clamp floor 340px, image ~181×394.
The width clamp never binds, so the image is never letterboxed and
`screen.getBoundingClientRect()` stays an exact 1:1 map to phone points. **This is load-bearing
for tap accuracy** — see §8.

### 3.2 Height, `#phone-pane` (flex column, `gap:var(--s2)` = 10px)

| Child | Sizing | Healthy | Both banners |
|---|---|---|---|
| pane padding (top + bottom) | `var(--s4)` ×2 | 48 | 48 |
| `#lan-banner` | `flex:0 0 auto`, `[hidden]{display:none}` | 0 | 38 |
| `#sig-banner` | same | 0 | 38 |
| `#screen-slot` | **`flex:1 1 auto; min-height:0`** | **650** | **534** |
| `#controls` (STOP 40 + gap 6 + buttons 2 rows 74 + gap 6 + paste 34) | `flex:0 0 auto` | 160 | 160 |
| `#click-hint` | `flex:0 0 auto` | 22 | 22 |
| flex gaps (only between *visible* children) | 10 each | 20 | 40 |
| **total** | | **900** | **900** |

Image height = slot − 16 (the 8px bezel border, `box-sizing:border-box`): **634px healthy,
518px with both banners**. Width follows: 292px / 238px. The phone never disappears, never
crops, never scrolls. **Flexbox, not grid, for this column** — a grid `gap` still applies
between tracks whose only item is `display:none`, silently wasting 20px; flexbox does not.

### 3.3 Height, `#side` (flex column)

| Child | Sizing | at 900 |
|---|---|---|
| pane padding | `var(--s4)` ×2 | 48 |
| `#dash-header` | `flex:0 0 auto` | 48 |
| gap | `var(--s3)` | 16 |
| `#dash-grid` | **`flex:1 1 auto; min-height:0`** | **788** |

`#dash-grid` rows: `auto 1fr`, `gap:var(--s3)`.

| Cell | Height |
|---|---|
| `#col-actions` (spans both rows) | 788 |
| `#col-agent` (spans both rows) | 788 |
| `#col-phone` (row 1, `auto`) | ~236 (6 `.prow` × 30 + label 24 + padding 32) |
| `#col-console` (row 2, `1fr`) | 788 − 236 − 16 = **536** |

Inside `#col-agent` (padding 16 ×2 → 756 usable): `#activity` and `#sent` are each
`flex:1 1 0; min-height:0; overflow:hidden` → ~370 each, minus a 24px section label = **346px
of list budget each**.

Inside `#col-console`: label 24 + `#console-row` 40 + gap 16 + `#console-hint` ~70 + padding 32
→ `#console-out` gets `flex:1; min-height:0` ≈ **354px**.

Inside `#col-actions` (373 usable width, 756 usable height): label 24 + contact chips ~74 +
`#text-to` 36 + `#text-row` ~60 + label 40 + app chips ~130 ≈ **364px used of 756**. Roomy —
**the existing `.slice(0,10)` / `.slice(0,12)` chip caps stay exactly as they are.** Dashboard
Grid proposed tightening them to 6/8 for a 350px cell; at 405px wide and 756px tall that is
unnecessary, and skipping it removes two JS edits.

---

## 4. CSS plan — exact rules

### 4.1 Phone pane (replaces lines 34–45, 49–52)

```css
#phone-pane {
  flex:0 0 auto; width:clamp(340px, 46vh, 560px);
  height:100vh; overflow:hidden;              /* was max-height:100vh; overflow-y:auto */
  padding:var(--s4); display:flex; flex-direction:column; align-items:center; gap:var(--s2);
}
#lan-banner, #sig-banner, #controls, #click-hint { flex:0 0 auto; }
/* The one flexible track: it absorbs whatever banners and controls leave. */
#screen-slot { flex:1 1 auto; min-height:0; display:flex; align-items:center; justify-content:center; width:100%; }
#screen-wrap {
  position:relative; height:100%; max-width:100%;
  border:8px solid #232b34; border-radius:44px; overflow:hidden; background:#000;
  box-shadow:0 0 0 1px #39434f, 0 26px 60px rgba(0,0,0,.55);
  transition:border-color var(--t), box-shadow var(--t);
}
/* DELETED: #screen-wrap { flex-shrink:0 }  — nothing shrinks it now; the slot does the math.
   DELETED: #screen { height:min(100vh - 260px, 1180px) } — the magic number that ignored banners. */
#screen { display:block; height:100%; width:auto; max-width:100%;
          background:#000; cursor:crosshair; user-select:none; -webkit-user-drag:none; touch-action:none; }
```

`#screen-wrap { height:100% }` resolves against `#screen-slot`'s flex-resolved height, which is
definite because `#phone-pane` has `height:100vh`. `#screen { height:100% }` then resolves
against the wrap's *content* box (border-box sizing subtracts the 16px bezel). Width `auto`
follows the natural aspect, and the wrap — an auto-width flex item in a centered row — hugs it.
The bezel keeps hugging the image exactly, so `#tap-dot`'s absolute positioning is unchanged.

Keep line 49's `#controls, #click-hint, #lan-banner, #sig-banner { width:0; min-width:100%; }`
verbatim. It is what makes the button row stretch to the pane instead of widening it.

### 4.2 Side pane, header, dashboard grid (replaces lines 114–131)

```css
#side {
  flex:1 1 auto; min-width:0; max-width:1400px;   /* was flex:1; max-width:520px */
  height:100vh; overflow:hidden;                   /* was overflow-y:auto — the 2nd scrollbar */
  padding:var(--s4); display:flex; flex-direction:column; gap:var(--s3);
}
h1 { font-size:17px; font-weight:600; letter-spacing:-.015em; }   /* was 22px */

#dash-header {
  flex:0 0 auto; display:flex; align-items:center; gap:var(--s3);
  padding:9px 12px; background:var(--panel); border:1px solid var(--line); border-radius:var(--r2);
}
#hdr-checks { border:0; background:none; padding:0; font-size:13px; font-weight:600; gap:8px; }
#hdr-checks:hover { background:none; color:var(--text); }
#checks-dots { display:inline-flex; gap:3px; }
#checks-dots .dot { width:7px; height:7px; }        /* .dot / .dot.bad already exist, lines 123-124 */
#strip-info { margin-left:auto; font-size:12.5px; white-space:nowrap; }   /* unchanged, just re-parented */

#dash-grid {
  flex:1 1 auto; min-height:0;                     /* min-height:0 is mandatory: without it a grid
                                                      item refuses to shrink below content size and
                                                      silently re-creates the scrollbar */
  display:grid; gap:var(--s3);
  grid-template-columns:1.1fr 1fr .9fr;
  grid-template-rows:auto 1fr;
  grid-template-areas:"actions agent phone"
                      "actions agent console";
}
#col-actions{grid-area:actions} #col-agent{grid-area:agent}
#col-phone{grid-area:phone}     #col-console{grid-area:console}
.col {
  min-height:0; min-width:0; overflow:hidden; display:flex; flex-direction:column;
  background:var(--panel); border:1px solid var(--line); border-radius:var(--r3); padding:var(--s3);
}
/* DELETED: #tabs, .tab, .tab:hover, .tab.on, .tabpane, #checks-wrap */
```

### 4.3 Column internals

```css
#col-agent #activity, #col-agent #sent { flex:1 1 0; min-height:0; overflow:hidden; display:flex; flex-direction:column; margin-top:0; }
#activity-list, #sent-list { flex:1 1 auto; min-height:0; overflow:hidden; }   /* was max-height:55vh; overflow-y:auto */
.act-row, .sent-row { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }  /* constant row height */
.sent-row { overflow-wrap:normal; }                                                    /* overrides line 171 */
.more-btn { flex:0 0 auto; align-self:flex-start; margin-top:var(--s1); border:0; background:none;
            padding:2px 0; font-size:12px; color:var(--dim); }
.more-btn:hover { background:none; color:var(--accent); }

#console-out { flex:1 1 auto; min-height:0; overflow:hidden; }   /* was max-height:52vh; overflow:auto */
#console-out > div { max-height:6em; overflow:hidden; }
#phone-rows { flex:0 0 auto; }
.chips { max-height:150px; overflow:hidden; }                     /* guardrail only; caps already fit */
```

### 4.4 Overlay shell (new)

```css
#ov { position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center;
      background:rgba(0,0,0,.55); }
#ov-panel { width:min(760px, 86vw); max-height:82vh; overflow:hidden;   /* HIDDEN, not auto — §5.6 */
            display:flex; flex-direction:column; gap:var(--s2);
            background:var(--panel); border:1px solid var(--line); border-radius:var(--r3);
            padding:var(--s4); box-shadow:0 24px 60px rgba(0,0,0,.6); }
#ov-head { display:flex; align-items:center; gap:var(--s2); flex:0 0 auto; }
#ov-title { font-weight:600; font-size:15px; flex:1; }
#ov-body { flex:1 1 auto; min-height:0; overflow:hidden; }
#ov-close { border:0; background:none; padding:2px 8px; font-size:16px; }

/* fix-input keeps its own look, just re-parented into #ov-body */
#fix-panel { margin-top:0; padding:0; border:0; background:none; }

/* one-line PASS rows inside the checks overlay */
.check.ok { padding:5px 12px; align-items:center; }
.check.ok .body { flex-direction:row; gap:var(--s2); align-items:baseline; }
.check.ok .detail { color:var(--dim); }
```

---

## 5. Per-region overflow policy (the whole point)

Rule for the base page: **hard count caps, not measurement.** No `scrollHeight` polling, no
`requestAnimationFrame` measure-trim loops, no resize listeners. Every cap below is checked
against the §3 budget with slack. Deterministic beats clever; a measure-and-trim loop against
a 3s poll thrashes and depends on constant row heights we would then have to enforce anyway.

### 5.1 Checks — never a wall, ever
- **Base page:** exactly one line in `#dash-header`, forever. N small dots (`#checks-dots`, one
  per result, green or red) + text (`#checks-text`): `All 10 checks pass ▾` / `3 checks failing ▾`.
  Height is identical whether 0 or 10 checks fail. The card list is **never** rendered inline.
- **Overlay (`#doctor` inside `#ov`):** passing checks render as **one line each**
  (`PASS  name    detail`, ~26px). Failing checks render expanded (name + detail + fix + copy, ~72px).
- **Hard cap:** at most **8 failing checks are expanded**; any beyond that render as one-liners
  plus a footer `+N more failing — run \`phone-harness doctor\``. Worst case (10/10 fail):
  8×72 + 2×26 + head 44 + footer 26 = **698px < 82vh (738px)**. No scrollbar.

### 5.2 Activity (`#col-agent`, top half, 346px budget)
- Base: `recs.slice(-10).reverse()` → 10 rows × ~25px = **250px**. Single-line rows (`nowrap` +
  ellipsis) so row height is constant.
- Overflow: `#more-activity` button — `+N earlier ▸` — opens the overlay with `#full-activity`,
  capped at **24 rows** (24×25 + head 44 = 644 < 738).
- Beyond 24: `+N earlier not shown` footer. The full ring is still on disk at
  `.state/agent_activity.log` and at `/api/activity`.

### 5.3 Recent sends (`#col-agent`, bottom half, 346px budget)
- Base: `recs.slice(-6).reverse()` → 6 rows × ~42px (single-line, ellipsised) = **252px**.
- Overflow: `#more-sends` → overlay `#full-sends`, capped at **14 rows** (14×42 + 44 = 632).
  Rows in the overlay wrap (`white-space:normal`) so the full message text is readable there.

### 5.4 Console output (`#col-console`, 354px budget)
- Base: after each `out.prepend(entry)`, trim to **3 entries**
  (`while (out.children.length > 3) out.lastElementChild.remove()`).
  Each entry is CSS-clamped to `max-height:6em` (~96px) → 3×96 = **288px < 354**.
- A parallel unbounded-but-capped copy is appended to `#full-console` (cap **6 entries**),
  reachable via `#more-console` → overlay. 6×96 + 44 = 620 < 738.
- `↑`/`↓` command history (`localStorage sidetap.console.hist`, 50 entries) is untouched —
  it is a separate array and is not affected by output trimming.

### 5.5 Fix-input wizard
- Not a grid cell and not an in-flow row. `#fix-panel` lives inside `#ov-body` and is shown by
  `openOverlay('fix-panel', '🔧 Enable touch input')`. Fixed length: 6 steps × 24 + title +
  body ≈ **224px**. Cannot overflow.

### 5.6 The overlay itself does not scroll either
`#ov-panel` is `overflow:hidden`, not `overflow:auto`. Three of the five designs planned
`max-height:80vh; overflow-y:auto` there, justified as "the user summoned it." The user said
*zero* scrolling; judges 2 and 3 both flagged this as an unresolved question. **Resolved: no
scrollbar anywhere in the document.** Every overlay body has a render cap proven above to fit
82vh at a 900px viewport, with a `+N …` footer when the cap bites.

### 5.7 Chips
Unchanged. `.slice(0,10)` contacts and `.slice(0,12)` apps already fit the §3.3 budget with
~390px to spare. `.chips { max-height:150px; overflow:hidden }` is a pure guardrail.

---

## 6. Failure states

### 6.1 The compound worst case: 10 checks red + both banners + fix wizard open + long console
- **Header:** one line, dots all red, `10 checks failing ▾`. Zero layout shift — the header is
  the same 48px it always was.
- **Banners:** two `flex:0 0 auto` children appear in `#phone-pane`. `#screen-slot` absorbs the
  loss: image drops 634 → 518px tall, 292 → 238px wide. `#side` is untouched (banners live in
  the phone column). Nothing scrolls.
- **Fix wizard:** modal over everything, dim backdrop, 224px tall.
- **Console:** 3 entries, each clamped at 6em.
- **`#dash-grid`:** completely unaffected by any of the above. Column contents never move,
  because nothing failure-related is in the flow of `#side`. This is the structural payoff of
  the design: **failure states cannot reflow the dashboard.**

### 6.2 Auto-open policy (fixes today's line 677)
Today `loadDoctor()` re-expands `#checks-wrap` on *every* failing 5s poll — a panel you cannot
keep closed. New rule:

```
open the checks overlay automatically only when ALL of:
  - fails > 0
  - prevFails === 0            (an ok -> fail transition, not a persisting failure)
  - !checksAutoOpened          (once per page load)
  - !fixRunning                (precedence: the fix wizard always wins the shared overlay)
  - #ov is currently closed
then set checksAutoOpened = true
```
After that, the red dots + red count in the header are the standing signal, and the user
reopens with one click whenever they want. `prevFails` is updated at the end of every
`loadDoctor()` run.

### 6.3 Individual failures
- **LAN exposure:** `#lan-banner` unhidden (existing logic, line 371), plus `#btn-lock-ports`
  in the button row (existing, line 666). Both unchanged.
- **Signature expiry:** `#sig-banner` + `#btn-sig-fix` unhidden (existing, lines 668–671).
  Unchanged. `#col-phone`'s `Input signature` row already mirrors it (line 468).
- **Touch input down:** `#btn-fix` and `#btn-up` become visible (poll-driven, unchanged);
  `#btn-notifs`, `#btn-control`, `#paste-row` hide; `updateActionAvail()` greys the action
  controls with a `title` reason. The **panel** no longer auto-appears — the ambient signal is
  the visible `Fix input` button plus a red dot in the header. This is the one deliberate
  ambient-visibility trade in the spec, and it is what stops the wizard modal re-opening
  itself every 5 seconds.
- **Viewer server unreachable (`loadDoctor` catch):** render **one** synthetic red dot +
  `viewer unreachable` in the header, and one FAIL row in `#doctor`. Do **not** force the
  overlay open — the header is already red and the user may be mid-restart.

---

## 7. JS rewiring — function by function

### 7.1 Delete tabs (lines 389–401)
Remove `showTab()`, the `#tabs .tab` click wiring, and the `localStorage.getItem('sidetap.tab')`
restore block. ~12 lines. The `sidetap.tab` key becomes dead in localStorage; harmless, no
migration needed. Nothing else reads it.

### 7.2 `loadDoctor()` (lines 645–684) — the biggest rewrite, ~35 lines
- Drop the `LABEL` const (`<div class="sec">Checks</div>`); the overlay's `#ov-title` names it.
- Split the card template on `r.ok`:
  - `ok` → `<div class="check ok"><span class="mark">PASS</span><span class="body"><span class="name">…</span><span class="detail">…</span></span></div>` (one line via §4.4 CSS)
  - `!ok` → the existing expanded markup verbatim, including `.fix` and the `.copy-btn`.
- Apply the 8-expanded-failures cap + `+N more failing` footer (§5.1).
- Keep the `.copy-btn` wiring loop and `fixCommand()` **exactly as they are**.
- Keep the `LAN exposure` → `#btn-lock-ports` and `input signature (7-day)` → `#sig-banner`
  logic **exactly as it is** (lines 665–671).
- Replace the `#strip-dot` / `#strip-text` writes with:
  `#checks-dots.innerHTML = results.map(r => '<span class="dot' + (r.ok?'':' bad') + '"></span>').join('')`
  and `#checks-text.textContent = fails ? … : 'All ' + results.length + ' checks pass'`.
  (Dot count is `results.length`, not a hardcoded 10 — judge 3's future-proofing note.)
- Replace line 677's unconditional `checks-wrap.hidden = false` with the §6.2 gate.

### 7.3 Header click (lines 404–407)
`document.getElementById('hdr-checks').onclick = () => openOverlay('doctor', 'Checks');`

### 7.4 `loadActivity()` (lines 434–443)
- Base list: `recs.slice(-10).reverse()` into `#activity-list` (same row template).
- Full list: `recs.slice(-24).reverse()` into `#full-activity`.
- `#more-activity`: `hidden = recs.length <= 10`; text `+${recs.length - 10} earlier ▸`.
- Replace `wrap.hidden = true` on empty with rendering `<div class="dim">No actions yet.</div>` —
  a permanently visible column must not look broken when idle.

### 7.5 `loadSent()` (lines 410–424)
Same shape: base `slice(-6)`, full `slice(-14)`, `#more-sends`, empty state `No sends yet.`
`sendRecs = recs; renderContactChips();` stays first, unchanged.

### 7.6 `runConsole()` (lines 871–896)
After the existing `out.prepend(entry)`:
```js
while (out.children.length > 3) out.lastElementChild.remove();
```
and mirror each entry into `#full-console` (`prepend` a clone; trim to 6). Update
`#more-console` hidden/text. Everything else — busy guard, history, error path — untouched.

### 7.7 `loadStatus()` (line 363) — the poll/modal split
Delete `document.getElementById('fix-panel').hidden = inputEnabled;`.
Keep `btn-fix.hidden = inputEnabled` and `btn-up.hidden = inputEnabled` inside the same
`if (!fixRunning)` guard. Modal open state becomes purely user-driven; a poll tick while a job
runs updates the steps in place (via `pollFix`) but never re-opens a closed modal.

### 7.8 `startFixInput()` (lines 767–774)
Replace `document.getElementById('fix-panel').hidden = false;` with
`openOverlay('fix-panel', '🔧 Enable touch input');`. `renderFix()` and `pollFix()` keep writing
to `#fix-steps` — same id, now inside `#ov-body`, so **zero changes to the wizard's logic**, and
progress keeps advancing even if the user closes the modal.

### 7.9 New: overlay controller (~22 lines)
```js
let ovOpen = null;
const OV_BODIES = ['doctor','fix-panel','full-activity','full-sends','full-console'];
function openOverlay(id, title) {
  OV_BODIES.forEach(b => document.getElementById(b).hidden = b !== id);
  document.getElementById('ov-title').textContent = title;
  document.getElementById('ov').hidden = false;
  ovOpen = id;
}
function closeOverlay() { document.getElementById('ov').hidden = true; ovOpen = null; }
document.getElementById('ov-close').onclick = closeOverlay;
document.getElementById('ov').onclick = (ev) => { if (ev.target.id === 'ov') closeOverlay(); };
```
Exactly one body is ever visible, so the checks/fix collision HUD Cockpit flagged cannot occur.
Precedence is enforced in §6.2's auto-open gate (`!fixRunning`).

### 7.10 Global keydown guard (line 585) — **required, not optional**
The existing handler forwards every printable keystroke to the iPhone. Without a guard, opening
a modal means Escape and any typing get typed onto the phone. Insert as the **first** lines of
the handler, *before* the `if (!inputEnabled) return;` check so Escape works even when input is
down:
```js
if (ovOpen) {
  if (ev.key === 'Escape') { ev.preventDefault(); closeOverlay(); }
  return;
}
```

### 7.11 Nothing else changes
`loadPhone()` keeps writing `#strip-info` (now in the header) and `#phone-rows` (now in
`#col-phone`) — same ids, zero edits. `updateActionAvail()`, `renderContactChips()`,
`renderAppChips()`, `chip()`, `pins()`, `togglePin()`, `renderStop()`, `loadStop()`,
`startStream()`, `poll()`, `showHint()`, `ago()`, `escapeHtml()`, `fixCommand()`, `edgeSwipe()`,
`lockPorts()`, `renderFix()`, `pollFix()`: **untouched**.

---

## 8. Behavior that must NOT change

| Contract | Why it must survive verbatim |
|---|---|
| **STOP kill switch** — `#btn-stop`, `/api/stop` GET poll every 5s, `renderStop()`, `.engaged` class, `#screen-wrap.stopped` border + `STOPPED` badge | Safety surface. The badge is a `::after` on `#screen-wrap`; the wrap keeps its `position:relative` and hugging geometry, so the badge stays centered on the phone. |
| **Origin-guarded `/api/*`** | Every call stays same-origin `fetch` with `Content-Type: application/json`. No new endpoints, no new fetch shapes, no CORS surface added. |
| **Busy guards** — `dataset.busy` on `btn-text-send`, `btn-console-run`, app chips; `btn-unlock`/`btn-lock` disable+relabel | Double-send protection. The 5s poll must never re-enable a button mid-request. |
| **MJPEG stream** — `startStream(port)` sets `screen.src` to `:9100`; `poll()` is the still fallback; `streaming` latch | Re-tuning on session change and the onerror fallback are unchanged. Only `#screen`'s CSS height rule changes; `src`, `onload`, `onerror` do not. |
| **Wheel over the phone** — `wheelAcc` / `wheelBusy` accumulate-and-flush, `{passive:false}` + `preventDefault` | The one intentional "scroll" on the page: it drives the phone. Untouched. |
| **Keyboard forwarding** — `keyBuf`/`keyBusy`, the `input,textarea,button,select,[tabindex]` bail-out, the Ctrl/Meta/Alt bail-out, Enter→`\n`, Backspace→`\b` | Only addition is the `ovOpen` early return (§7.10). No other condition changes. |
| **Tap/swipe mapping** — `getBoundingClientRect()` → normalized → `points.width/height`, 8px move threshold, 0.1–0.5s swipe clamp | Depends on the image never being letterboxed. §3.1 proves the width clamp never binds; if a future change makes it bind, tap mapping breaks silently. Do not add `object-fit` to `#screen`. |
| **Boot-id reload** — `s.boot` mismatch → `location.reload()` | Stale-tab protection after a viewer restart. |
| **Hint hold** — `showHint()` / `hintHoldUntil` 6s | Button feedback must survive the 5s status poll. |
| **Pins + console history in localStorage** — `sidetap.pins.contacts`, `sidetap.pins.apps`, `sidetap.console.hist` | Untouched. Only `sidetap.tab` goes dead. |
| **`[hidden] { display:none !important }`** (line 18) | Load-bearing for the zero-gap banner collapse and for every button whose `display` is set inline. |

---

## 9. Element id ledger

**Added (13):** `screen-slot`, `dash-header`, `hdr-checks`, `checks-dots`, `checks-text`,
`dash-grid`, `ov`, `ov-panel`, `ov-head`, `ov-title`, `ov-body`, `ov-close`,
`full-activity`, `full-sends`, `full-console`, `more-activity`, `more-sends`, `more-console`
*(18 total; all new and unique)*.

**Removed (6):** `strip`, `strip-main`, `strip-dot`, `strip-text`, `checks-wrap`, `tabs`.

**Renamed (4):** `tab-actions`→`col-actions`, `tab-agent`→`col-agent`, `tab-phone`→`col-phone`,
`tab-console`→`col-console` (class `tabpane` → `col`; drop their `hidden` attributes).

**Moved, id unchanged (5):** `doctor` and `fix-panel` → inside `#ov-body`; `strip-info` → inside
`#dash-header`; `phone-rows` → inside `#col-phone`; `activity`/`sent` → inside `#col-agent`.

**Unchanged (everything else):** `phone-pane`, `screen`, `screen-wrap`, `tap-dot`, `controls`,
`buttons`, `btn-stop`, `btn-home`, `btn-unlock`, `btn-lock`, `btn-notifs`, `btn-control`,
`btn-shot`, `btn-refresh`, `btn-up`, `btn-fix`, `btn-lock-ports`, `btn-lan-lock`, `btn-sig-fix`,
`paste-row`, `paste-text`, `btn-paste-send`, `click-hint`, `lan-banner`, `sig-banner`,
`sig-banner-text`, `side`, `fix-title`, `fix-body`, `fix-steps`, `activity-list`, `sent-list`,
`contact-chips`, `text-to`, `text-row`, `text-msg`, `btn-text-send`, `app-chips`, `console-row`,
`console-in`, `btn-console-run`, `console-out`, `console-hint`.

No duplicates. `tests/test_viewer.py:203` passes by construction.

---

## 10. Implementation checklist — the page works after every step

Run `python launch.py` once at the start and keep the tab open; hard-reload after each step.

- [ ] **1. Add the overlay shell, empty and hidden.** Insert `#ov` / `#ov-panel` / `#ov-head` /
      `#ov-title` / `#ov-close` / `#ov-body` markup just before `</body>`, plus §4.4 CSS and the
      §7.9 controller. Nothing references it yet. *Verify:* page identical to before.
- [ ] **2. Fix the phone pane sizing (steal #1).** Wrap `#screen-wrap` in `#screen-slot`, apply
      §4.1, delete `height:min(100vh - 260px, 1180px)` and `flex-shrink:0`.
      *Verify (the highest-risk edit):* phone renders full height with no crop; tap a known UI
      element and confirm it lands; toggle `#lan-banner` and `#sig-banner` visible in DevTools
      one at a time and both at once — the phone shrinks, the pane never scrolls; check 1600×900,
      1920×1080, and one tall window.
- [ ] **3. Move `#doctor` and `#fix-panel` into `#ov-body`** (both `hidden`). Point `#strip-main`'s
      click at `openOverlay('doctor','Checks')` for now. *Verify:* clicking the status strip opens
      the checks modal; Escape/backdrop/× close it; the fix wizard still starts (it will open the
      modal after step 7 — for now it just runs).
- [ ] **4. Add the keydown guard (§7.10).** *Verify:* with input live, open the modal and type —
      nothing reaches the phone; Escape closes it.
- [ ] **5. Rewrite `loadDoctor()` (§7.2)** — one-line PASS rows, 8-fail expansion cap, `+N more`
      footer, the §6.2 auto-open gate. Header still uses the old `#strip-*` ids at this point.
      *Verify:* with real (green) data the modal shows 10 one-liners; with a forced failure
      (temporarily rename a check to fail, or stop the tunnel) the modal auto-opens once, and
      stays closed after you close it across several 5s polls.
- [ ] **6. Replace `#strip` with `#dash-header`.** New markup + §4.2 header CSS; move `#strip-info`
      in; retarget `loadDoctor`'s dot/text writes and the click handler to `#hdr-checks`.
      Delete `#strip*` ids and `#checks-wrap`. *Verify:* one header line, N dots, correct count,
      glance bits right-aligned, click opens the modal.
- [ ] **7. Route the fix wizard through the overlay (§7.7 + §7.8).** Delete line 363; change
      `startFixInput` to `openOverlay('fix-panel', …)`. *Verify:* with input down, the `Fix input`
      button is visible and the panel is **not** forced open; clicking it opens the modal and the
      6 steps advance; closing mid-job does not cancel it and the modal does not re-open itself.
- [ ] **8. Kill the tabs, build the grid.** Rename the four panes to `#col-*` with `class="col"`,
      drop their `hidden` attributes, wrap them in `#dash-grid`, apply §4.2/§4.3 CSS, delete
      `showTab()` and its wiring (§7.1). *Verify:* all four columns visible at once; `#side` has
      no scrollbar; the body has no scrollbar.
- [ ] **9. Cap activity and sends (§7.4, §7.5)** + `#more-activity` / `#more-sends` +
      `#full-activity` / `#full-sends` in `#ov-body`. *Verify:* drive the phone ~30 actions;
      base list stays 10 rows with no scrollbar; `+N earlier` opens the modal with up to 24;
      empty state reads `No actions yet.`
- [ ] **10. Cap console output (§7.6)** + `#more-console` / `#full-console`. *Verify:* run 6
      console commands including one long `ocr()`; the column shows 3 clamped entries, no
      scrollbar; `↑` history still recalls all 6.
- [ ] **11. Adversarial render pass at 1600×900.** Force simultaneously: both banners visible,
      all doctor checks failing (stub `/api/doctor` in DevTools), the fix modal open, 6 console
      entries, 30 activity rows. *Verify:* `document.scrollingElement.scrollHeight ===
      innerHeight`, and `[...document.querySelectorAll('*')].filter(e => e.scrollHeight >
      e.clientHeight + 1)` returns an empty array.
- [ ] **12. `python -m pytest tests -q`** — id-uniqueness gate and the rest of the suite.
- [ ] **13. Update `CLAUDE.md`'s viewer bullet** to describe the dashboard grid, the one-line
      checks header, the overlay, and the hard caps (docs change ships with the code change).

---

## 11. Open questions — all resolved, none left for the implementer

| Question the designs left open | Resolution |
|---|---|
| Does the checks overlay scroll? | **No.** Nothing in the document scrolls. 8-expanded-fail cap proves 698px < 738px. §5.6 |
| Hard count caps or measure-and-trim? | **Hard counts.** 10 / 6 / 3 base, 24 / 14 / 6 in overlays. No rAF, no resize listener. §5 |
| Tighten chip caps to 6/8? | **No.** 10/12 fit with 390px of slack. Two fewer JS edits. §3.3 |
| One overlay or five? | **One shell, five hidden bodies.** No content duplication, no drift, no z-index stacking. §7.9 |
| Checks/fix overlay collision? | **Fix wizard wins.** Auto-open is gated on `!fixRunning`. §6.2 |
| Does the widened `#side` shrink the phone? | **No.** The image is height-limited by its 0.4603 aspect. §1.2, §3.1 |
| Grid or flex for `#phone-pane`? | **Flex.** Grid `gap` still applies between tracks holding a `display:none` item, wasting 20px. §3.2 |
| Full-width banner row? | **Declined.** Steal #1 removes the coupling that motivated it; a body-grid rewrite buys nothing. §1.1 |
| Losing the always-visible fix-input panel? | **Accepted trade,** and required to stop the 5s re-open. Ambient signal = visible `Fix input` button + red header dot. §6.3 |
| Older activity becomes unbrowsable? | **No** — one click to 24 rows in the overlay; the full ring stays at `/api/activity` and `.state/agent_activity.log`. §5.2 |
