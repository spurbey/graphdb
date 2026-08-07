import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useScramble } from "../anim/useScramble.js";

const STEPS = [
  {
    title: "connect your cloud",
    sub: "install monolayer in your AWS account.",
    number: "01",
  },
  {
    title: "connect your repository",
    sub: "link your github or gitlab project.",
    number: "02",
  },
  {
    title: "push intent",
    sub: "push code or describe a change. monolayer turns it into a running, managed system.",
    number: "03",
  },
];

// Sticky-column track: wrapper scrolls up (collectionTravel) while inner list
// travels down (−listTravel), both scrubbed. Faithful to howTrack.js config.
// start "top 30%" / end "bottom 60%" / scrub 0.075.
export default function How() {
  const scopeRef = useRef(null);
  const wrapperRef = useRef(null);
  const trackRef = useRef(null);
  const listRef = useRef(null);
  useScramble(scopeRef, null);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const track = trackRef.current;
    const list = listRef.current;
    if (!wrapper || !track || !list) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const getCollectionTravel = () => Math.max(0, wrapper.clientHeight - track.offsetHeight);
    const getListTravel = () => {
      const lastItem = list.lastElementChild;
      if (!lastItem) return 0;
      const listRect = list.getBoundingClientRect();
      const lastItemRect = lastItem.getBoundingClientRect();
      const realContentHeight = lastItemRect.bottom - listRect.top;
      return Math.max(0, realContentHeight - track.clientHeight);
    };

    const ctx = gsap.context(() => {
      gsap.set([track, list], { y: 0, willChange: "transform", force3D: true });
      if (reduced) {
        gsap.set([track, list], { clearProps: "transform,willChange" });
        return;
      }
      const tl = gsap.timeline({
        defaults: { ease: "none" },
        scrollTrigger: {
          trigger: wrapper,
          start: "top 30%",
          end: "bottom 60%",
          scrub: 0.075,
          invalidateOnRefresh: true,
        },
      });
      tl.to(track, { y: getCollectionTravel, duration: 1 }, 0);
      tl.to(list, { y: () => -getListTravel(), duration: 1 }, 0);
    }, wrapper);

    const observer = new ResizeObserver(() => requestAnimationFrame(() => ScrollTrigger.refresh()));
    [wrapper, track, list, ...list.children].forEach((n) => observer.observe(n));

    return () => {
      observer.disconnect();
      ctx.revert();
    };
  }, []);

  return (
    <section
      className="how"
      data-nav-progress-section="how it works"
      data-theme-change="nav:base"
      ref={scopeRef}
    >
      <div className="container">
        <div className="how__wrapper" space-top="s" space-bottom="xl">
          <div className="how__collection-wrapper" data-how-track-wrapper ref={wrapperRef}>
            <div className="how__collection" data-how-track ref={trackRef}>
              <div className="how__list" role="list" ref={listRef}>
                {STEPS.map((step) => (
                  <div className="how__item" role="listitem" key={step.number}>
                    <div className="how__step-info">
                      <h2 className="paragraph-m" data-scramble data-scramble-intensity text-indent="">
                        {step.title}
                      </h2>
                      <p className="paragraph-m opacity-64">{step.sub}</p>
                    </div>
                    <div className="how__step-number">
                      <p className="how__step">{step.number}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="how__text">
            <p className="heading-m" text-indent="2" data-scramble data-scramble-intensity>
              monolayer connects to your repository and cloud, then takes over the layer between writing
              software and having it run properly.
            </p>
            <p className="heading-m" text-indent="0" data-scramble data-scramble-intensity>
              what used to be spread across setup, deployment, and ongoing system work becomes part of one
              continuous system.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}