import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Shutter placed directly ABOVE the system section (variant off-white / nav:base).
// Matches original: a wall of bars that scales up to cover the hero before system.
export default function PreSystemShutter({ rows = 10 }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const cells = el.querySelectorAll(".shutter-cell");
    const ctx = gsap.context(() => {
      gsap.fromTo(
        cells,
        { scaleY: 0 },
        {
          scaleY: 1,
          duration: 0.1,
          stagger: { amount: 0.22, from: "start" },
          ease: "none",
          scrollTrigger: {
            trigger: el,
            start: "top bottom",
            end: "bottom top-=52%",
            scrub: 0.3,
          },
        }
      );
    });
    return () => ctx.revert();
  }, []);

  return (
    <div
      className="shutter-scroll-transition"
      data-shutter-scroll-transition
      data-mode="cover"
      data-rows={rows}
      data-theme-change="nav:base"
      data-scroll-end="bottom top-=52%"
      ref={ref}
      aria-hidden="true"
    >
      <div className="shutter-cells" style={{ gridTemplateColumns: `repeat(${rows}, 1fr)` }}>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="shutter-cell" />
        ))}
      </div>
    </div>
  );
}