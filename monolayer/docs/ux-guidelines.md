# ux-guidelines.md

> Parent index: [`README.md`](README.md) · Prev: [`animations.md`](animations.md) ·
> Theming: [`styles/design-system.md`](styles/design-system.md)

The **interaction + accessibility model** of the clone — not how pixels look, but how
the product *behaves* and why the motion choices are the way they are. The clone is
deliberately faithful to the original's UX, so several rules here describe
*reproducing* behavior rather than inventing new UX.

---

## 1. Core interaction model (the "feel")

- **One-way motion**: almost everything reveals **from the bottom**. Buttons squash on
  press, walls rise from the lower edge, items translate vertically as you scroll. This
  directional consistency (bottom-anchored reveal) reads as "scrolling the surface,
  the page peels up toward you." Keep new motion bottom-up unless there's a strong
  reason otherwise.
- **Scroll is the only controller.** There is no click-to-advance for section content;
  the System visual, the How track, and the Different morph are all **scrubbed by
  scroll position**. This matches the original's "self-running" narrative — the page
  basically autoplays as you scroll, which is the product pitch in motion.
- **Text as punctuation**: chapter headings scramble on entry; body reveals are
  calm. Type is content-forward, motion is the flourish, not the other way around.
- **Color is a ruler**: indigo = the **system** (blue = product tech), olive = **the
  commercial layer** (pricing). The nav recolors to tell you where you are, without a
  single chrome page-change. Sections you're in are the tint; everything else is the
  base off-white.

---

## 2. Mapping motion → content (so it always reads)

| Motion | What it means |
|--------|---------------|
| Wall rising (shutter) | "You are leaving one world, entering the next colored band." |
| Scramble-in heading | "Fresh idea introduced here" (load/scroll into view). |
| Hover-scramble on a link | "This word is interactive" (manifested as text, not a box). |
| System stack collapse → truth | The visual narrative: chapters compress, then flow as a lead chapter. |
| How list sliding within sticky column | "These steps share one journey" — the eye stays anchored while the different fires. |
| Different morph + bar | before → after contract; the bar is real progress, the scramble is the change. |
| Scale-down on press (main CTA) | touch/tactile affordance that the button is real. |

---

## 3. Reduced-motion & accessibility

`prefers-reduced-motion: reduce` collapses every motion module to a **static, fully-
readable state** (no pin, no scrub, no scramble):

- `System.jsx` → static stacked chapters (no pin/sequence).
- `Different.jsx` → final content + bar, no morph.
- `How.jsx` → transforms cleared (vertical stack).
- `useScramble`/`ShutterTransition`/nav tracker → no-op.

Beyond reduced motion:

- **Focus**: keyboard `focus-visible` rings on buttons/icons; modal traps focus on the
  close button when open.
- **Sass**: `aria-expanded`/`aria-label` on interactive icons; `aria-hidden` on
  decorative auto-scramble elements.
- **Scroll**: Lenis + ScrollTrigger are disabled/fine on touch; wheel/touch handled
  separately in `App.jsx`.
- **Responsive**: breakpoints only re-target the fluid scale, so no layout flips/SCSS
  hacks — the system stays the same at every width.

---

## 4. Touch handling

- `isTouchUnder991()` (in `SiteContext` / `App.jsx`) switches the pinned System visual
  to phrase pointer coarse→ simpler scrub.
- Buttons get `:active` transforms (no hover-only interactions baffle a touch user);
  hover-states only add polish, never block.
- Modal pan-y works for coarse pointer (custom scrollbar).

---

## 5. The scroll contract

Rule of thumb: **scroll position is the only input; GSAP scrubs everything.** Nothing
in the app reads scroll distance with custom math except when a module *intentionally*
derives progress (System, Different) — those still do so **from `self.progress`**, not
by hand-scrolling math. This invariance is what keeps the reveal deterministic and
stable across fonts that load late. If a new module needs scroll → keep it ScrollTrigger-
driven, never a raw `scroll` listener with bespoke math unless it can't be helped (and
register it for `ScrollTrigger.refresh()`).

---

## 6. Color → theme wiring (UX token)

`.theme-*` classes on the `<nav>` / sections are the **source of truth** for shell
chroma. Never hard-code nav; color into a component: set `data-theme-change` on a section
and let `ShutterTransition` + `<Nav theme>` apply it. Full mapping in:
`styles/design-system.md` § Themes.

---

## 7. Common pitfalls checked against at review

- Did I add a `reset` `active` to keep flush seams (see `architecture.md` §7)?
- Did I add a Custom `CustomEase` outside `easings.js` (should never)?
- Did I add a hardcoded px color/font that should be a token?
- Did I add a new `scroll` listener that should be SwimTrigger scrub (or an onRefresh
  re-measure)?
- Does reduced motion still render the final text?

---

Prev: [`animations.md`](animations.md) · Top: [`README.md`](README.md) · Theming: [`styles/design-system.md`](styles/design-system.md)