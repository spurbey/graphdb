# animations.md

> Parent index: [`README.md`](README.md) · Prev: [`architecture.md`](architecture.md) ·
> Theming: [`styles/design-system.md`](styles/design-system.md)

This is the **complete animation guide**. Every system, its trigger, its driving
inputs, its timing/easing, and how it was ported 1:1 from the original bundle.

All timing/easing lives in **`src/anim/easings.js`** (`TIMING` + `CustomEase`s) — when
you tune a system, edit there first.

---

## 0. How animation is delivered

- **Lenis** (smooth scroll) + **GSAP**: one shared clock (see `architecture.md` §6).
- **ScrollTrigger scrub** for everything tied to scroll position.
- Support `prefers-reduced-motion` (every module short-circuits to a static state).
- Pay attention to **cover vs reveal**: the site's directionality (walls scale up from
  bottom) is intentional and matches the original.

---

## 1. Text scramble (`useScramble.js`)

**Where**: `data-scramble` / `data-scramble-hover` attributes on headings/paragraphs.
**Engine**: `playScramble` — for each word, split into `[data-scramble-word]` spans; then
random-flavored letters are written into locked positions over the duration.
**Intensity** → `intensityToRatio` (how many letters scramble; `1..10`).
**Modes**:
- `data-scramble="load"` — play once on mount (hero headline + USPs).
- `data-scramble="scroll"` — play when entering viewport (IntersectionObserver;
  `rootMargin: "0px 0px -8% 0px"`).
- `data-scramble-hover="link"` / `data-scramble-hover="target"` — replay on
  mouseenter / focus; restore original on leave.

**Timing** (`TIMING.scramble*`): load 1.45s, scroll 1.65s, hover 0.9s; stagger
2–30 ms; update interval ~26–110 ms.

---

## 2. Nav progress + theming

**Progress bar** (`SectionNavTracker.jsx`): a global ScrollTrigger on `document.body`
(`top top` → `bottom bottom`, scrub) sets `scaleX(progress)` on `.nav-main__progress-bar`.
Per-section underlines: one ScrollTrigger per `[data-nav-progress-section]`
(`top 60%` → `bottom 40%`) sets `--nav-progress` on matching labels.

**Theme flip** (`ShutterTransition.jsx` § 4): independent ScrollTrigger, same
start/end as the wall, flips `.nav` to `theme-indigo`/`theme-olive`/`theme-base` at
`progress >= 0.4`.

**Fullscreen menu** (`Nav.jsx`): opens/closes with React state (`menuOpen`), menu tabs,
cropmarks, and jump-to-section links; the logo scrolls to top. Jump uses
`scrollIntoView({ behavior:"smooth" })`.

---

## 3. Hero + CTA

- `Hero.jsx`: scramble headline (`data-scramble="load"` intensity 5) + USPs; the big
  visual is a **Unicorn Studio WebGL scene** injected via SDK (`addScene`) with a
  graceful empty-box fallback when the CDN is unreachable.
- `main-btn` (CTA): pure-CSS elastic squash on press (`:active transform scaleX/scaleY`),
  plus an animated arrow whose `rect`s "plop" in with per-rect `--plop-delay`
  (see `components.css` `/* MAIN BTN interaction */`).

---

## 4. Shutter scroll transition (`ShutterTransition.jsx`) ⭐

The multi-section reveal wall — the site's signature section-to-section transition.

**Structure** (matches original DOM): a `div` with `data-shutter-scroll-transition`
containing a grid of **`cols` × `rows`** cells (default `4 × 10` = 40 cells) in
`[data-shutter-scroll-grid]`, plus `[data-shutter-scroll-fill]`.

**Variant colors** map to theme background: `indigo`, `olive` (from theme aliases),
`off-white` (default = base background).

**Animation** (cover mode): cells start `scaleY(0)` anchored `bottom center`; on scroll
they scale to 1 with `stagger 0.22 **from the end**` (the "curtain" rises from the
bottom row). A solid `fill` layer covers behind (stagger 0.1). Scrubbed (`scrub: 0.3`).

**The four walls on the page** (matching the original):

| Wall | sits inside | target section | variant | nav theme |
|------|-------------|----------------|---------|-----------|
| A: enter absurdity→System | absurdity `TextSection` | System (indigo/blue) | indigo | `nav:indigo` |
| B: exit System→How | System | How | off-white | `nav:base` |
| C: enter "with monolayer"→Pricing | "with monolayer" `TextSection` | Pricing (olive) | olive | `nav:olive` |
| D: exit Pricing→Footer | Pricing | Footer | off-white | `nav:base` |

**Bridge + trigger (the fix)** — see `architecture.md` §7. Three key lines:
```
const start  = "top bottom";                                  // target section's top hits viewport bottom
const end    = scrollEnd.replace(/^bottom/, "top");           // e.g. "top top-=30%"
applyBridge() → el.bottom = calc(-1 * var(--shutter-seam))    // wall lands flush on next section
```
So the reveal fires **when the colored section is about to enter the viewport**, and the
wall is flush (not floating in the seam). Resize re-measures the seam.

---

## 5. System pinned visual (`System.jsx`)

