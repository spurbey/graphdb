import { useState } from "react";
import { LogoFooter, CropmarkSet, EnterIcon } from "./icons.jsx";

const PRODUCT_LINKS = ["absurdity", "system", "how it works", "pricing"];

const NEWS_LINKS = [
  { label: "create a account", href: "https://monolayer-demo.webflow.io/news/create-your-monolayer-account", aria: "create a account" },
  { label: "all news", href: "/news" },
];

function FooterTabs({ tab, setTab }) {
  return (
    <>
      <div className="footer-nav__btn-list">
        <div className="btn-tabs__list">
          <div className="btn-tabs__cropmarks">
            <CropmarkSet />
          </div>
          <button className="btn-tabs" onClick={() => setTab("product")}>
            <span className="btn-tabs__text">product</span>
          </button>
          <button className="btn-tabs" onClick={() => setTab("news")}>
            <span className="btn-tabs__text">news</span>
          </button>
        </div>
      </div>
      <div className="footer-nav__list">
        <div className="footer-tabs__list">
          {tab === "product" ? (
            <div className="footer-tabs__item">
              {PRODUCT_LINKS.map((label) => (
                <button className="footer-nav__item" data-underline-link="static" key={label}>
                  <a
                    className="footer-nav__item-link"
                    href="/"
                    aria-current="page"
                    data-jump-to={label}
                    onClick={(e) => e.preventDefault()}
                  />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="footer-tabs__item">
              {NEWS_LINKS.map((link) => (
                <button className="footer-nav__item" data-underline-link="static" key={link.label}>
                  <a className="footer-nav__item-link" href={link.href} aria-label={link.aria} />
                  <span>{link.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default function Footer() {
  const [tab, setTab] = useState("product");

  return (
    <footer className="footer" data-theme-change="nav:base">
      <div className="container">
        <div className="footer__nav" space-top="regular">
          <div className="statement">
            <p className="paragraph-m">
              {"\u201cwe started monolayer because software should not need people standing behind it to keep it alive.\u201d"}
            </p>
            <div className="statement__author">
              <div className="statement-author__line" />
              <p className="paragraph-regular">Dani and Marc, co-founders</p>
            </div>
          </div>
          <FooterTabs tab={tab} setTab={setTab} />
        </div>

        <div className="footer__btm" space-bottom="xs" space-top="xl">
          <div className="footer__social-links">
            <p>
              <a href="https://x.com/monolayer_dev" target="_blank" rel="noreferrer" aria-label="X">
                X
              </a>
            </p>
            <p>
              <a
                href="https://www.linkedin.com/company/monolayer/"
                target="_blank"
                rel="noreferrer"
                aria-label="LinkedIn"
              >
                LinkedIn
              </a>
            </p>
          </div>
          <div className="footer__contact">
            <p>
              <a href="mailto:hello@monolayer.dev" aria-label="hello@monolayer.dev">
                <span>hello@monolayer.dev</span>
              </a>
            </p>
            <div className="footer-contact__or">
              <EnterIcon />
            </div>
            <p className="custom-paragraph" data-copy-value="hello@monolayer.dev">
              <a href="#" aria-label="copy " onClick={(e) => e.preventDefault()}>
                <span>copy </span>
              </a>
            </p>
          </div>
          <a className="footer__logo" href="/" aria-label="Home" aria-current="page">
            <LogoFooter />
          </a>
          <div className="footer__legals">
            <p>
              <a href="/terms" aria-label="Terms">Terms</a>
            </p>
            <p>
              <a href="/privacy" aria-label="Privacy">Privacy</a>
            </p>
            <p className="paragraph-regular">{"\u00a9 2026, monolayer, Inc."}</p>
          </div>
          <div className="footer__made-by">
            <p>
              <a href="https://www.raviklaassens.com/" target="_blank" rel="noreferrer" aria-label="Made in Barcelona, website by R—K">
                <span>Made in Barcelona, website by R{"\u2014"}K</span>
              </a>
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}