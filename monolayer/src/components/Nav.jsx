import { useRef, useState } from "react";
import { LogoNav } from "./icons.jsx";
import { useSite } from "../SiteContext.jsx";
import { useScramble } from "../anim/useScramble.js";
import BtnTabs from "./BtnTabs.jsx";

const PROGRESS_ITEMS = ["absurdity", "system", "how it works", "different", "pricing"];
const MENU_ITEMS = ["absurdity", "system", "how it works", "pricing"];

export default function Nav() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuTab, setMenuTab] = useState("product");
  const scopeRef = useRef(null);
  const { lenis } = useSite();
  useScramble(scopeRef, null);

  const jumpTo = (label) => {
    const el = document.querySelector(`[data-nav-progress-section="${label}"]`);
    if (el) {
      const nav = document.querySelector(".nav");
      const offset = nav ? nav.offsetHeight : 0;
      const top = Math.max(
        0,
        el.getBoundingClientRect().top + (window.scrollY || window.pageYOffset) - offset
      );
      if (lenis) lenis.scrollTo(top);
      else window.scrollTo({ top, behavior: "smooth" });
    }
    setMenuOpen(false);
  };

  return (
    <nav className="nav theme-base" data-theme-target="nav" ref={scopeRef}>
      <div className="nav__main">
        <div className="nav-main__menu">
          <div
            className="menu__btn"
            data-menu-trigger
            role="button"
            aria-label="Menu"
            aria-expanded={menuOpen}
            tabIndex={0}
            onClick={() => setMenuOpen(!menuOpen)}
            onKeyDown={(e) => e.key === "Enter" && setMenuOpen(!menuOpen)}
          >
            <div className="menu-icon">
              <div className="menu-line" />
              <div className="menu-line" />
            </div>
          </div>
        </div>
        <div className="nav-main__logo-wrap">
          <a className="logo__btn" href="/" aria-label="Home" onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
            <LogoNav />
          </a>
        </div>
        <div className="nav-main__progress" data-nav-progress>
          <div className="nav-main__progress-bar" />
          <div className="nav-main__progress-labels">
            {PROGRESS_ITEMS.map((label, i) => (
              <a
                key={label}
                href="#"
                className={`nav-main__progress-item paragraph-m${i === 0 ? " is-active" : ""}`}
                data-nav-progress-item={label}
                data-scramble-hover="link"
                aria-current={i === 0 ? "true" : "false"}
                onClick={(e) => { e.preventDefault(); jumpTo(label); }}
              >
                <span data-scramble-hover="target" data-nav-progress-label>{label}</span>
              </a>
            ))}
          </div>
        </div>
        <div className="nav-main__docs">
          <a className="paragraph-m" href="https://app.monolayer.dev/docs" target="_blank" rel="noreferrer" data-scramble-hover="link" data-scramble-intensity="3">
            <span data-scramble-hover="target">[ documentation ]</span>
          </a>
        </div>
        <div className="nav-main__signin paragraph-m">
          <p data-scramble-hover="link">
            <a className="absolute-link" href="https://app.monolayer.dev/sign-up" target="_blank" rel="noreferrer" aria-label="sign up" />
            <span data-scramble-hover="target">sign up</span>
          </p>
          <p>/</p>
          <p data-scramble-hover="link">
            <a className="absolute-link" href="https://app.monolayer.dev/log-in" target="_blank" rel="noreferrer" aria-label="log in" />
            <span data-scramble-hover="target">log in</span>
          </p>
        </div>
      </div>

      {/* fullscreen menu */}
      <div className={`nav__menu${menuOpen ? " open" : ""}`}>
<div className="menu__top">
          <BtnTabs
            horizontal
            items={[
              { key: "product", label: "product" },
              { key: "news", label: "news" },
            ]}
            active={menuTab}
            onSelect={setMenuTab}
          />
          </div>
        <div className="menu-tabs__list">
          {menuTab === "product" ? (
            <div className="menu-tabs__item">
              {MENU_ITEMS.map((label) => (
                <a key={label} className="menu__link" href="#" onClick={(e) => { e.preventDefault(); jumpTo(label); }}>
                  <span className="menu__link-text heading-xl text-align-center">{label}</span>
                </a>
              ))}
            </div>
          ) : (
            <div className="menu-tabs__item">
              <a className="menu__link" href="#"><span className="menu__link-text heading-xl text-align-center">Why monolayer</span></a>
              <a className="menu__link" href="#"><span className="menu__link-text heading-xl text-align-center">Create your account</span></a>
              <a className="menu__link" href="#"><span className="menu__link-text heading-xl text-align-center">The SDLC Didn't Break</span></a>
              <a className="menu__link" href="#"><span className="menu__link-text heading-xl text-align-center">all news</span></a>
            </div>
          )}
        </div>
        <div className="nav-main__signin is--mobile paragraph-m">
          <p><span>sign up</span></p>
          <p>/</p>
          <p><span>log in</span></p>
        </div>
        <div className="menu__social-links paragraph-m">
          <p><a href="https://x.com/monolayer_dev" target="_blank" rel="noreferrer" aria-label="X"><span>X</span></a></p>
          <p><a href="https://www.linkedin.com/company/monolayer/" target="_blank" rel="noreferrer" aria-label="LinkedIn"><span>LinkedIn</span></a></p>
        </div>
        <div className="menu__cta">
          <a className="main-btn" href="https://app.monolayer.dev/sign-up" target="_blank" rel="noreferrer">
            <p className="main-btn__text">turn on autopilot</p>
            <div className="main-btn__box"><ArrowInline /></div>
          </a>
          <p className="paragraph-m menu__cta-note">try monolayer</p>
        </div>
      </div>

      <div className="nav__bg" />
      <div className="nav__border-bottom" />
    </nav>
  );
}

function ArrowInline() {
  // lazy import to avoid circular jsx from icons
  const rects = [[6,0],[9,0],[12,0],[0,3],[3,3],[6,3],[9,3],[12,3],[0,6],[3,6],[6,6],[9,6],[12,6],[0,9],[3,9],[6,9],[9,9],[12,9],[0,12],[3,12],[6,12],[9,12],[12,12],[3,0],[0,0]];
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 14 14" fill="none" className="arrow-icon">
      {rects.map(([x, y], i) => <rect key={i} x={x} y={y} width="2" height="2" fill="currentColor" />)}
    </svg>
  );
}