The most complex module — a **pinned**, scroll-scrubbed indigo scene of a "chapter tree".

**Structure**: chapters nest (1 → 5), each with `.system__text` + a nested wrap. The
visual root is pinned via GSAP ScrollTrigger (`pin`, `pinSpacing:false`), scroll
distance = `--system-visual-scroll-distance` (`SCROLL_VH` 460% viewport).

**States driven from scroll progress `0→1`** (rendered by `renderMainProgress`), in order:
1. **Hold** — first chapter visible.
2. **Reveal** (loop) — each `system-chapter` expands at its own reveal step; chapters
   scale in through a `systemChapterPop` ease.
3. **Collapse** — stack chapters crinkle to a single block (chapter/text padding
   compressed via `scaleLength`).
4. **Bridge** — the lead chapter downsizes / nesting reorders (text-before-nested).
5. **Sequence (Seq 3)** — a scrolling "sequence three" travels the stack: text items
   expand with padding animation while the whole tree translates vertically to center
   the lead, ending centered with all chapters in sequence.

**Easing** (`easings.js`): `systemEaseOut`, `systemEasePop`, `systemSequenceThree`,
`systemSequenceNative`. Hand-rolled helpers (`lerp3`, `scaleLength`, `mixLength`,
`progressInRange`) map its own timeline in `renderMainProgress`, so it does **not**
use a GSAP timeline — it renders state imperatively per progress.

**Touch**: `isTouchUnder991()` → collapses to a simpler grab-by-scrubbing mode with
different segment lengths.

---

## 6. How — sticky column track (`How.jsx`)

**The digital track**: a sticky-ish column (`data-how-track`) that scrolls up while the
inner list (`data-how-track`) travels down 
`list`) travels down (`−getListTravel`). Both scrubbed on the wrapper
(`start "top 30%" / end "bottom 60%" / scrub 0.075`).

- `getCollectionTravel()` = wrapper height − track height.
- `getListTravel()` uses the last item's real content box, so stops exactly.

**Timing**: `howTrackScrub: 0.075`.

---

## 7. Different — scrub-scramble morph (`Different.jsx`)

The five "before (manual) → after (automated)" rows.

**Per item**: it measures the *before* and *after* text heights, then scrubs:
- A **progress bar** scales `scaleY` along the item's scroll (`0→0.7`).
- The **content text morphs** (length-aware) + scrambles as you scroll
  (`TEXT_PROGRESS_START .18 → .52`), easing `easeInOutCubic`.
- The **item itself translates** vertically within its own `__track`
  (`ITEM_TRAVEL_RATIO` of the scroll distance; desktop track multiplier 1.8×).
- A manual rAF-driven `renderScrubScramble` throttles DOM text updates (~26 ms) and
  re-renders the `<p>` text content each step.

**Easing/timing**: in `easings.js` (`differentTrackDesktop`, etc.) and local consts in
`Different.jsx`.

Metrics re-measure via `setTrackHeight()` on refresh (`ScrollTrigger` onRefresh) +
ResizeObserver → `ScrollTrigger.refresh()`, so it survives resize/font-load.

---

## 8. Modal (`Modal.jsx`)

Triggered by delegated `[data-modal-trigger]` clicks (one real content block:
"account-setup" sharing the full body text).

**Animation** (timing in `TIMING.modal*`):
- Panel: `xPercent -100 → 0`, duration 0.75s, ease `main` left-to-right.
- Backdrop: `autoAlpha 0 → opacity (default .24)`, 0.35s `power1.out`.
- Close: panel 0.32s, backdrop 0.22s.
- `Enter`/`Esc`? Actually close via backdrop click or `[data-modal-close]`; focuses the
  close button on open; stores `body` overflow so the page scroll locks (`overflow:
  hidden`) while open; records `--modal-nav-height` to pad `.main` so the fixed header
  doesn't overlap.
- Custom scrollbar: `.modal-scrollbar__bar` `top%` synca rAF on the inner scroll el
  (keeps a manual pan-y for coarse pointer).

---

## 9. Where to tune timing/easing (summary)

| Want to change… | Go to |
|----------------|-------|
| global default ease/duration | `easings.js` `registerEasings()` |
| any specific time constant | `easings.js` `TIMING` |
| scramble speed/stagger | `easings.js` `TIMING.scramble*` |
| shutter wall reveal | `easings.js` `TIMING.shutter*` + `System.jsx` wall props |
| system pinned scrub pacing | `easings.js` `TIMING.system*` + `System.jsx` constants |
| how track | `easings.js` `TIMING.howTrackScrub` |
| different morph/travel | `easings.js` `TIMING.different*` + `Different.jsx` consts |
| modal open/close | `easings.js` `TIMING.modal*` |

---

## 10. Reduced motion

`prefers-reduced-motion: reduce` → each is handled:
- `System.jsx` collapses to a static stacked state
  (`data-system-visual-collapsed="true"`, no pin).
- `How.jsx` clears transforms.
- `Different.jsx` shows final text/bar, no morph.
- `useScramble` skips, `ShutterTransition` & nav tracker no-op.

---

Prev: [`architecture.md`](architecture.md) · Next: [`styles/design-system.md`](styles/design-system.md) · Top: [`README.md`](README.md)