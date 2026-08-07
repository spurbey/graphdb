// Exact animation/easing layer — every value extracted 1:1 from the
// original site's decompiled bundle (odyn bundle.js).

import gsap from "gsap";
import { CustomEase } from "gsap/CustomEase";

// ---- cubic-bezier(x1,y1,x2,y2) → GSAP CustomEase ----
export function registerEasings() {
  // the site's global default ease
  CustomEase.create("main", "0.625, 0.05, 0, 1");
  // menu open/close (equivalent curve written as SVG path in source)
  CustomEase.create("navMenuOpenCloseEase", "M0,0 C0.625,0.05 0,1 1,1");
  // system-visual section
  CustomEase.create("systemEaseOut", "0.215, 0.61, 0.355, 1");
  CustomEase.create("systemChapterPop", "0.34, 1.12, 0.64, 1");
  CustomEase.create("systemSequenceThree", "0.76, 0, 0.24, 1");
  CustomEase.create("systemSequenceNative", "0.22, 0, 0.78, 1");

  gsap.defaults({ ease: "main", duration: 0.6 });
}

// ---- hand-rolled eases (verbatim from bundle) ----
// jump-to-section scroll: power3 inOut
export const regalScrollEase = (t) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

// nav progress anchor scroll: power3 out
export const power3Out = (t) => 1 - Math.pow(1 - t, 3);

// different-section text morph progress: cubic in-out
export const easeInOutCubic = (v) =>
  v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2;

// CSS fallback (nav progress reset, underline links)
export const CSS_EASE_OUT_QUINT = "cubic-bezier(0.22, 1, 0.36, 1)";
export const CSS_EASE_MAIN = "cubic-bezier(0.625, 0.05, 0, 1)";

// ---- timing constants ----
export const TIMING = {
  // scramble
  scrambleLoad: { duration: 1.45, minStagger: 2e-3, maxStagger: 0.024, minUpdateInterval: 110, maxUpdateInterval: 26 },
  scrambleScroll: { duration: 1.65, minStagger: 3e-3, maxStagger: 0.03, minUpdateInterval: 110, maxUpdateInterval: 26 },
  scrambleHover: { duration: 0.9, minStagger: 2e-3, maxStagger: 0.026, minUpdateInterval: 95, maxUpdateInterval: 24 },

  // jump-to-section ("regal scroll")
  regalScrollMin: 0.72,
  regalScrollMax: 2.35,
  regalDistanceMultiplier: 0.34,
  regalDistancePower: 0.72,

  // modal
  modalOpen: 0.75,
  modalClose: 0.32,
  backdropOpen: 0.35,
  backdropClose: 0.22,
  backdropOpacity: 0.24,

  // nav progress reset
  navProgressReset: 0.45,

  // tabs cropmark slide
  tabsCropmark: 0.42,

  // shutter-scroll
  shutterScrub: 0.3,
  shutterDuration: 0.1,
  shutterStagger: 0.22,
  shutterFillStart: 0.34,
  shutterFillDuration: 0.06,
  shutterFillStagger: 0.1,

  // system visual (fractions of pinned scroll distance)
  systemReveal: 0.46,
  systemPadding: 0.24,
  systemHold: 0.008,
  systemCollapse: 0.64,
  systemBridge: 0.34,
  systemSeqThree: 1.45,
  systemSeqThreeTouch: 1.08,
  systemDefaultScrollVH: 460, // data-systemVisualScroll default (4.6 viewport heights)
  systemFocusY: 0.5,

  // how track
  howTrackScrub: 0.075,

  // different
  differentTrackDesktop: 1.8,
  differentItemTravelRatio: 0.75,

  // menu
  menuShutterRows: 6,
  menuShutterCols: 4,
  menuItemY: "1.15em",
  menuItemOpen: 0.46,
  menuItemClose: 0.26,
  menuItemStagger: 0.028,
  navChromeYPercent: -120,
};
