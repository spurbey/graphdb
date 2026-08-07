# architecture.md

> Parent index: [`README.md`](README.md) · Next: [`animations.md`](animations.md) ·
> See also: [`styles/design-system.md`](styles/design-system.md)

This file maps the **entire codebase**: every file, what it owns, and how the pieces
line up — from the static shell to the last animation.

---

## 1. The whole picture (one paragraph)

A single-page React app composed top-to-bottom in `App.jsx`: a fixed `Nav` with a
scroll-progress tracker, then one `<main>` containing Hero → absurdity `TextSection`
→ `System` (indigo) → `How` → `Different` → "with monolayer" `TextSection` → `Pricing`
(olive) → `Footer`, with a `Modal` portal sitting outside `main`. Motion is split into
**reusable primitives** (`anim/easings.js`, `anim/useScramble.js`) consumed by each
section, all driven by **one Lenis smooth-scroll instance** piped into GSAP's ticker,
while **ScrollTrigger** scrubs the section reveals. Layout is powered by a **fluid
em engine** (see `styles/design-system.md`) and every animation is a faithful port of
the original site's shipped bundle.

---

## 2. File tree (annotated)

```
monolayer/
├─ index.html            # Static shell: #root + <script> /src/main.jsx. Meta/title only.
├─ package.json          # Deps: react, react-dom, gsap, lenis, vite. Scripts: dev/build/preview.
├─ vite.config.js        # Vite + React plugin; dev server on :5173.
├─ tsconfig.json         # TS config (unused at runtime — JS project).
├─ public/
│  └─ fonts/
│     └─ PPFrama-Regular.woff   # The only font (self-hosted).
└─ src/
   ├─ main.jsx           # ENTRY: imports CSS cascade then mounts <App/> in <StrictMode>.
   ├─ App.jsx            # COMPOSITION ROOT: Lenis + GSAP wiring, section stack, context provider.
   ├─ SiteContext.jsx    # React context: { lenis, reducedMotion, isTouch }.
   ├─ anim/
   │  ├─ easings.js      # registerEasings() (CustomEase set) + TIMING table + raw eases.
   │  └─ useScramble.js  # useScramble hook + playScramble: word scramble engine.
   ├─ components/
   │  ├─ Nav.jsx                  # Fixed nav: logo, progress bar, fullscreen menu.
   │  ├─ SectionNavTracker.jsx    # ScrollTrigger → nav progress bar + per-section underlines.
   │  ├─ Hero.jsx                 # Intro headline + Unicorn (WebGL) visual + CTA.
   │  ├─ TextSection.jsx          # Reusable editorial text block + entrance shutter.
   │  ├─ ShutterTransition.jsx    # The scroll-reveal wall (indigo/olive/off-white).
   │  ├─ System.jsx               # Indigo pinned scrub: chapter tree collapse→stack→sequence.
   │  ├─ How.jsx                  # Sticky-column step track.
   │  ├─ Different.jsx            # "what was manual → automated" scrub-scramble items.
   │  ├─ Pricing.jsx              # Olive pricing grid + note column + exit shutter.
   │  ├─ Footer.jsx               # Statement, tabbed link columns, legals, logo.
   │  ├─ Modal.jsx                # Slide-in panel + backdrop + custom scrollbar.
   │  └─ icons.jsx                # Inline SVG icon components (Logo, arrows, cropmarks…).
   └─ styles/
      ├─ tokens.css      # Fonts, fluid engine, colors, themes, sizes, spacing, breakpoints.
      ├─ base.css        # Reset, typescale classes, layout primitives, [space-*], underlines.
      └─ components.css  # Component blocks, page order, SHUTTER, SYSTEM VISUAL, MODAL, etc.
```

> `icons.jsx` also has an inline fallback arrow drawn as `<rect>`s (see `Nav.jsx`
> `ArrowInline`) to avoid circular imports.

---

## 3. Composition root — `App.jsx`

`App.jsx` is where "how the site lines up" lives. Responsibilities:

1. **Lenis smooth scrolling.** One instance; `lenis.on("scroll", ScrollTrigger.update)`;
   a GSAP ticker loop drives Lenis (`gsap.ticker.add(raf)`), so **Lenis and GSAP share
   the same clock**. Wheel multiplier differs for touch.
2. **Scroll restoration off + start at top.**
3. **`ScrollTrigger.refresh()`** after fonts/layout settle (600 ms) so pinned scrub
   distances measure correctly.
4. **Context provider** — `<SiteStateContext.Provider value={site}>` exposes the Lenis
   instance + `reducedMotion` + `isTouch` to consumers.
5. **Section stack** (the `main`):

```jsx
<Nav />
<SectionNavTracker />
<main className="main">
  <Hero />
  <TextSection sectionKey="absurdity" … spaceTop="xxl" spaceBottom="xl" />  {/* indigo shutter */}
  <System />                                                                    {/* indigo */}
  <How />
  <Different />
  <TextSection sectionKey={null} … spaceTop="s" spaceBottom="xl"
               shutterVariant="olive" shutterTheme="nav:olive" />            {/* olive shutter */}
  <Pricing />                                                                  {/* olive */}
  <Footer />
</main>
<Modal />
```

### Section → nav-theme contract
Each section carries `data-theme-change="nav:base|indigo|olive"`. `ShutterTransition`
reads it (via the `theme` prop) and flips the nav's theme class at `progress >= 0.4`,
so the nav recolors exactly as the colored sections enter. `Nav` starts as
`.theme-base`.

