import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { CustomEase } from "gsap/CustomEase";
import { useScramble } from "../anim/useScramble.js";

const CHAPTERS = [
  { number: 1, tagline: "intent", sub: "repo / prompt", text: "① a change is introduced, monolayer sees the intent" },
  { number: 2, tagline: "logic", sub: "compute / data", text: "② monolayer determines what that change requires." },
  { number: 3, tagline: "validation", sub: "drift / safety", text: "③ before anything moves, the next state is checked." },
  { number: 4, tagline: "live", sub: "deploy / runtime", text: "④ the system takes shape in your cloud." },
  { number: 5, tagline: "feedback", sub: "load / recovery", text: "⑤ once live, it keeps reading runtime state." },
];

const STYLES = {
  chapterPadding: { top: "1em", right: "1em", bottom: "0px", left: "1em" },
  textPadding: { top: "1em", right: "1em", bottom: "2em", left: "1em" },
  stackChapterPadding: { top: "1em", right: "1em", bottom: "1em", left: "1em" },
  stackTextPadding: { top: "1em", right: "1em", bottom: "1em", left: "1em" },
};

const REVEAL = 0.46;
const PAD = 0.24;
const HOLD = 0.008;
const COLLAPSE = 0.64;
const BRIDGE = 0.34;
const SEQ3 = 1.45;
const SCROLL_VH = 460;

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const scaleLength = (value, p) => (Number.isFinite(parseFloat(value)) ? `${parseFloat(value) * p}px` : value);
const mixLength = (from, to, p) => {
  const f = parseFloat(from);
  const t = parseFloat(to);
  return Number.isFinite(f) && Number.isFinite(t) ? `${f + (t - f) * p}px` : to;
};

// Nested chapter tree — outermost carries ⑤, innermost carries ① (matches the
// original HTML structure the animation is built around).
function ChapterNode({ items, idx, chapterRefs, textRefs }) {
  const item = items[idx];
  return (
    <div className="system__chapter" ref={(n) => (chapterRefs.current[item.number - 1] = n)}>
      <div className="system__text" ref={(n) => (textRefs.current[item.number - 1] = n)}>
        <div className="system__chapter-tagline">
          <p className="paragraph-m" data-scramble="scroll" data-scramble-intensity="2">{item.tagline}</p>
          <p className="paragraph-m" data-scramble="scroll" data-scramble-intensity="2">{item.sub}</p>
        </div>
        <div className="system__chapter-headline">
          <h3 className="heading-m text-align-center" data-scramble="scroll" data-scramble-intensity="2">
            {item.text}
          </h3>
        </div>
      </div>
      {idx < items.length - 1 ? (
        <div className="system-visual__nested-wrap" data-system-visual-nested-wrap>
          <ChapterNode items={items} idx={idx + 1} chapterRefs={chapterRefs} textRefs={textRefs} />
        </div>
      ) : null}
    </div>
  );
}

