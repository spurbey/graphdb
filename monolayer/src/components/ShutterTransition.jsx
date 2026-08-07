import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

const VARIANT_CLASS = {
  indigo: "w-variant-indigo",
  olive: "w-variant-olive",
  "off-white": "",
};

// Faithful port of the original initScrollTransition shutter.
// A wall of cells (rows x cols) that scales open from the bottom as you scroll
// through the containing section (cover mode). The wall takes the section color
// via the variant class; off-white uses the base theme background.
export default function ShutterTransition({
  variant = "off-white",
  theme = "nav:base",
  rows = 10,
  cols = 4,
  scrollEnd = "bottom top-=30%",
  isTop = false,
}) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const variantClass = VARIANT_CLASS[variant] || "";
    if (variantClass) el.classList.add(variantClass);
    const section = el.closest("section") || el.parentElement || el;
    const cells = Array.from(el.querySelectorAll("[data-shutter-scroll-cell]"));
    const fillCols = Array.from(el.querySelectorAll("[data-shutter-scroll-fill-col]"));

    // Bridge the inter-section seam (collapsing wrapper margins push the next
    // section down, but the wall anchors to its own section bottom). Measure the
    // real gap to the next section and drop the wall so its bottom edge lands
    // exactly on the following section's top — connecting it to the colored
    // section instead of floating above it.
    let next = section.nextElementSibling;
    while (next && next.dataset.themeChange === undefined && next.tagName !== "SECTION" && next.tagName !== "FOOTER") {
      next = next.nextElementSibling;
    }
    const applyBridge = () => {
      if (next && next.getBoundingClientRect) {
        const sectionRect = section.getBoundingClientRect();
        const nextRect = next.getBoundingClientRect();
        const seam = Math.max(0, nextRect.top - sectionRect.bottom);
        el.style.setProperty("--shutter-seam", `${seam}px`);
        el.style.bottom = `calc(-1 * var(--shutter-seam, 0px))`;
      }
    };
    applyBridge();
    window.addEventListener("resize", applyBridge);

    // Trigger off the *target* section's top edge (the colored section being
    // revealed), not the containing section's bottom. In the original the
    // sections are flush, so "bottom bottom" of the containing section and
    // "top bottom" of the next section are the same moment. With the seam the
    // containing bottom fires 270px early; the next top fires exactly when the
    // colored section is about to hit the screen.
    const triggerEl = next || section;
    const start = "top bottom";
    const end = scrollEnd.startsWith("top") ? scrollEnd : scrollEnd.replace(/^bottom/, "top");

    const ctx = gsap.context(() => {
      gsap.set(cells, { scaleY: 0, transformOrigin: "bottom center" });
      gsap.set(fillCols, { scaleY: 0, transformOrigin: "bottom center" });

      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: triggerEl,
          start,
          end,
          scrub: 0.3,
          invalidateOnRefresh: true,
        },
      });
      tl.to(cells, { scaleY: 1, duration: 0.1, ease: "none", stagger: { amount: 0.22, from: "end" } }, 0);
      tl.to(fillCols, { scaleY: 1, duration: 0.06, ease: "none", stagger: { amount: 0.1, from: "end" } }, 0.34);

      // theme flip of the sticky nav at progress 0.4
      const navEl = document.querySelector(".nav");
      if (navEl) {
        ScrollTrigger.create({
          trigger: triggerEl,
          start,
          end,
          onUpdate: (self) => {
            const themeName = theme.replace("nav:", "");
            navEl.classList.remove("theme-base", "theme-indigo", "theme-olive");
            navEl.classList.add(self.progress >= 0.4 ? `theme-${themeName}` : "theme-base");
          },
        });
      }
    });

    return () => {
      window.removeEventListener("resize", applyBridge);
      ctx.revert();
      if (variantClass) el.classList.remove(variantClass);
    };
  }, [theme, scrollEnd, variant]);

  return (
    <div
      className={`shutter-scroll-transition${isTop ? " is--top" : ""}`}
      data-shutter-scroll-transition
      data-mode="cover"
      data-rows={rows}
      data-cols={cols}
      data-theme-progress="0.4"
      data-theme-change={theme}
      data-scroll-end={scrollEnd}
      ref={ref}
      aria-hidden="true"
    >
      <div className="shutter-scroll-transition__panel" data-shutter-scroll-panel style={{ "--shutter-cols": cols }}>
        <div className="shutter-scroll-transition__fill" data-shutter-scroll-fill>
          {Array.from({ length: cols }).map((_, c) => (
            <div key={`fill-${c}`} className="shutter-scroll-transition__fill-col" data-shutter-scroll-fill-col />
          ))}
        </div>
        <div className="shutter-scroll-transition__grid" data-shutter-scroll-grid>
          {Array.from({ length: cols }).map((_, c) => (
            <div key={`col-${c}`} className="shutter-scroll-transition__col" data-shutter-scroll-col>
              {Array.from({ length: rows }).map((_, r) => (
                <div key={`${c}-${r}`} className="shutter-scroll-transition__cell" data-shutter-scroll-cell />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
