import { useEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Fixed-nav scroll tracker, matching the original nav.js behavior:
//  - the bar fills as the page scrolls
//  - the section label row slides left with overall progress, so the current
//    section's label stays in view within the bordered strip
//  - the label matching the section you're in gets .is-active (smooth underline)
// Each labelled section drives its own activation point via its DOM position.
export default function SectionNavTracker() {
  useEffect(() => {
    const progress = document.querySelector("[data-nav-progress]");
    const bar = progress?.querySelector(".nav-main__progress-bar");
    const labels = progress?.querySelector(".nav-main__progress-labels");
    if (!progress || !bar || !labels) return;

    const sections = Array.from(
      document.querySelectorAll("[data-nav-progress-section]")
    ).filter((section) => section.getAttribute("data-nav-progress-section")?.trim());
    const items = Array.from(progress.querySelectorAll("[data-nav-progress-item]"));
    if (!sections.length || !items.length) return;

    const ctx = gsap.context(() => {
      let maxLabelTranslate = Math.max(0, labels.scrollWidth - progress.clientWidth);

      const remeasure = () => {
        maxLabelTranslate = Math.max(0, labels.scrollWidth - progress.clientWidth);
      };
      ScrollTrigger.addEventListener("refreshInit", remeasure);

      const applyActive = (index) => {
        const active = Math.min(index, items.length - 1);
        items.forEach((item, i) => {
          const isActive = i === active;
          item.classList.toggle("is-active", isActive);
          item.setAttribute("aria-current", isActive ? "true" : "false");
          if (isActive) item.setAttribute("data-nav-progress-current", "true");
          else item.removeAttribute("data-nav-progress-current");
        });
      };

      const onUpdate = (self) => {
        const scrollY = self.scroll();
        bar.style.width = `${self.progress * 100}%`;
        gsap.set(labels, { x: -maxLabelTranslate * self.progress, force3D: true });

        // last section whose top has reached the bottom of the nav strip
        const navBottom = progress.getBoundingClientRect().bottom;
        let index = 0;
        for (let i = 0; i < sections.length; i++) {
          const top = sections[i].getBoundingClientRect().top + scrollY;
          if (top - navBottom <= 0) index = i;
        }
        applyActive(index);
      };

      ScrollTrigger.create({
        trigger: document.body,
        start: "top top",
        end: "bottom bottom",
        scrub: true,
        onUpdate,
      });

      // initialize without waiting for the first scroll
      requestAnimationFrame(() => {
        onUpdate({ progress: 0, scroll: () => window.scrollY || 0 });
      });
    });

    return () => ctx.revert();
  }, []);

  return null;
}