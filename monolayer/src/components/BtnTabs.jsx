import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { CropmarkSet } from "./icons.jsx";

const MOVE_DURATION = 0.42;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// Faithful tabs: a frame of corner cropmarks that slides over whichever tab
// is active or hovered. Mirrors the original .btn-tabs__cropmarks behavior.
export default function BtnTabs({ items, active, onSelect, horizontal = false }) {
  const listRef = useRef(null);
  const [hovered, setHovered] = useState(null);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const crop = list.querySelector(".btn-tabs__cropmarks");
    const triggers = Array.from(list.querySelectorAll(".btn-tabs"));
    if (!crop || !triggers.length) return;

    const move = () => {
      const index = hovered != null ? items.findIndex((it) => it.key === hovered) : items.findIndex((it) => it.key === active);
      const target = triggers[index] || triggers[0];
      if (!target) return;
      const lr = list.getBoundingClientRect();
      const tr = target.getBoundingClientRect();
      if (lr.width <= 0 || tr.width <= 0) return;
      const x = tr.left - lr.left;
      const y = tr.top - lr.top;
      const width = tr.width;
      const height = tr.height;
      const ready = crop.getAttribute("data-tabs-cropmarks-ready") === "true";
      gsap.killTweensOf(crop);
      if (!ready) {
        gsap.set(crop, { x, y, width, height, autoAlpha: 1 });
        crop.setAttribute("data-tabs-cropmarks-ready", "true");
      } else {
        gsap.to(crop, {
          x, y, width, height, autoAlpha: 1,
          duration: prefersReducedMotion() ? 0 : MOVE_DURATION,
          ease: "main",
          overwrite: true,
        });
      }
    };

    move();
    window.addEventListener("resize", move);
    return () => {
      window.removeEventListener("resize", move);
      gsap.killTweensOf(crop);
    };
  }, [hovered, active, items]);

  return (
    <div
      className={`btn-tabs__list${horizontal ? " is--horizontal" : ""}`}
      ref={listRef}
    >
      <div className="btn-tabs__cropmarks">
        <CropmarkSet />
      </div>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          className="btn-tabs"
          aria-selected={item.key === active}
          onMouseEnter={() => setHovered(item.key)}
          onMouseLeave={() => setHovered(null)}
          onFocus={() => setHovered(item.key)}
          onBlur={() => setHovered(null)}
          onClick={() => onSelect(item.key)}
        >
          <span className="btn-tabs__text">{item.label}</span>
        </button>
      ))}
    </div>
  );
}