### Section → nav-progress contract
`data-nav-progress-section="<label>"` on a section lets `SectionNavTracker` fill the
matching nav underline. Labels: `absurdity`, `system`, `how it works`, `different`,
`pricing`.

---

## 4. Shared state (`SiteContext.jsx`)

Plain React context. Nothing reactive re-renders on scroll — it exists so motion modules
read `reducedMotion`/`isTouch` at mount (and Lenis is reachable). Consumers:
`Hero`, `Nav`, `useScramble`, `ShutterTransition`, etc. — all read via
`useSite()` or passed explicitly.

---

## 5. The motion layer (`anim/`)

**`easings.js`** — single source of truth for curves and timing:
- `registerEasings()` (called once in `App.jsx`) creates the site's global
  `CustomEase`s: `main` (default), `navMenuOpenCloseEase`, the four `system*` eases,
  and sets `gsap.defaults({ ease: "main", duration: 0.6 })`.
- Exports raw math eases (`regalScrollEase`, `power3Out`, `easeInOutCubic`) for
  hand-rolled scroll/morph work.
- Exports `TIMING` — the whole timing table (scramble, regal, modal, tabs, shutter,
  system, how, different, menu). **Edit timing here, not in components.**

**`useScramble.js`** — the signature type effect:
- `playScramble(el, opts)` — splits element text into `[data-scramble-word]` spans
  (grapheme-aware, whitespace-preserved), then shuffles random chars into locked
  positions on a `setTimeout` cadence until progress hits 1. `intensity` → ratio of
  letters that scramble. Modes: `load`/`transition` (play once),
  `scroll` (IntersectionObserver, fires when entering viewport), `hover`
  (mouse/focus in-out).
- `useScramble(scopeRef, site)` — hooks all targets inside a component scope:
  `[data-scramble="load"]`, `[data-scramble="scroll"]`, `[data-scramble-hover="link"]`.

Every section calls `useScramble(scopeRef, null)` at mount and puts `data-scramble`
attributes on the headings it wants animated.

---

## 6. The scroll system (how JS + scroll connect)

```
Lenis (App.jsx)                    ScrollTrigger (gsap)          component effects
     │                                      ▲                              ▲
     │  lenis.on("scroll", ST.update)       │                              │
     └─────────── gsap.ticker ──────────────┘  scrub: true / 0.075 / 0.3   │
                                                          │                │
   Each component creates its own ScrollTrigger(s)      ◄──┘                │
   (pinned system, shutter walls, how/different tracks, nav tracker).       │
```

Rule of thumb: **scroll position is the only input; GSAP scrubs everything.** There is
no custom scroll-spy logic except `SectionNavTracker` (which also uses ScrollTrigger).

---

## 7. How the sections line up (geometry + the "seams")

The page is a **single vertical stack** of `<section>`s. Sections are given vertical
rhythm via `[space-top]` / `[space-bottom]` attributes on inner wrappers (margins).

### ⚠️ The seam problem (fixed — don't reintroduce)
Those margins collapse **between** sections, leaving white gaps. The original site is
fully flush (each section's bottom touches the next section's top). Because
`ShutterTransition` walls anchor to their own section's `bottom:0`, a seam would make a
wall hover **270px above** the colored section it reveals.

`ShutterTransition` solves it by:
1. Finding the next section sibling and measuring the **seam**
   (`nextRect.top - sectionRect.bottom`).
2. Setting `--shutter-seam` and `bottom: calc(-1 * var(--shutter-seam))` so the wall
   drops to sit **exactly on the next section's top**.
3. **Triggering off the next section** (`start: "top bottom"`) instead of the containing
   section's bottom, so the reveal fires exactly when the colored section enters the
   viewport (see `animations.md` § Shutter).

Keep sections flush or keep the seam-bridge; never do both incorrectly.

---

## 8. What runs where (execution flow)

| What happens | Who does it |
|--------------|-------------|
| Shell loads, CSS cascade, `<App/>` mounts | `index.html` → `main.jsx` |
| Lenis + GSAP wiring | `App.jsx` effect |
| Global eases registered | `App.jsx` → `registerEasings()` |
| Nav progress bar wired | `SectionNavTracker.jsx` effect |
| Each section's effects run (scramble, scrub, pins) | per-component `useEffect` |
| ScrollTrigger positions measured after fonts | `App.jsx` 600 ms refresh |
| Reduced motion short-circuits | read in each motion module |

---

## 9. Adding a new section (concrete checklist)

1. Create `src/components/MySection.jsx` — `<section className="my-section" data-theme-change="nav:base" data-nav-progress-section="…" ref={scopeRef}>`, call `useScramble(scopeRef, null)`.
2. Add it to the `main` stack in `App.jsx` in the right visual order.
3. Add a `styles/components.css` block under a `/* ---- MY SECTION ---- */` marker.
4. If it's a nav-progress section, add its label to `Nav.jsx` `PROGRESS_ITEMS` (and the menu links).
5. If it should flip nav color, use a `data-theme-change` value matching a theme class.
6. If you want an entry/exit reveal wall, drop a `<ShutterTransition variant="…" theme="nav:…"/>` inside it (see `animations.md` § Shutter for the four existing walls).
7. `npm run build` must stay green.

---

Next: [`animations.md`](animations.md).
Back to [`README.md`](README.md).