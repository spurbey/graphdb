import { useRef } from "react";
import { useScramble } from "../anim/useScramble.js";
import { ArrowIcon } from "./icons.jsx";

// The "topography" 3D visual on the original is an embedded Unicorn Studio
// WebGL scene. This recreation injects the same SDK + project for 1:1 parity,
// with graceful fallback (empty box) if the CDN is unreachable.
const UNICORN_SDK = "https://cdn.jsdelivr.net/gh/hiunicornstudio/unicornstudio.js@v2.1.11/dist/unicornStudio.umd.js";
const UNICORN_PROJECT = "Pehlshd0BZceYruyqIGJ?update=dev-008";

function UnicornVisual() {
  const mountRef = useRef(null);

  // load SDK + init scene once
  const init = async () => {
    const el = mountRef.current;
    if (!el || el.__unicornInit) return;
    el.__unicornInit = true;
    if (!el.id) el.id = `unicorn-${Math.random().toString(36).slice(2, 9)}`;

    if (!window.UnicornStudio) {
      await new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = UNICORN_SDK;
        s.onload = resolve;
        s.onerror = resolve;
        document.head.appendChild(s);
      });
    }
    try {
      await window.UnicornStudio?.addScene?.({
        elementId: el.id,
        projectId: UNICORN_PROJECT,
        scale: 1,
        dpi: 1.5,
        fps: 60,
        lazyLoad: true,
        production: false,
        fixed: false,
        altText: "Monolayer system visual",
        ariaLabel: "Animated Monolayer system visual",
        interactivity: { mouse: { disableMobile: false, disabled: false } },
      });
    } catch {
      /* CDN unreachable — leave placeholder */
    }
  };

  return (
    <div className="unicorn">
      <div className="unicorn-visual">
        <div
          className="unicorn-visual__item"
          data-unicorn-init
          ref={(node) => {
            mountRef.current = node;
            if (node) init();
          }}
        />
      </div>
    </div>
  );
}

export default function Hero() {
  const scopeRef = useRef(null);
  useScramble(scopeRef, null);

  return (
    <section className="hero theme-base" data-theme-change="nav:base" ref={scopeRef}>
      <div className="intro">
        <div className="container">
          <div className="hero__content">
            <h1 className="heading-l" data-scramble="load" data-scramble-intensity="5" text-indent="">
              monolayer shifts software toward what it should be: a system that runs, maintains, and scales itself.
            </h1>
            <div className="hero__usps">
              <p className="paragraph-m" data-scramble="load" data-scramble-intensity="5">no manual setup</p>
              <p className="paragraph-m text-align-right hide-tablet-mobile" data-scramble="load" data-scramble-intensity="5">monolayer©2026</p>
              <p className="paragraph-m" data-scramble="load" data-scramble-intensity="5">runs in your cloud</p>
            </div>
          </div>
        </div>
      </div>
      <UnicornVisual />
      <div className="cta">
        <div className="cta__wrap">
          <a className="main-btn" href="https://app.monolayer.dev/sign-up" target="_blank" rel="noreferrer" data-scramble-hover="link">
            <p className="main-btn__text" data-scramble-hover="target">turn on autopilot</p>
            <div className="main-btn__box"><ArrowIcon /></div>
          </a>
        </div>
      </div>
    </section>
  );
}
