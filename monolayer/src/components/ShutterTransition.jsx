import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Scroll-scrubbed row of solid bars that scale up to cover the leading edge
// of the following section (the site's signature section transition).
// rows=10, scrub 0.3, stagger 0.22, theme flips nav theme at progress 0.4.
export default function ShutterTransition({ variant = "indigo", theme = "nav:indigo", rows = 10 }) {
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
            end: "bottom top-=30%",
            scrub: 0.3,
          },
        }
      );

      // theme flip of the sticky nav at progress 0.4
      const navEl = document.querySelector(".nav");
      if (navEl) {
        ScrollTrigger.create({
          trigger: el,
          start: "top bottom",
          end: "bottom top-=30%",
          onUpdate: (self) => {
            const themeName = theme.replace("nav:", "");
            navEl.classList.remove("theme-base", "theme-indigo", "theme-olive");
            navEl.classList.add(self.progress >= 0.4 ? `theme-${themeName}` : "theme-base");
          },
        });
      }
    });
    return () => ctx.revert();
  }, [theme]);

  return (
    <div
      className="shutter-scroll-transition"
      data-shutter-scroll-transition
      data-mode="cover"
      data-rows={rows}
      data-theme-change={theme}
      data-theme-progress="0.4"
      data-scroll-end="bottom top-=30%"
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