export default function System() {
  const scopeRef = useRef(null);
  const wrapRef = useRef(null);
  const pinRef = useRef(null);
  const treeRef = useRef(null);
  const chapterRefs = useRef([]);
  const textRefs = useRef([]);
  useScramble(scopeRef, null);

  useEffect(() => {
    const scope = scopeRef.current;
    const wrap = wrapRef.current;
    const pin = pinRef.current;
    const tree = treeRef.current;
    const chapters = chapterRefs.current;
    const textNodes = textRefs.current;
    if (!scope || !wrap || !pin || !tree || !chapters.length || !textNodes.length) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    // register the bespoke eases used by this effect
    if (!gsap.parseEase("systemEaseOut")) CustomEase.create("systemEaseOut", "0.215, 0.61, 0.355, 1");
    if (!gsap.parseEase("systemChapterPop")) CustomEase.create("systemChapterPop", "0.34, 1.12, 0.64, 1");
    if (!gsap.parseEase("systemSequenceThree")) CustomEase.create("systemSequenceThree", "0.76, 0, 0.24, 1");
    if (!gsap.parseEase("systemSequenceNative")) CustomEase.create("systemSequenceNative", "0.22, 0, 0.78, 1");

    const items = textNodes.map((text, i) => ({
      number: i + 1,
      chapter: chapters[i],
      text,
      height: 0,
      stackHeight: 0,
    }));

    const readHeight = () => {
      items.forEach((item) => {
        const st = STYLES.textPadding;
        gsap.set(item.text, { height: "auto", paddingTop: st.top, paddingRight: st.right, paddingBottom: st.bottom, paddingLeft: st.left, overflow: "hidden" });
        item.height = Math.max(1, Math.ceil(item.text.offsetHeight));
        const sst = STYLES.stackTextPadding;
        gsap.set(item.text, { height: "auto", paddingTop: sst.top, paddingRight: sst.right, paddingBottom: sst.bottom, paddingLeft: sst.left, overflow: "hidden" });
        item.stackHeight = Math.max(1, Math.ceil(item.text.offsetHeight));
      });
    };

    const getItemCenter = (item) => {
      const textRect = item.text.getBoundingClientRect();
      const rootRect = chapters[chapters.length - 1].getBoundingClientRect();
      return textRect.top - rootRect.top + textRect.height / 2;
    };

    const setChapterPad = (item, p) => {
      const s = STYLES.chapterPadding;
      gsap.set(item.chapter, {
        paddingTop: scaleLength(s.top, p),
        paddingRight: scaleLength(s.right, p),
        paddingBottom: scaleLength(s.bottom, p),
        paddingLeft: scaleLength(s.left, p),
        overflow: "hidden",
      });
    };
    const setChapterStackPad = (item, p) => {
      const s = STYLES.stackChapterPadding;
      const c = STYLES.chapterPadding;
      gsap.set(item.chapter, {
        paddingTop: s.top,
        paddingRight: s.right,
        paddingBottom: mixLength(c.bottom, s.bottom, p),
        paddingLeft: s.left,
        overflow: "hidden",
      });
    };
    const setChapterSeqPad = (item) => {
      const s = STYLES.stackChapterPadding;
      gsap.set(item.chapter, {
        paddingTop: s.top,
        paddingRight: s.right,
        paddingBottom: s.bottom,
        paddingLeft: s.left,
        overflow: "hidden",
      });
    };
    const setText = (item, p, padTop, padRight, padBottom, padLeft) => {
      gsap.set(item.text, {
        height: Math.max(0, item.height * p),
        paddingTop: scaleLength(padTop, p),
        paddingRight: scaleLength(padRight, p),
        paddingBottom: scaleLength(padBottom, p),
        paddingLeft: scaleLength(padLeft, p),
        overflow: "hidden",
      });
    };
    const setStackText = (item, p) => {
      const s = STYLES.stackTextPadding;
      gsap.set(item.text, {
        height: Math.max(0, item.stackHeight * p),
        paddingTop: scaleLength(s.top, p),
        paddingRight: scaleLength(s.right, p),
        paddingBottom: scaleLength(s.bottom, p),
        paddingLeft: scaleLength(s.left, p),
        overflow: "hidden",
      });
    };
    const setTextToStack = (item, p) => {
      const t = STYLES.textPadding;
      const s = STYLES.stackTextPadding;
      gsap.set(item.text, {
        height: Math.max(0, item.height + (item.stackHeight - item.height) * p),
        paddingTop: mixLength(t.top, s.top, p),
        paddingRight: mixLength(t.right, s.right, p),
        paddingBottom: mixLength(t.bottom, s.bottom, p),
        paddingLeft: mixLength(t.left, s.left, p),
        overflow: "hidden",
      });
    };

    const setCollapsed = (item, collapsed) => {
      item.chapter.setAttribute("data-system-visual-collapsed", collapsed ? "true" : "false");
    };

    const ctx = gsap.context(() => {
      // ---- CSS hooks used by the visual ----
      wrap.style.height = "auto";
      pin.style.height = "100svh";

      const scrollDistance = () => Math.round((pin.clientHeight || window.innerHeight) * SCROLL_VH / 100);
      wrap.style.setProperty("--system-visual-scroll-distance", `${scrollDistance()}px`);
      wrap.style.height = `calc(100svh + ${scrollDistance()}px)`;

      let introTrigger = null;
      let mainTrigger = null;
      let renderMainProgress = null;
      let sequenceOrderActive = false;

      const reorderText = (textFirst) => {
        if (sequenceOrderActive === textFirst) return;
        sequenceOrderActive = textFirst;
        // chapter nodes: outermost (5) down to innermost (1)
        const nodeList = [chapters[chapters.length - 1], ...chapters.slice(0, chapters.length - 1).reverse()];
        nodeList.forEach((chapter) => {
          const children = Array.from(chapter.children);
          const text = children.find((c) => c.classList.contains("system__text"));
          const nested = children.find((c) => c.hasAttribute("data-system-visual-nested-wrap"));
          if (!text || !nested) return;
          if (textFirst) {
            if (text.nextElementSibling !== nested) chapter.insertBefore(text, nested);
          } else {
            if (nested.nextElementSibling !== text) chapter.insertBefore(text, nested.nextSibling);
          }
        });
      };

      const resetVisualState = () => {
        reorderText(false);
        items.forEach((item) => {
          setCollapsed(item, true);
          gsap.set(item.chapter, { paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
          gsap.set(item.text, { height: 0, paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
        });
        const first = items[0];
        setCollapsed(first, false);
        setChapterPad(first, 1);
        gsap.set(first.text, { height: "auto", ...STYLES.textPadding, overflow: "hidden" });
      };

      const renderVisibleState = (lastVisibleIndex, y) => {
        reorderText(false);
        items.forEach((item, index) => {
          const visible = index <= lastVisibleIndex;
          setCollapsed(item, !visible);
          if (visible) {
            setChapterPad(item, 1);
            gsap.set(item.text, { height: "auto", ...STYLES.textPadding, overflow: "hidden" });
          } else {
            gsap.set(item.chapter, { paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
            gsap.set(item.text, { height: 0, paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
          }
        });
        gsap.set(tree, { y });
      };

      const renderRevealState = (activeIndex, chapterProgress, revealProgress) => {
        reorderText(false);
        items.forEach((item, index) => {
          if (index < activeIndex) {
            setCollapsed(item, false);
            setChapterPad(item, 1);
            gsap.set(item.text, { height: "auto", ...STYLES.textPadding, overflow: "hidden" });
            return;
          }
          if (index === activeIndex) {
            setCollapsed(item, false);
            setChapterPad(item, chapterProgress);
            setText(item, revealProgress, STYLES.textPadding.top, STYLES.textPadding.right, STYLES.textPadding.bottom, STYLES.textPadding.left);
            return;
          }
          setCollapsed(item, true);
          gsap.set(item.chapter, { paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
          gsap.set(item.text, { height: 0, paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
        });
      };

      const renderCollapseState = (progress, leadIndex) => {
        reorderText(false);
        items.forEach((item) => setCollapsed(item, false));
        items.forEach((item, index) => {
          setChapterStackPad(item, progress);
          if (index === leadIndex) {
            setTextToStack(item, progress);
          } else {
            setText(item, 1 - progress, STYLES.textPadding.top, STYLES.textPadding.right, STYLES.textPadding.bottom, STYLES.textPadding.left);
          }
        });
      };

      const renderSequenceBridgeState = (progress, leadIndex) => {
        progress = clamp(progress);
        const switchPoint = 0.42;
        if (progress < switchPoint) {
          reorderText(false);
        } else {
          reorderText(true);
        }
        items.forEach((item, index) => {
          setCollapsed(item, false);
          setChapterSeqPad(item);
          if (index === leadIndex) setStackText(item, 1);
          else setStackText(item, 0);
        });
      };

      const renderSequenceThreeState = (progress, leadIndex, bottomIndex, topY, bottomY, focusY, easeFn) => {
        progress = clamp(progress);
        reorderText(true);
        const expandEnd = 0.24;
        const expandProgress = clamp(easeFn(progress / expandEnd));
        const scrollProgress = clamp((progress - expandEnd) / (1 - expandEnd));
        items.forEach((item, index) => {
          setCollapsed(item, false);
          setChapterSeqPad(item);
          setStackText(item, index === leadIndex ? 1 : expandProgress);
        });
        const liveTopY = getItemCenteredY(leadIndex, focusY);
        const stableTopY = Number.isFinite(liveTopY) ? liveTopY : topY;
        gsap.set(tree, { y: stableTopY + (bottomY - stableTopY) * scrollProgress });
      };

      const getItemCenteredY = (index, focusY) => focusY - getItemCenter(items[index]);

      const progressInDuration = (time, duration) => (duration <= 0 ? 1 : clamp(time / duration));
      const progressInRange = (p, start, end) => (end <= start ? (p >= end ? 1 : 0) : clamp((p - start) / (end - start)));

      const stepDur = Math.max(REVEAL, PAD);
      const revealSteps = items.length - 1;
      const totalDuration = HOLD + revealSteps * (stepDur + HOLD) + COLLAPSE + BRIDGE + SEQ3;

      // ---- main pinned renderer ----
      renderMainProgress = (progress) => {
        progress = clamp(progress);
        const time = progress * totalDuration;
        let cursor = 0;
        const focusY = (pin.clientHeight || window.innerHeight) * 0.5;
        const centers = items.map((item, i) => getItemCenteredY(i, focusY));

        if (time <= cursor + HOLD) {
          renderVisibleState(0, centers[0]);
          return;
        }
        cursor += HOLD;
        for (let index = 1; index < items.length; index++) {
          const segmentStart = cursor;
          const segmentEnd = segmentStart + stepDur;
          if (time <= segmentEnd) {
            const localTime = time - segmentStart;
            const chapterProgress = clamp(progressInDuration(localTime, PAD));
            const revealProgress = clamp(progressInDuration(localTime, REVEAL));
            const easePop = (v) => (typeof gsap.parseEase("systemChapterPop") === "function" ? gsap.parseEase("systemChapterPop")(v) : v);
            const easeOut = (v) => (typeof gsap.parseEase("systemEaseOut") === "function" ? gsap.parseEase("systemEaseOut")(v) : v);
            const easedChapterProgress = Math.max(0, easePop(chapterProgress));
            const easedRevealProgress = clamp(easeOut(revealProgress));
            renderRevealState(index, easedChapterProgress, easedRevealProgress);
            gsap.set(tree, { y: centers[index - 1] + (centers[index] - centers[index - 1]) * easedRevealProgress });
            return;
          }
          cursor = segmentEnd;
          if (time <= cursor + HOLD) {
            renderVisibleState(index, centers[index]);
            return;
          }
          cursor += HOLD;
        }
        if (time <= cursor + COLLAPSE) {
          const cp = clamp(progressInDuration(time - cursor, COLLAPSE));
          renderCollapseState(cp, items.length - 1);
          gsap.set(tree, { y: getItemCenteredY(items.length - 1, focusY) });
          return;
        }
        cursor += COLLAPSE;
        if (time <= cursor + BRIDGE) {
          const bp = clamp(progressInDuration(time - cursor, BRIDGE));
          renderSequenceBridgeState(bp, items.length - 1);
          gsap.set(tree, { y: getItemCenteredY(items.length - 1, focusY) });
          return;
        }
        cursor += BRIDGE;
        if (time <= cursor + SEQ3) {
          const sp = clamp(progressInDuration(time - cursor, SEQ3));
          const seqEase = (v) => (typeof gsap.parseEase("systemSequenceThree") === "function" ? gsap.parseEase("systemSequenceThree")(v) : v);
          const seqScrollEase = (v) => (typeof gsap.parseEase("systemSequenceNative") === "function" ? gsap.parseEase("systemSequenceNative")(v) : v);
          renderSequenceThreeState(sp, items.length - 1, 0, getItemCenteredY(items.length - 1, focusY), getItemCenteredY(0, focusY), focusY, seqEase);
          return;
        }
        renderSequenceThreeState(1, items.length - 1, 0, getItemCenteredY(items.length - 1, focusY), getItemCenteredY(0, focusY), focusY, (v) => v);
      };

      // intro: bring chapter 1 to center as section enters viewport
      const introStartY = 0;
      let centeredStartY = 0;
      readHeight();
      resetVisualState();
      centeredStartY = getItemCenteredY(0, (pin.clientHeight || window.innerHeight) * 0.5);
      gsap.set(tree, { y: introStartY });

      introTrigger = ScrollTrigger.create({
        trigger: wrap,
        start: "top bottom",
        end: "top top",
        invalidateOnRefresh: true,
        onUpdate: (self) => {
          const p = clamp(self.progress);
          const y = introStartY + (centeredStartY - introStartY) * p;
          renderVisibleState(0, y);
        },
        onLeave: () => renderVisibleState(0, centeredStartY),
        onEnterBack: () => renderVisibleState(0, centeredStartY),
        onLeaveBack: () => renderVisibleState(0, introStartY),
      });

      mainTrigger = ScrollTrigger.create({
        trigger: wrap,
        start: "top top",
        end: () => `+=${scrollDistance()}`,
        pin: pin,
        pinSpacing: false,
        anticipatePin: 1,
        invalidateOnRefresh: true,
        onUpdate: (self) => renderMainProgress(self.progress),
        onRefresh: () => renderMainProgress(getScrollProgress()),
        onLeave: () => renderMainProgress(1),
        onLeaveBack: () => renderMainProgress(0),
      });

      function getScrollProgress() {
        if (!mainTrigger) return 0;
        const d = mainTrigger.end - mainTrigger.start;
        const scrollY = window.scrollY;
        if (scrollY < mainTrigger.start) return 0;
        return d <= 0 ? 0 : clamp((scrollY - mainTrigger.start) / d);
      }
    }, scope);

    return () => ctx.revert();
  }, []);

  return (
    <section
      className="system theme-indigo section"
      data-nav-progress-section="system"
      data-autopilot-trigger
      data-theme-change="nav:indigo"
      ref={scopeRef}
    >
      <div className="container">
        <div className="system__intro" space-top="s">
          <h2 className="paragraph-m text-align-center" data-scramble="scroll" data-scramble-intensity="2">
            the system
          </h2>
          <h3 className="heading-l text-align-center" data-scramble="scroll" data-scramble-intensity="2">
            monolayer takes your intent, acts on it, learns from feedback, and moves toward the next step
            automatically.
          </h3>
        </div>
        <div className="system__wrap" data-system-visual ref={wrapRef}>
          <div className="system-visual__pin" ref={pinRef}>
            <div className="system-visual__stage">
              <div className="system-visual__tree" ref={treeRef}>
                <ChapterNode items={CHAPTERS} idx={0} chapterRefs={chapterRefs} textRefs={textRefs} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}