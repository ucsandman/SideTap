# Product

## Register

**product** (viewer) / **brand** (landing page). This is a dual-register repo: two shipped surfaces, two registers, split by which file is being worked on.

- `src/phone_harness/viewer.html` is the operator console. Register: **product**. Design serves the product; the page exists to be trusted and fast, not admired.
- `site/index.html` is the public landing page at sidetap.io. Register: **brand**. Design IS the product; a stranger's whole impression of SideTap forms on this one page.

Default register when a command does not name a surface: **product**. The viewer is open for every session; the landing page changes rarely by comparison.

## Users

**The operator** is a solo developer running SideTap against their own real iPhone, on their own Windows PC, with no Mac and no paid Apple developer account. They are not moving through a sales funnel and they are not an IT admin managing a device fleet. They are one person who just told an LLM agent to do something on their phone (read a text, open an app, send a reply) and now has to watch it happen.

- **On the viewer**, the operator is mid-session with divided attention: half watching a live phone screen, half doing something else, until the moment the agent does something wrong. At that moment their entire job becomes hitting STOP before the next action lands. Supervision with a fast exit is the primary task every time this screen is open, not configuration or exploration.
- **On the landing page**, the visitor is a developer deciding whether to trust an unfamiliar tool with control of a real phone. They arrive skeptical of a big claim (drive a real iPhone from Windows, no Mac needed) and scan for proof this is not vaporware or a security risk, within the first few seconds. The primary task on this page is a fast decision: clone it, join the Pro waitlist, or leave.

## Product Purpose

SideTap lets an LLM agent see and control a real iPhone over USB from a Windows desktop, using go-ios and WebDriverAgent, with no Mac, no Xcode, no jailbreak, and no paid Apple developer account required to start. It exists because every other route to iPhone automation assumes macOS, which locks out the much larger population of Windows-only developers who want to build or run phone-driving agents.

Success looks different per surface:

- **Viewer:** an agent completes a real task on the operator's phone while the operator watches the live screen the whole time, and can stop every further action in well under a second if anything looks wrong, without needing to think about how.
- **Landing page:** a visitor who has never heard of SideTap understands what it does within one screen, believes it is real and safe, and either clones the repo or leaves an email for Pro.

## Brand Personality

Three words, held across both surfaces: **direct, capable, watchful.** The voice never oversells ("no Mac, no jailbreak, no Appium server" states facts and lets them persuade on their own) and it never hides what the product cannot do (the README's Security section names exactly what the prompt-injection gate does not cover, in the same breath as what it does).

- **Viewer (product):** a calm-under-pressure instrument panel. The emotional target is confidence, not delight. The operator should read the state of the whole system at a glance and trust that the one control that matters, STOP, sits exactly where expected, every time. Familiarity is a feature here: this should feel like the best tool in its category (a CI dashboard, a device-farm console), not like a novel interface to learn under pressure.
- **Landing page (brand):** confident and a little cheeky, backed by receipts. The hook line, "My PC just texted my mom. From my real iPhone.", sets the tone: concrete, slightly funny, and immediately disprovable if it were not true, which is what makes it read as proof rather than a claim. The page earns trust fast for a tool about to touch someone's real device by showing (a live, clickable STOP button; a proof strip of what you do not need) instead of telling.

## Anti-references

What SideTap should not look or feel like, on either surface:

- **Generic AI-tool SaaS marketing.** Hero, three feature cards, testimonial carousel, gradient-blob background. This is the exact template a skeptical developer is already primed to distrust, and trust is the whole job of the landing page for a tool that drives someone's real phone.
- **Enterprise security-vendor dashboard.** Dense data tables, compliance-badge walls, jargon-heavy chrome. SideTap is a personal tool run by one person for their own device, not fleet-management software for an IT department.
- **Cutesy consumer-app playfulness.** Mascots, bright multi-hue palettes, bubble type. This undercuts a tool whose signature feature is a kill switch; the product needs to be taken seriously in the one second it might matter.
- **Anything that makes STOP slower to find.** Less a style anti-reference than a hard rule: no redesign of the viewer may shrink the STOP control, move it below the fold, recolor it away from red, or add a click in front of it. See Design Principles.

## Design Principles

1. **STOP outranks every other design decision.** On the viewer, if a choice trades STOP's speed or visibility for anything else (density, aesthetics, a cleaner grid), the choice loses. Every other principle here is subordinate to this one.
2. **Show receipts, not claims.** Both surfaces prove instead of assert: a live activity feed instead of "trust us," a doctor panel that names the exact fix instead of a vague error, a landing-page STOP button a stranger can actually click before installing anything.
3. **The operator outranks the agent, visibly.** Every viewer decision assumes a human is one click from overriding whatever the agent just did. Approval cards, the kill switch, and the activity log are the point of the page, not decoration around some other point.
4. **Untrusted by default, and the interface says so.** The product's threat model is prompt injection: anyone who can text the operator can put words in the agent's input. The design distinguishes automated-and-safe from automated-and-needs-a-human-look inside the interface itself (approval cards, the Approve-sends gate), not only in a README.
5. **Plain speech, on purpose.** Copy states what happens in the fewest, most concrete words available (house style bans marketing buzzwords and em dashes outright). This is a brand-personality choice as much as a copy rule: SideTap's credibility comes from sounding like an engineer who will tell the truth, not a marketer who will not.

## Accessibility & Inclusion

No formal WCAG target has been set. The baseline below is what the current code already does; preserve and extend it rather than weakening it:

- **Never color alone.** Every status the interface communicates in color also ships as text: PASS/FAIL, STOP/RESUME, "sent" vs. "attempted," the amber note next to the Approve-sends gate when it is off Always. Keep this pairing for any new status the design adds.
- **`:focus-visible` is already defined** on both surfaces (buttons, inputs, links) with a visible 2px accent-colored outline. Any new interactive element needs the same treatment; do not ship a focusable control with the outline suppressed.
- **`prefers-reduced-motion` is already respected on the landing page.** All animation and transition, plus smooth scroll, are disabled under that media query, and the STOP demo pauses its own feed animation when engaged. The viewer has almost no decorative motion to begin with (120ms state transitions only); keep it that way rather than adding entrance choreography that would need its own reduced-motion guard.
- **Keyboard operability exists for the core flows.** Typing on the physical keyboard drives phone text input, the checks overlay closes on Escape, and a guard keeps overlay keystrokes from leaking through to the phone. Preserve this when adding new controls.
- **Known gap, not addressed by this document:** neither surface has had an explicit screen-reader pass (no skip link, minimal `aria-live` use; the landing page's live feed is explicitly `aria-live="off"`). Flagged for a future accessibility audit; out of scope for this documentation pass.
