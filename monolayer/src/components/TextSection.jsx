import { useRef } from "react";
import { useScramble } from "../anim/useScramble.js";
import ShutterTransition from "./ShutterTransition.jsx";

function Chapter({ text }) {
  return (
    <div className="chapter">
      <span className="chapter__text">{text}</span>
      <div className="chapter__wrap">
        <div className="chapter__line" />
        <div className="chapter__box" />
        <div className="chapter__line" />
      </div>
    </div>
  );
}

export default function TextSection({
  sectionKey,
  chapter,
  blocks,
  spaceTop = "regular",
  spaceBottom = "regular",
  shutterVariant = "indigo",
  shutterTheme = "nav:indigo",
}) {
  const scopeRef = useRef(null);
  useScramble(scopeRef, null);

  return (
    <section
      className="text section"
      data-nav-progress-section={sectionKey || undefined}
      data-theme-change="nav:base"
      ref={scopeRef}
    >
      <div className="container">
        <div className="text__wrapper" space-top={spaceTop} space-bottom={spaceBottom}>
          {blocks.map((b, i) => (
            <div className="text__component" key={i}>
              {i === 0 && chapter ? <Chapter text={chapter} /> : null}
              <h2 className="heading-m" data-scramble="scroll" text-indent={String(b.indent)}>
                {b.text}
              </h2>
            </div>
          ))}
        </div>
      </div>
      <ShutterTransition variant={shutterVariant} theme={shutterTheme} />
    </section>
  );
}
