import React, { useRef, useLayoutEffect } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import './index.css';

gsap.registerPlugin(ScrollTrigger);

export default function App() {
  const heroRef = useRef();
  const heroTextRef = useRef();
  
  const introWrapRef = useRef();
  const introTextRef = useRef();
  const statsRef = useRef();

  const expertWrapRef = useRef();
  const featuresRef = useRef();

  useLayoutEffect(() => {
    let ctx = gsap.context(() => {
      
      // 1. HERO ANIMATION (Translates up and fades out as you scroll down the empty block)
      gsap.to(heroTextRef.current, {
        scrollTrigger: {
          trigger: heroRef.current,
          start: "top top",
          end: "+=100%", // based on the empty block size
          scrub: 1,
        },
        y: -150,
        opacity: 0,
        scale: 0.95
      });

      // 2. INTRO STATS ANIMATION (Fades in as it becomes pinned)
      const tlIntro = gsap.timeline({
        scrollTrigger: {
          trigger: introWrapRef.current,
          start: "top top",
          end: "+=100%",
          scrub: 1,
        }
      });
      tlIntro.fromTo(introTextRef.current, 
        { opacity: 0, y: 100 }, 
        { opacity: 1, y: 0, duration: 1 }
      )
      .fromTo(statsRef.current.children, 
        { opacity: 0, y: 50 }, 
        { opacity: 1, y: 0, duration: 1, stagger: 0.2 },
        "-=0.5"
      )
      .to(introTextRef.current, { opacity: 0, y: -50, duration: 1 }, "+=1")
      .to(statsRef.current, { opacity: 0, y: -50, duration: 1 }, "<");


      // 3. EXPERT FEATURES ANIMATION
      const tlExpert = gsap.timeline({
        scrollTrigger: {
          trigger: expertWrapRef.current,
          start: "top top",
          end: "+=150%",
          scrub: 1,
        }
      });
      
      tlExpert.fromTo(featuresRef.current.children, 
        { opacity: 0, x: -50 },
        { opacity: 1, x: 0, duration: 1, stagger: 1 }
      );

    });
    return () => ctx.revert();
  }, []);

  return (
    <div className="main-wrapper">
      <header>
        <div className="logo">GRAPHDB</div>
      </header>

      {/* SECTION 1: HERO */}
      <div className="section-wrap" ref={heroRef}>
        <section className="section-sticky">
          <div ref={heroTextRef} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <h1 className="heading-massive">CODE INTELLIGENCE.<br/>PRECISELY MAPPED.</h1>
            <p className="subtitle-center">
              World-class structural intelligence delivered by a graph database that treats your architecture like a neural network.
            </p>
          </div>
        </section>
        {/* The spacer that dictates how long the sticky stays */}
        <div className="spacer-150vh"></div> 
      </div>

      {/* SECTION 2: INTRO & STATS */}
      <div className="section-wrap" ref={introWrapRef}>
        <section className="section-sticky" style={{ flexDirection: 'column' }}>
          <div ref={introTextRef} style={{ marginBottom: '6rem' }}>
            <h2 className="heading-h2 text-center">EARNED IN THE GRAPH</h2>
          </div>
          <div className="stat-grid" ref={statsRef}>
            <div className="stat-item">
              <div className="stat-num">4.03</div>
              <div className="stat-label">MS TRACE RESPONSE</div>
            </div>
            <div className="stat-item">
              <div className="stat-num">100</div>
              <div className="stat-label">% DETERMINISTIC</div>
            </div>
            <div className="stat-item">
              <div className="stat-num">0</div>
              <div className="stat-label">% FLAKE RATE</div>
            </div>
          </div>
        </section>
        <div className="spacer-200vh"></div>
      </div>

      {/* SECTION 3: EXPERTISE */}
      <div className="section-wrap" ref={expertWrapRef}>
        <section className="section-sticky">
          <div className="feature-list" ref={featuresRef}>
            <div className="feature-item">
              <div className="feature-title">SEMANTIC VECTOR SEARCH</div>
              <div className="feature-desc">Matches natural language intent directly to function states. Suppresses god-node contamination.</div>
            </div>
            <div className="feature-item">
              <div className="feature-title">TIME-TRAVEL DIFF</div>
              <div className="feature-desc">Maintains PREVIOUS_VERSION edges. Instantly compare current and prior structural behaviors without git checkouts.</div>
            </div>
            <div className="feature-item">
              <div className="feature-title">VULNERABILITY TRACE</div>
              <div className="feature-desc">Finds all caller paths up to N hops deep in milliseconds. Ensure your agent never breaks production.</div>
            </div>
          </div>
        </section>
        <div className="spacer-200vh"></div>
      </div>

    </div>
  );
}
