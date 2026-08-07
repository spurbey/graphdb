# monolayer clone — documentation index

This is a **faithful, from-scratch recreation** of the `monolayer.dev` landing page
(built in React + Vite + GSAP + Lenis). This doc set describes the **whole build**:
what each piece is, how the parts line up, how the motion/JS/CSS connect, the entry
points you touch when you want to change something, and the site's UI/UX model.

Every value, easing, timing constant, and DOM structure was reverse-engineered 1:1
from the original site's shipped bundle (`bundle.js`, `bundle.css`, `webflow.css`) so
the clone reproduces the original determinism, not an approximation.

> **Start here.** Read `README.md` (this file), then follow the links below in order.

---

## The doc map (read order)

| # | Document | What it answers |
|---|----------|-----------------|
| 1 | [`architecture.md`](architecture.md) | How the repo is laid out, what every file is, and how the pieces line up end-to-end. |
| 2 | [`styles/design-system.md`](styles/design-system.md) | The fluid scaling engine, design tokens, themes, spacing, and breakpoints. |
| 3 | [`animations.md`](animations.md) | Every animation system: scramble, Lenis scroll, ScrollTrigger scrub, shutter, system visual, how/different tracks, modal. |
| 4 | [`ux-guidelines.md`](ux-guidelines.md) | UI/UX principles, interaction model, reduced-motion, touch handling, and how motion maps to content. |

> The four docs are standalone files (see the map above). `styles/design-system.md` is
> nested under `styles/`; `architecture.md`, `animations.md`, and `ux-guidelines.md` are
> at the top level. Each cross-links to the others.

---

## Quick map — everything at a glance

```
index.html            static shell, mounts /src/main.jsx
src/main.jsx          entry: imports CSS cascade (tokens → base → components) + App
src/App.jsx           page composition = top-to-bottom stack of sections + shared systems
├─ Nav                 fixed nav + progress bar + fullscreen menu
├─ SectionNavTracker   scroll-progress tracker for the nav (GSAP ScrollTrigger)
├─ main > sections      Hero, TextSection(s), System, How, Different, Pricing, Footer
└─ Modal               slide-in panel system (delegated [data-modal-trigger])
```

**Three CSS layers** (loaded exactly in this order — order matters):

1. `styles/tokens.css` — fonts, fluid engine, colors, themes, sizes, spacing, breakpoints.
2. `styles/base.css` — reset, typescale classes, layout primitives, `[space-*]` attr system, underline links.
3. `styles/components.css` — per-component block styles, in page order (NAV → … → MODAL → UTILITY → BREAKPOINTS).

**Three JS concerns** (interconnected through GSAP + Lenis):

| Concern | Location | Role |
|---------|----------|------|
| Motion / easing | `src/anim/easings.js` | Registers all `CustomEase`s, global GSAP defaults, timing table. |
| Text scramble | `src/anim/useScramble.js` | Word-by-word scramble via `data-scramble` / `data-scramble-hover`. |
| Scroll | `App.jsx` + each component | Lenis smooth scroll wired into GSAP ticker; ScrollTrigger drives all scrub. |

---

## Entry points (how to change the site)

- **Add / change a page section** → `src/App.jsx` (the stack is assembled here) + a new JSX in
  `src/components/` + a matching block in `styles/components.css` + a nav label in
  `Nav.jsx` (`PROGRESS_ITEMS`) + a `data-theme-change` for nav color.
- **Change design tokens / scaling / fonts / breakpoints** → `styles/tokens.css`.
- **Change site-wide spacing rhythm** → the `[space-*]` / `[text-indent]` attributes (see
  `styles/design-system.md`).
- **Change any animation timing / easing** → `anim/easings.js` (`TIMING` + `CustomEase`).
- **Change the scramble text effect** → `anim/useScramble.js`.
- **Change the section-scroll reveal walls** → `components/ShutterTransition.jsx` + `styles/components.css` SHUTTER block.
- **Change the pinned System visual** → `components/System.jsx` (see `animations.md` § System).
- **Change the modal** → `components/Modal.jsx` (`MODAL_CONTENT`).
- **Change the nav progress bar / section labels** → `components/Nav.jsx` + `SectionNavTracker.jsx`.

---

## Running / building

```bash
cd monolayer
npm install
npm run dev        # http://localhost:5173 (Vite)
npm run build      # production build → dist/
npm run preview    # preview the built bundle
```

Scripts + deps live in `package.json` (React 19, GSAP 3.13, Lenis, Vite 7). Vite config is
`vite.config.js` (dev server port 5173). No tests/lint gates — **always verify the build**:
`npm run build` must be green after any change.

---

## Core invariants (keep these true)

1. **Fluid engine is the backbone** — never hard-code pixel sizes. All layout/type derives
   from the `em`-based tokens driven by `--size-font` (see `styles/design-system.md`).
2. **Sections must stay flush** — the inter-section "seams" are measured and bridged by
   `ShutterTransition` so walls land exactly on the next section's top. Don't reintroduce
   gaps between `space-bottom`/`space-top` margins that desync the reveal.
3. **`data-*` attrs are the contract** — the animations bind to markup attributes
   (`data-scramble`, `data-theme-change`, `data-modal-*`, `[data-shutter-*]`, nav
   progress selectors). Keep ids/attrs stable if you rename markup.
4. **Reduced motion** is honored in every motion module (`prefers-reduced-motion`).

---