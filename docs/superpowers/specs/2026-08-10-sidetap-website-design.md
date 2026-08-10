# sidetap.io website + monetization — design

Date: 2026-08-10. Author: Claude (Fable), from the `sidetap-marketing-reshoot` handoff.

## Goal

A public website at **sidetap.io** that converts visitors into (a) GitHub installs and
(b) a validated paid tier, reusing the re-shot marketing assets in `docs/media` and the
postkit positioning ("My PC just texted my mom. From my real iPhone. No Mac. No
jailbreak. Kill switch included.").

## Decision 1 — where the site lives

**Chosen: this repo, `site/` folder, deployed to Vercel.**

- The site is a marketing surface of the product. One repo keeps README, docs, and site
  copy in sync; a separate repo adds maintenance for zero benefit.
- Media policy holds: only small web assets are committed (`og.mp4` 458KB hero loop, one
  ~4MB web-optimized demo clip, `og.png`). The 14–31MB videos stay local; the full
  launch video links out to YouTube once posted.
- Rejected: `docs/` GitHub Pages (no serverless function for the waitlist, weaker
  domain/SSL story); separate repo (splits history, breaks the "one repo to maintain"
  rule).

## Decision 2 — monetization shape

**Chosen: open-core + "sidetap Pro", validated with a waitlist before any billing.**

- Rejected: **hosted service** — the product is inherently local (USB, your PC, your
  phone); hosting inverts its whole trust story. **Server-side signing** — automating
  Apple sign-in server-side skirts Apple ToS and holds user credentials; hard no.
  **Sponsorware alone** — keeps goodwill, earns approximately nothing.
- The wedge is the sharpest recurring pain: **free Apple ID signatures die every 7
  days**. Revised 2026-08-10 after a ToS review: automating the free-ID renewal
  (AltStore-style Apple-auth emulation) is gray-to-violating under the Apple
  developer agreement, can be broken by Apple at any time, and means handling
  customer Apple credentials. Not a paid product. Pro instead sells the honest
  fixes: **multi-device** (2+ iPhones from one PC, purely local, no ToS exposure),
  **guided 1-year signing** using the customer's own $99/yr Apple developer
  account, and priority support. Price hypothesis **$49/yr**.
- Validation first: the site ships a **waitlist** (email capture → Resend →
  wes@practicalsystems.io). Stripe products/prices are created only after explicit
  approval, once the waitlist shows demand. This respects the billing-approval rule
  and avoids building payment infra for an unvalidated tier.

## Decision 3 — push before starting

**Chosen: push immediately (done).** The public repo README already referenced the
re-shot `readme.gif`; holding the commits kept the public surface stale. Both repos
were secret-scanned (prose-only matches) and pushed: sidetap `d4e6e3b..fae7754`,
animations `249c66f..62b80a3`.

## Site structure (single page + one function)

- `site/index.html` — hero (og.mp4 loop, postkit hook line), how-it-works diagram,
  features (real UI tree, live viewer, kill switch, MCP tools, doctor), install
  (copy-paste agent prompt + quick start), Pro section with waitlist form, footer
  (GitHub, MIT, Practical Systems).
- `site/api/waitlist.js` — Vercel serverless function: POST {email} → Resend email to
  wes@practicalsystems.io. Origin-checked, no storage, no dependencies.
- Deployed via Vercel git integration from `site/`; domain sidetap.io + www; DNS at
  Namecheap (A @ → 76.76.21.21, CNAME www → cname.vercel-dns.com).

## Success criteria

- sidetap.io serves the page over HTTPS with the hero loop playing.
- Waitlist form delivers a real email end-to-end.
- README links to sidetap.io; docs updated; repo still passes tests.
