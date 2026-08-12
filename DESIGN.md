---
name: SideTap
description: Instrument-panel dark UI for an iPhone driven by an AI agent, with a kill switch always in reach.
colors:
  bg: "#101418"
  surface: "#1a2129"
  surface-2: "#222b36"
  line: "#313b46"
  ink: "#e8edf2"
  ink-2: "#c4cdd6"
  ink-3: "#8a97a5"
  signal-blue: "#4493f8"
  pass-green: "#3fb950"
  stop-red: "#f85149"
  caution-amber: "#d29922"
typography:
  display:
    fontFamily: "Inter, system-ui, -apple-system, sans-serif"
    fontSize: "clamp(30px, 4.6vw, 46px)"
    fontWeight: 800
    lineHeight: 1.12
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Inter, system-ui, -apple-system, sans-serif"
    fontSize: "clamp(24px, 3vw, 32px)"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, 'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Inter, 'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.1em"
  mono:
    fontFamily: "'JetBrains Mono', 'Cascadia Mono', Consolas, ui-monospace, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "7px"
  md: "10px"
  lg: "14px"
  pill: "99px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "8px 13px"
    typography: "{typography.body}"
  button-default-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
  button-primary:
    backgroundColor: "{colors.signal-blue}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "11px 20px"
  button-stop:
    backgroundColor: "transparent"
    textColor: "{colors.stop-red}"
    rounded: "{rounded.sm}"
    padding: "11px 16px"
  button-stop-engaged:
    backgroundColor: "{colors.stop-red}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "11px 16px"
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "5px 11px"
  input-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
  card-console:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "{spacing.md}"
  card-marketing:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "22px"
---

# Design System: SideTap

## 1. Overview

**Creative North Star: "The Glass Cockpit"**

SideTap's visual system reads as an instrument panel, not app chrome. A near-black base holds flat, bordered graphite panels; state is reported through exactly four functional colors and never through decoration; the one control that must never be missed (STOP) gets the one loud, saturated, unmistakable treatment on the page. That is the same grammar a cockpit, a control room, or a CI dashboard uses: calm and dark at rest, precise about what "green," "amber," and "red" each mean, and built so the operator's eye finds the emergency control without hunting for it.

The system explicitly rejects generic AI-tool marketing (hero plus three cards plus a gradient blob), enterprise-dashboard density (wall-to-wall data tables and compliance chrome), and cutesy consumer-app playfulness (mascots, bright multi-hue palettes). See PRODUCT.md's Anti-references; none of those read as an instrument worth trusting with a real phone.

The same token values and color grammar run through both shipped surfaces, `viewer.html` and `site/index.html`. What changes by register is density, motion, and scale: tight and fixed on the operator console, generous and fluid on the landing page, because the console's user is mid-task and the landing page's user is being persuaded. Documented per surface below wherever the two diverge.

