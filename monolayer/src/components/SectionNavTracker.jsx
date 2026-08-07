import { useEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Scroll-progress tracker for the fixed nav (matches nav.js [data-nav-progress]).
// Each labelled section has an underline that fills as you scroll through it;
// the single nav bar shows overall page progress and which section is active.
export default function SectionNavTracker() {
  useEffect(() => {
    const bar = document.querySelector(".nav-main__progress-bar");
    if (!bar) return;

    const ctx = gsap.context(() => {
      ScrollTrigger.create({
        trigger: document.body,
        start: "top top",
        end: "bottom bottom",
        scrub: true,
        onUpdate: (self) => {
          bar.style.transform = `scaleX(${self.progress})`;
        },
      });

      // per-section underline progress
      document.querySelectorAll("[data-nav-progress-section]").forEach((section) => {
        ScrollTrigger.create({
          trigger: section,
          start: "top 60%",
          end: "bottom 40%",
          onUpdate: (self) => {
            document.querySelectorAll("[data-nav-progress-label]").forEach((label) => {
              const labelActive = false;
              // scale each matching underline by the section's own progress
              if (label.textContent === (section.dataset.navProgressSection || "")) {
                label.style.setProperty("--nav-progress", self.progress);
              } else if (!labelActive) {
                label.style.setProperty("--nav-progress", "0");
              }
            });
          },
        });
      });
    });

    return () => ctx.revert();
  }, []);

  return null;
}