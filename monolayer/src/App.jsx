import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { CustomEase } from "gsap/CustomEase";
import Lenis from "lenis";
import { SiteStateContext } from "./SiteContext.jsx";

import Nav from "./components/Nav.jsx";
import Hero from "./components/Hero.jsx";
import TextSection from "./components/TextSection.jsx";
import System from "./components/System.jsx";
import How from "./components/How.jsx";
import Different from "./components/Different.jsx";
import Pricing from "./components/Pricing.jsx";
import Footer from "./components/Footer.jsx";
import Modal from "./components/Modal.jsx";
import SectionNavTracker from "./components/SectionNavTracker.jsx";

import { registerEasings } from "./anim/easings.js";

gsap.registerPlugin(ScrollTrigger, CustomEase);
registerEasings();

export default function App() {
  const [site] = useState(() => ({
    lenis: null,
    reducedMotion:
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    isTouch:
      typeof window !== "undefined" &&
      window.matchMedia("(pointer: coarse)").matches,
  }));
  const lenisRef = useRef(null);

  useEffect(() => {
    const lenis = new Lenis({
      lerp: site.isTouch ? 1 : 0.14,
      wheelMultiplier: site.isTouch ? 1 : 1.25,
      touchMultiplier: 1,
    });
    lenisRef.current = lenis;
    site.lenis = lenis;

    lenis.on("scroll", ScrollTrigger.update);
    const raf = (time) => lenis.raf(time * 1000);
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    window.scrollTo(0, 0);

    // refresh after fonts/layout settle
    const t = setTimeout(() => ScrollTrigger.refresh(), 600);

    return () => {
      clearTimeout(t);
      gsap.ticker.remove(raf);
      lenis.destroy();
      site.lenis = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SiteStateContext.Provider value={site}>
      <div className="page">
        <Nav />
        <SectionNavTracker />
        <main className="main">
          <Hero />
          <TextSection
            sectionKey="absurdity"
            chapter="the absurdity"
            blocks={[
              {
                indent: 5,
                text: "writing code is only one part of getting software live. setup, deployment, scaling, recovery, and system health still sit across separate tools, decisions, and ongoing manual work. the code may be modern. the operating model still is not.",
              },
              {
                indent: 0,
                text: "monolayer takes code or intent and turns it into a running, self-managed system in your own cloud. not another tool in the chain. the execution layer behind it.",
              },
            ]}
            spaceTop="xxl"
            spaceBottom="xl"
          />
          <System />
          <How />
          <Different />
          <TextSection
            sectionKey={null}
            chapter="with monolayer"
            blocks={[
              {
                indent: 5,
                text: "you work from intent, not infrastructure. the system understands what needs to happen and carries it forward.",
              },
            ]}
            spaceTop="s"
            spaceBottom="xl"
            shutterVariant="olive"
            shutterTheme="nav:olive"
          />
          <Pricing />
          <Footer />
        </main>
        <Modal />
      </div>
    </SiteStateContext.Provider>
  );
}