**Key characteristics:**
- Dark, near-black base (#101418) with two graphite panel layers, so color-coded state reads instantly against a neutral field.
- Flat and bordered by default. Shadow is rationed to exactly two jobs (lifting the one dominant panel on screen, signaling that STOP is engaged) and never used for ordinary cards or buttons.
- One sans (Inter) for everything meant to be read, one mono (JetBrains Mono) for anything that is data, a command, or standing in for a terminal.
- Four functional accent colors, each with exactly one meaning, never chosen for variety.
- Status is always stated in words, never in color alone (PASS/FAIL, STOP/RESUME, sent/pending).

## 2. Colors: The Cockpit Palette

The palette is a near-black instrument base with four functional accents. Each accent means exactly one thing and nothing else.

**Judged, not assumed: the Primer question.** All four accents (`#4493f8`, `#3fb950`, `#f85149`, `#d29922`) are, near verbatim, GitHub Primer's dark-theme accent, success, danger, and attention colors. That is a real observation, not a coincidence, and it earns an honest verdict rather than a reflex reskin.

- **On the operator console (product), keep it exactly as it is.** SideTap's whole audience already has these four meanings wired into muscle memory from CI dashboards, GitHub Actions, and terminal tooling: green passed, red is danger, amber needs a look, blue is an action you can take. Borrowing that vocabulary speeds recognition under the exact time pressure the viewer is built for (PRODUCT.md Design Principle 1: STOP outranks everything). Trading that recognition speed for differentiation would be a bad deal on a page whose entire job is finding the right control in under a second.
- **On the landing page (brand), there is a real case for a small, deliberate difference.** The brand register's job includes being memorable to a stranger who has never used GitHub Actions, and reading as "default GitHub dark mode" is a genuine dent in distinctiveness for a marketing surface trying to be remembered. The page is not broken today; it reads as competent developer-tool marketing. But a future colorize pass introducing one small, intentional departure on the brand surface only (a distinct hue for the primary CTA, for instance) is worth exploring. That decision belongs to a later command, not to this document.

### Primary
- **Signal Blue** (#4493f8): the only color used for anything actionable right now: primary links, the waitlist button, focus rings, the phone's tap dot, and the "an action is in flight" border on the phone frame during unlock, open, or send. Never decorative.

### Secondary
- **Pass Green** (#3fb950): passing or ok state only: doctor PASS rows, "sent" confirmations, the proof-strip checkmarks, the status dots in the landing page's demo frame bar.

### Tertiary
- **Stop Red** (#f85149): danger and the kill switch, nothing softer. STOP at rest and engaged, the stopped-phone-frame glow, the LAN-exposure banner (WDA has no auth, a real risk rather than a caution), the "kill switch" feature card. Reserved this narrowly on purpose: red never means "you have a configuration problem," only "stop, or something is actively unsafe."

### Accent, Caution (operator console only)
- **Caution Amber** (#d29922): needs attention but not urgent: the signature-expiry banner, and, notably, doctor **FAIL** rows, which use amber rather than red. A failing check means "go fix this," not an emergency; red stays reserved for STOP and live exposure risk. Amber does not appear in `site/index.html` today; the landing page's simpler, proof-first content never needed a caution state.

### Neutral
- **Void** (#101418, token `--bg`): page background on both surfaces, plus a faint radial vignette on the viewer only (`radial-gradient(1100px 700px at 26% -10%, #151c24 0%, transparent 70%)`).
- **Panel** (#1a2129, token `--panel` on the viewer / `--surface` on the landing page): the base surface for every card, button, input, and section, the first layer up from Void.
- **Panel Raised** (#222b36, token `--panel2` / `--surface2`): the hover state for panel-colored elements, and the base for the second neutral layer product register calls for on toolbars and side panels.
- **Line** (#313b46): the only border color anywhere in the system. 1px, always.
- **Ink** (#e8edf2, token `--text` / `--ink`): primary reading text.
- **Ink Soft** (#c4cdd6, token `--text2` / `--ink2`): secondary text: labels, sub-copy, button text at rest.
- **Ink Dim** (#8a97a5, token `--dim` / `--ink3`): tertiary text: timestamps, section labels, placeholder-weight copy, the least important word on a line.

### Named Rules
**The One Meaning Rule.** Every accent color maps to exactly one state, everywhere it appears: blue is "act now," green is "passing," red is "stop or danger," amber is "needs attention." No accent is ever chosen for variety or decoration. A new UI element that needs a fifth meaning needs a fifth color; do not overload one of the four.

## 3. Typography

**Display Font:** Inter (with `system-ui, -apple-system, sans-serif` on the landing page; `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif` on the viewer, since the viewer is a local Windows app and does not load Google Fonts).
**Body Font:** Inter, the same family, no second face.
**Label/Mono Font:** JetBrains Mono (`"Cascadia Mono", Consolas, ui-monospace, monospace` fallback on the viewer; `ui-monospace, Consolas, monospace` on the landing page).

**Character:** One family carries every role on both surfaces: headlines, body, buttons, and data. That matches product-register convention ("one family is often right") even on the brand surface, where two families would be typical. Inter reads as competent and neutral rather than as a voice choice; the system's personality comes from color discipline and copy, not from type character.

### Hierarchy

- **Display** (weight 800, `clamp(30px, 4.6vw, 46px)`, line-height 1.12, letter-spacing -0.03em): the landing page's hero hook only (`.hook`), "My PC just texted my mom." Fluid scale with a ratio above 1.25 to the next step down, matching brand-register convention. Never appears on the viewer.
- **Headline** (weight 800, `clamp(24px, 3vw, 32px)`, line-height 1.2, letter-spacing -0.02em): landing-page section headings (`h2`): "One cable. Four hops.", "Built for agents. Supervised by you."
- **Title** (weight 600, 17px, letter-spacing -0.015em): the viewer's single page title (`h1`, "SideTap") and the landing page's card and tier sub-headings (`.card h3`, `.tier h3`, `.node h3`), which sit at slightly varying weight (600 to 800) but the same rough size. Fixed px, not fluid, matching product-register convention.
- **Body** (weight 400, 14px, line-height 1.55): the viewer's default reading size for anything not explicitly a label. The landing page runs body text larger, `.sub` at 17.5px and `.lead` at 16.5px, because a marketing page optimizes for a comfortable single pass, not density; the console optimizes for fitting more real information on one screen.
- **Label** (weight 600, 11px, letter-spacing 0.1em, uppercase, Ink Dim): the viewer's `.sec` section headers ("Text someone," "Activity," "Recent sends"). Small, quiet, always dim-colored; labels never compete with the content they introduce.
- **Mono / data** (weight 400, 12 to 13px): JetBrains Mono is used two ways that should stay distinct. **Functional**: activity-feed timestamps, console output, doctor `code` snippets, install terminal blocks, all genuine data or commands. **Stylistic**: the landing page's `.eyebrow` kickers and proof-strip labels, where mono is worn as a "developer tool" costume rather than displaying real data. Both uses are legitimate today. Keep the functional use free of decoration creep (do not style real data to look prettier at the cost of scanability) and keep the stylistic use rare (see the flag on `.eyebrow` below).

### Named Rules
**The One Family Rule.** Inter carries every weight and every role on both surfaces. No second display face is introduced anywhere in the system, including the brand-register landing page, where a second family would be typical. This is a deliberate identity choice already shipped; preserve it.

**Flagged, not resolved: the eyebrow kicker.** `.eyebrow` (JetBrains Mono, 12.5px, uppercase, letter-spacing 0.14em, Signal Blue, 14px bottom margin) appears identically above all four of the landing page's non-hero sections: How it works, What you get, Install, and SideTap Pro. That is every section on the page below the fold, using the same label, unvaried. Brand register's own ban list names this shape directly: "repeated tiny uppercase tracked labels above every section heading... is AI scaffolding unless it's a deliberate, named brand system." Documented here as it currently ships. Whether it earns "deliberate, named brand system" status or should be varied or removed is a judgment call for a future critique or polish pass, not resolved by this document.

## 4. Elevation

The system is flat and bordered by default. Nearly everything (buttons, chips, inputs, `.col` panels, `.card`, `.check` rows, `.sent-row`) is a solid panel color with a single 1px Line border and no shadow at all. Where the system needs depth, it comes from a 3px colored left border, not from elevation; see Components. Shadow is rationed to exactly two jobs and used nowhere else.

### Shadow Vocabulary
- **Frame Lift** (`box-shadow: 0 0 0 1px #39434f, 0 26px 60px rgba(0,0,0,.55)` on the viewer's phone bezel; `0 24px 60px rgba(0,0,0,.45)` on the landing page's demo frame; `0 24px 60px rgba(0,0,0,.6)` on the checks overlay panel): marks the one dominant, single-focus object on a screen. Used on exactly one element per view, the phone itself, the demo frame, or an open overlay, never stacked and never applied to an ordinary card.
- **Stop Glow** (`box-shadow: 0 0 0 1px var(--bad), 0 0 42px rgba(248,81,73,.28)` on the stopped phone frame; `box-shadow: 0 0 0 3px rgba(248,81,73,.22)` on the engaged STOP button): a colored ring, not a depth cue. It exists purely to say the kill switch is on, and it is always red.

### Named Rules
**The State-Not-Depth Rule.** A shadow in this system either lifts the single dominant panel on screen or announces that STOP is engaged, never anything else. Ordinary cards, buttons, chips, and list rows stay flat and are differentiated by a colored left border, not by elevation. A new component that needs to feel important should reach for Frame Lift's exact values, not a new shadow.

## 5. Components

### Buttons
- **Shape:** 7px radius (viewer token `--r1`), 1px Line border, no shadow.
- **Default (viewer):** Panel background, Ink Soft text, weight 500 at 13px, padding 8px 13px. Hover moves to Panel Raised background plus full Ink text over a 120ms ease transition (token `--t`); active presses down 1px (`translateY(1px)`); disabled drops to 45% opacity. Landing-page buttons and links use the same shape family (7 to 8px radius, bordered or filled) but snap on hover with no eased transition; `filter: brightness(1.1 to 1.12)` applies instantly. The two surfaces do not share a motion token today.
- **Primary (landing page only, `.wl button`, "Join the waitlist"):** solid Signal Blue fill, white text, weight 600, 8px radius, padding 11px 20px.
- **Ghost / ambient:** the viewer's blue-tinted "Fix input" button (`#182838` background, blue border, `#cfe3ff` text, deepening on hover) and the landing page's `.clone` / `.termbox header` copy buttons (transparent, blue text only). Both read as a lower-emphasis action, not a primary call to action.
- **Signature, STOP / kill switch:** documented on its own below, because it is the most important control on the viewer and appears in matching visual language on the landing page.

### STOP / Kill Switch (signature)
The one control the whole system is built around (PRODUCT.md Design Principle 1). Same grammar on both surfaces.
- **At rest:** transparent background, Stop Red border and text, bold, uppercase, letter-spaced (0.1em on the viewer, 0.08em on the landing page). On the viewer it spans the full width of the control column, the widest, most prominent button on the page.
- **Engaged:** solid Stop Red fill, white text, plus a red glow ring (`0 0 0 3px rgba(248,81,73,.22)`). The phone frame itself grows a matching red border and glow, and a floating "STOPPED" pill appears centered over it, so the state is visible even when not looking directly at the button.
- **Landing-page demo:** the same button, genuinely wired up. Clicking it pauses the demo video and the simulated activity feed and prints "STOPPED, every agent action blocked" into the feed, mirroring the real product's behavior exactly. This is proof rather than a claim (PRODUCT.md Design Principle 2), built directly into the component.
- **Never:** smaller than the busiest button on the page, any color but red, more than one click away, or positioned below content that could push it out of the initial view.

### Chips (viewer only)
- **Style:** pill shape (`border-radius: 99px`), Panel background, Ink Soft text, 12.5px, padding 5px 11px.
- **State:** a small pin or star glyph sits inside every chip (unpinned versus pinned); pinned chips persist to `localStorage` and always sort first. Two chip rows exist, recent contacts learned from send history and known apps, each capped at 10 to 12 visible.

### Cards / Containers
- **Corner style:** 14px radius on the viewer's dashboard panels (token `--r3`, `.col`); 10px on the viewer's doctor and check rows and on the landing page's feature cards; 12px on the landing page's single "hero" panels (`.frame`, `.tier`). The landing page hardcodes these values rather than sharing the viewer's `--r1` / `--r2` / `--r3` tokens, but the effective scale (roughly 7 to 8px, 10px, 12 to 14px) is the same family.
- **Background:** Panel, flat, no shadow (see Elevation), except the one Frame Lift panel per screen.
- **Border:** 1px Line everywhere. Status-bearing rows (`.check`, `.sent-row`) add a 3px colored left border instead of a shadow: green for ok or passing, amber for a fixable fail or a pending send, never red (red stays reserved for STOP and danger; see the Colors Named Rule).
- **Internal padding:** 16px on the viewer's dashboard panels (token `--s3`), 22 to 28px on the landing page's feature cards and pricing tiers. Generous on brand, tighter on product.

### Inputs / Fields
- **Style:** Panel background, 1px Line border, 7px radius on the viewer (8px on the landing page's waitlist email field), padding 7px 10px, inherits the body font except the console input, which uses mono.
- **Focus:** a 2px Signal Blue outline offset 1 to 2px from the field, an outline ring rather than a border-color shift, so focus is visible without changing the field's resting shape.
- **Error / disabled:** no dedicated error or invalid style exists on either surface today. Disabled controls (buttons only) drop to 45% opacity with `cursor: not-allowed`.

### Navigation
- **Landing page:** a real nav bar, sticky at the top, 58px tall, a near-opaque dark background (`rgba(16,20,24,.94)`, not a blurred glass panel) over a 1px Line bottom border, an SVG logo mark plus wordmark on the left, right-aligned text links with one (GitHub) picked out in Signal Blue.
- **Viewer:** no navigation menu. `#dash-header` is a slim status strip (title, a compact PASS/FAIL dot cluster, battery, lock, and app glance), not a nav, because the whole page is one screen with nowhere else to navigate. Every deeper view opens in the single shared overlay instead of a new route.

### Banner (signature)
A loud, full-width, bordered strip reserved for state that needs attention right now without opening anything: the LAN-exposure warning (Stop Red border and background, "phone control ports are open to your network") and the signature-expiry countdown (Caution Amber). Both carry their own inline fix button (Lock ports / Fix input), so the fix is one click from the warning, never a separate hunt.

### Doctor Check Row (signature)
The `phone-harness doctor` result, rendered as a row: a bold PASS or FAIL mark, the check's name, a dim one-line detail, and, for fails only, an amber fix line with an optional copy button that copies the exact runnable command. Rows that would overflow the panel degrade to a compact one-liner (fails first, capped at 5 expanded plus 12 compact) rather than ever clipping content invisibly. This is a real, tested behavior (`degradeChecksToFit`), not just a CSS guess.

### Approval Card (signature)
The prompt-injection gate's UI: a Stop-Red-bordered card naming the contact, the exact message text, and why it is being asked ("It read your phone before asking to send this"), plus any heuristic flags, with Deny and Approve buttons of equal width. It appears inline in the side panel and also force-opens the shared overlay once per request; the one place in the system where a modal is used as a first resort, because the decision is genuinely blocking and irreversible once approved.

## 6. Do's and Don'ts

### Do:
- **Do** keep STOP the fastest control to find on the viewer: full width or the widest button in its row, red at rest, solid red plus a glow ring when engaged, never more than one click away. This overrides every other stylistic preference on the page.
- **Do** state every status in words as well as color: PASS/FAIL, STOP/RESUME, "sent" versus "attempted." Never ship a status that reads by color alone.
- **Do** keep the flat, 1px-bordered, no-shadow panel as the default for every ordinary card, button, chip, and input. Reserve shadow for Frame Lift (one dominant panel per screen) and Stop Glow (STOP is engaged) only.
- **Do** hold one meaning per accent color: Signal Blue for "act now," Pass Green for "passing," Stop Red for "stop or danger" only, Caution Amber for "needs attention" (console only). Do not reach for a fifth meaning without a fifth color.
- **Do** keep JetBrains Mono for genuine data, commands, and terminal-style output; keep Inter for anything meant to be read as prose.
- **Do** let the two registers keep their own scale logic: fixed px and a tight ratio on the operator console (product convention), fluid `clamp()` with a ratio of 1.25 or higher between steps on the landing page (brand convention). Both are already doing this correctly.

### Don't:
- **Don't** add a decorative colored border stripe, gradient text, or a blurred or glassmorphic panel. None exist today, and impeccable bans all three outright regardless of register.
- **Don't** add a hero-metric template, an identical repeating card grid, or numbered section scaffolding to either surface.
- **Don't** let text overflow its container at any breakpoint. Both files already guard this explicitly (`overflow-wrap: anywhere`, `text-overflow: ellipsis`, `-webkit-line-clamp`, and the checks panel's render-once `degradeChecksToFit` pass). Preserve the pattern rather than reintroducing a bare count cap that can silently clip content, a real regression the code's own comments call out by name.
- **Don't** swap the four-color palette for "differentiation" on the operator console. The borrowed, already-automatic GitHub and CI color grammar (green passed, red stop, amber caution, blue act) is a deliberate legibility choice for an operator under time pressure, not an unconsidered default; see the judgment in Colors.
- **Don't** treat the repeated `.eyebrow` kicker as settled section grammar without a look. It currently sits above all four non-hero landing-page sections, identically, which is the exact shape brand register's own ban list names: "repeating [a kicker] as section grammar is AI scaffolding unless it's a deliberate, named brand system." Flagged for a future critique or polish pass, not resolved here.
- **Don't** introduce a second display typeface on either surface. Inter already carries every role, including the landing page's hero, a deliberate, already-shipped identity choice.
