// Word-per-word scramble text effect — the site's signature type animation.
// Custom grapheme-shuffle implementation matching the original bundle's
// scramble.js exactly (GSAP plugins were registered but unused there).

import { useEffect } from "react";
import { TIMING } from "./easings.js";

const SCRAMBLE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789";

function intensityToRatio(intensity, menuVariant = false) {
  const i = Math.min(10, Math.max(1, parseInt(intensity, 10) || 5));
  if (menuVariant) {
    const table = [0.12, 0.24, 0.34, 0.46, 0.54, 0.62, 0.70, 0.78, 0.86, 0.94];
    return table[i - 1];
  }
  if (i === 1) return 0.4;
  if (i === 2) return 0.6;
  if (i === 3) return 0.75;
  if (i === 4) return 0.9;
  return 1.0;
}
const lerp = (a, b, t) => a + (b - a) * t;

// Split element text into word spans (whitespace preserved).
function splitIntoWords(el) {
  if (el.dataset.scrambleWordSplit === "true") return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_SKIP;
      if (node.parentElement?.closest?.("[data-scramble-ignore]")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const frag = document.createDocumentFragment();
    node.nodeValue.split(/(\s+)/).forEach((part) => {
      if (!part) return;
      if (/^\s+$/.test(part)) {
        frag.appendChild(document.createTextNode(part));
      } else {
        const span = document.createElement("span");
        span.setAttribute("data-scramble-word", "");
        span.textContent = part;
        frag.appendChild(span);
      }
    });
    node.parentNode.replaceChild(frag, node);
  });
  el.dataset.scrambleWordSplit = "true";
}

function scrambleWord(wordEl, { duration, staggerIndex, settings, ratio, menuVariant }) {
  const original = wordEl.textContent;
  // grapheme-aware split
  const graphemes = Array.from(original);
  const letterIdx = [];
  graphemes.forEach((g, i) => {
    if (/[\p{L}\p{N}]/u.test(g)) letterIdx.push(i);
  });
  if (!letterIdx.length) return;

  const count = Math.max(1, Math.round(letterIdx.length * ratio));
  // evenly distributed picks across letter positions
  const picks = [];
  for (let k = 0; k < count; k++) {
    picks.push(letterIdx[Math.floor((k * letterIdx.length) / count)]);
  }

  const tNorm = 1 / 9; // normalized intensity step effect is folded into settings
  const wordDelay = staggerIndex * (settings.minStagger + (settings.maxStagger - settings.minStagger) * tNorm);
  const updateMs = lerp(settings.minUpdateInterval, settings.maxUpdateInterval, tNorm);
  const start = performance.now() + wordDelay * 1000;
  const end = start + settings.duration * 1000;

  let timer = null;
  const tick = () => {
    const now = performance.now();
    if (now < start) {
      timer = setTimeout(tick, updateMs);
      return;
    }
    const progress = Math.min(1, (now - start) / (end - start));
    const locked = Math.floor(progress * picks.length);
    const chars = original.split("");
    picks.forEach((pi, order) => {
      if (order < locked) {
        chars[pi] = original[pi];
      } else {
        const g = original[pi];
        // preserve numeric vs alpha flavor
        const pool = /[0-9]/.test(g) ? "0123456789" : SCRAMBLE_CHARS;
        chars[pi] = pool[Math.floor(Math.random() * pool.length)];
      }
    });
    wordEl.textContent = chars.join("");
    if (progress < 1) {
      timer = setTimeout(tick, updateMs);
    } else {
      wordEl.textContent = original;
    }
  };
  timer = setTimeout(tick, updateMs);
  return () => clearTimeout(timer);
}

export function playScramble(component, { menuVariant = false } = {}) {
  const intensity = component.dataset.scrambleIntensity || "5";
  const ratio = intensityToRatio(intensity, menuVariant);
  const mode = component.dataset.scramble || "scroll";
  let settings = TIMING.scrambleScroll;
  if (mode === "load" || mode === "transition") settings = TIMING.scrambleLoad;
  if (component.dataset.scrambleHover) settings = TIMING.scrambleHover;

  splitIntoWords(component);
  const words = component.querySelectorAll("[data-scramble-word]");
  const cleanups = [];
  words.forEach((w, i) => {
    const c = scrambleWord(w, {
      staggerIndex: i,
      settings,
      ratio,
      menuVariant,
    });
    if (c) cleanups.push(c);
  });
  return () => cleanups.forEach((fn) => fn());
}

// Mount-time wiring for all scramble targets in scope.
export function useScramble(scopeRef, site) {
  useEffect(() => {
    const scope = scopeRef.current;
    if (!scope || site?.reducedMotion) return;
    const cleanups = [];

    // load / transition: play once immediately (transition replays post-nav)
    scope.querySelectorAll('[data-scramble="load"], [data-scramble="transition"]').forEach((el) => {
      cleanups.push(playScramble(el) || (() => {}));
    });

    // scroll: fire once when entering viewport
    scope.querySelectorAll('[data-scramble="scroll"]').forEach((el) => {
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              playScramble(el);
              io.disconnect();
            }
          });
        },
        { rootMargin: "0px 0px -8% 0px" }
      );
      io.observe(el);
      cleanups.push(() => io.disconnect());
    });

    // hover: link wraps target
    scope.querySelectorAll('[data-scramble-hover="link"]').forEach((link) => {
      const target = link.querySelector('[data-scramble-hover="target"]');
      if (!target) return;
      splitIntoWords(target);
      const originals = new Map();
      target.querySelectorAll("[data-scramble-word]").forEach((w) => originals.set(w, w.textContent));

      let active = null;
      const onEnter = () => {
        if (active) active();
        active = playScramble(target);
      };
      const onLeave = () => {
        if (active) active();
        active = null;
        originals.forEach((orig, w) => (w.textContent = orig));
      };
      link.addEventListener("mouseenter", onEnter);
      link.addEventListener("focusin", onEnter);
      link.addEventListener("mouseleave", onLeave);
      link.addEventListener("focusout", onLeave);
      cleanups.push(() => {
        link.removeEventListener("mouseenter", onEnter);
        link.removeEventListener("focusin", onEnter);
        link.removeEventListener("mouseleave", onLeave);
        link.removeEventListener("focusout", onLeave);
        if (active) active();
      });
    });

    return () => cleanups.forEach((fn) => fn());
  }, [scopeRef, site]);
}
