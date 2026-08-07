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

const SCROLL_VH = 460;
const FOCUS = 0.5;
const REVEAL = 0.46;
const PAD = 0.24;
const HOLD = 8e-3;
const COLLAPSE = 0.64;
const BRIDGE = 0.34;
const SEQ3 = 1.45;
const SEQ3_HOLD = 0.04;

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp3 = (a, b, p) => a + (b - a) * p;
const mixNumber = (a, b, p) => a + (b - a) * p;
const scaleLength = (value, progress) => {
  const parsed = parseFloat(value);
  if (!Number.isFinite(parsed)) return value;
  return `${parsed * progress}px`;
};
const mixLength = (from, to, progress) => {
  const f = parseFloat(from);
  const t = parseFloat(to);
  if (!Number.isFinite(f) || !Number.isFinite(t)) return to;
  return `${mixNumber(f, t, progress)}px`;
};
const progressInDuration = (time, duration) => (duration <= 0 ? 1 : clamp(time / duration));
const progressInRange = (progress, start, end) => {
  if (end <= start) return progress >= end ? 1 : 0;
  return clamp((progress - start) / (end - start));
};
const getScrollY = () => window.pageYOffset || document.documentElement.scrollTop || 0;
const isTouchUnder991 = () =>
  (window.matchMedia("(pointer: coarse)").matches || navigator.maxTouchPoints > 0) && window.innerWidth <= 991;

// Chapter tree — outermost carries ⑤ (feedback), innermost carries ① (intent),
// matching the source HTML the animation is built around. Each non-innermost
// chapter renders its nested wrap first, then its own text (default DOM order).
function renderChapter(number, chapterRefs, textRefs) {
  const item = CHAPTERS[number - 1];
  const child = number > 1 ? renderChapter(number - 1, chapterRefs, textRefs) : null;
  return (
    <div className="system__chapter" ref={(n) => (chapterRefs.current[number - 1] = n)}>
      {child ? (
        <div className="system-visual__nested-wrap" data-system-visual-nested-wrap aria-hidden="false">
          {child}
        </div>
      ) : null}
      <div className="system__text" ref={(n) => (textRefs.current[number - 1] = n)}>
        <div className="system__chapter-tagline">
          <p className="paragraph-m" data-scramble="scroll" data-scramble-intensity="2">{item.tagline}</p>
          <p className="paragraph-m" data-scramble="scroll" data-scramble-intensity="2">{item.sub}</p>
        </div>
        <div className="system__chapter-headline">
          <h3 className="heading-m text-align-center" data-scramble="scroll" data-scramble-intensity="2">{item.text}</h3>
        </div>
      </div>
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
    const chapterNodes = chapterRefs.current;
    const textNodes = textRefs.current;
    if (!scope || !wrap || !pin || !tree || !chapterNodes.length || !textNodes.length) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (!gsap.parseEase("systemEaseOut")) CustomEase.create("systemEaseOut", "0.215, 0.61, 0.355, 1");
    if (!gsap.parseEase("systemChapterPop")) CustomEase.create("systemChapterPop", "0.34, 1.12, 0.64, 1");
    if (!gsap.parseEase("systemSequenceThree")) CustomEase.create("systemSequenceThree", "0.76, 0, 0.24, 1");
    if (!gsap.parseEase("systemSequenceNative")) CustomEase.create("systemSequenceNative", "0.22, 0, 0.78, 1");

    const visualRoot = tree.querySelector(".system__chapter");
    if (!visualRoot) return;

    const chapters = [visualRoot, ...Array.from(visualRoot.querySelectorAll(".system__chapter"))];
    const nestedWraps = Array.from(visualRoot.querySelectorAll("[data-system-visual-nested-wrap]"));

    const textItems = textNodes
      .map((text, i) => {
        const chapter = text.closest(".system__chapter");
        if (!chapter) return null;
        return {
          number: i + 1,
          text,
          chapter,
          styles: STYLES,
          height: 0,
          stackHeight: 0,
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.number - b.number);

    if (textItems.length < 2) return;

    if (reduced) {
      textItems.forEach((item) => {
        item.chapter.setAttribute("data-system-visual-collapsed", "false");
        gsap.set(item.chapter, { marginTop: 0, marginBottom: 0, paddingTop: STYLES.chapterPadding.top, paddingRight: STYLES.chapterPadding.right, paddingBottom: STYLES.chapterPadding.bottom, paddingLeft: STYLES.chapterPadding.left, overflow: "hidden" });
        gsap.set(item.text, { height: "auto", paddingTop: STYLES.textPadding.top, paddingRight: STYLES.textPadding.right, paddingBottom: STYLES.textPadding.bottom, paddingLeft: STYLES.textPadding.left, overflow: "hidden" });
      });
      return;
    }

    let introTrigger = null;
    let mainTrigger = null;
    let renderMainProgress = null;
    let sequenceOrderActive = false;
    let centeredStartY = 0;
    let lastWidth = window.innerWidth;
    let isKilled = false;
    let refreshRaf = null;
    let syncRaf = null;

    const getItemCenter = (item) => {
      const itemRect = item.text.getBoundingClientRect();
      const rootRect = visualRoot.getBoundingClientRect();
      return itemRect.top - rootRect.top + itemRect.height / 2;
    };
    const getItemCenteredY = (item, focusY) => focusY - getItemCenter(item);

    const setChapterProgress = (item, progress) => {
      const styles = item.styles;
      gsap.set(item.chapter, {
        paddingTop: scaleLength(styles.chapterPadding.top, progress),
        paddingRight: scaleLength(styles.chapterPadding.right, progress),
        paddingBottom: scaleLength(styles.chapterPadding.bottom, progress),
        paddingLeft: scaleLength(styles.chapterPadding.left, progress),
        overflow: "hidden",
      });
    };
    const setChapterStackProgress = (item, progress) => {
      const styles = item.styles;
      gsap.set(item.chapter, {
        paddingTop: styles.stackChapterPadding.top,
        paddingRight: styles.stackChapterPadding.right,
        paddingBottom: mixLength(styles.chapterPadding.bottom, styles.stackChapterPadding.bottom, progress),
        paddingLeft: styles.stackChapterPadding.left,
        overflow: "hidden",
      });
    };
    const setChapterSequenceProgress = (item) => {
      const styles = item.styles;
      gsap.set(item.chapter, {
        paddingTop: styles.stackChapterPadding.top,
        paddingRight: styles.stackChapterPadding.right,
        paddingBottom: styles.stackChapterPadding.bottom,
        paddingLeft: styles.stackChapterPadding.left,
        overflow: "hidden",
      });
    };
    const setTextProgress = (item, progress) => {
      const styles = item.styles;
      gsap.set(item.text, {
        height: Math.max(0, item.height * progress),
        paddingTop: scaleLength(styles.textPadding.top, progress),
        paddingRight: scaleLength(styles.textPadding.right, progress),
        paddingBottom: scaleLength(styles.textPadding.bottom, progress),
        paddingLeft: scaleLength(styles.textPadding.left, progress),
        overflow: "hidden",
      });
    };
    const setStackTextProgress = (item, progress) => {
      const styles = item.styles;
      gsap.set(item.text, {
        height: Math.max(0, item.stackHeight * progress),
        paddingTop: scaleLength(styles.stackTextPadding.top, progress),
        paddingRight: scaleLength(styles.stackTextPadding.right, progress),
        paddingBottom: scaleLength(styles.stackTextPadding.bottom, progress),
        paddingLeft: scaleLength(styles.stackTextPadding.left, progress),
        overflow: "hidden",
      });
    };
    const setTextToStackProgress = (item, progress) => {
      const styles = item.styles;
      gsap.set(item.text, {
        height: Math.max(0, mixNumber(item.height, item.stackHeight, progress)),
        paddingTop: mixLength(styles.textPadding.top, styles.stackTextPadding.top, progress),
        paddingRight: mixLength(styles.textPadding.right, styles.stackTextPadding.right, progress),
        paddingBottom: mixLength(styles.textPadding.bottom, styles.stackTextPadding.bottom, progress),
        paddingLeft: mixLength(styles.textPadding.left, styles.stackTextPadding.left, progress),
        overflow: "hidden",
      });
    };
    const setCollapsed = (item, collapsed) => {
      item.chapter.setAttribute("data-system-visual-collapsed", collapsed ? "true" : "false");
    };

    const setTextBeforeNestedChapter = (root, textFirst) => {
      const nodeList = [root, ...Array.from(root.querySelectorAll(".system__chapter"))];
      nodeList.forEach((chapter) => {
        const children = Array.from(chapter.children);
        const directText = children.find((c) => c.classList?.contains("system__text"));
        const nestedBlock = children.find((c) => c.hasAttribute("data-system-visual-nested-wrap"));
        if (!directText || !nestedBlock) return;
        if (textFirst) {
          if (directText.nextElementSibling !== nestedBlock) chapter.insertBefore(directText, nestedBlock);
        } else if (nestedBlock.nextElementSibling !== directText) {
          chapter.insertBefore(directText, nestedBlock.nextSibling);
        }
      });
    };
    const setSequenceOrder = (active) => {
      if (sequenceOrderActive === active) return;
      sequenceOrderActive = active;
      setTextBeforeNestedChapter(visualRoot, active);
    };
    const clearLayoutClips = () => {
      nestedWraps.forEach((w) => gsap.set(w, { height: "auto", overflow: "visible" }));
      chapters.forEach((c) => gsap.set(c, { height: "auto" }));
    };
    const setNestedBlockClipHeight = (block, height) => {
      if (!block) return;
      gsap.set(block, { height: `${Math.max(0, height)}px`, overflow: "hidden" });
    };
    const getDirectNestedBlock = (item) => {
      if (!item?.chapter) return null;
      return Array.from(item.chapter.children).find((c) => c.hasAttribute("data-system-visual-nested-wrap"));
    };

    const measureTextItems = () => {
      textItems.forEach((item) => {
        const styles = item.styles;
        gsap.set(item.text, { height: "auto", paddingTop: styles.textPadding.top, paddingRight: styles.textPadding.right, paddingBottom: styles.textPadding.bottom, paddingLeft: styles.textPadding.left, overflow: "hidden" });
        item.height = Math.max(1, Math.ceil(item.text.offsetHeight));
        gsap.set(item.text, { height: "auto", paddingTop: styles.stackTextPadding.top, paddingRight: styles.stackTextPadding.right, paddingBottom: styles.stackTextPadding.bottom, paddingLeft: styles.stackTextPadding.left, overflow: "hidden" });
        item.stackHeight = Math.max(1, Math.ceil(item.text.offsetHeight));
      });
    };

    const setVisibleCount = (count) => {
      clearLayoutClips();
      textItems.forEach((item, index) => {
        const isVisible = index < count;
        const styles = item.styles;
        item.chapter.setAttribute("data-system-visual-collapsed", isVisible ? "false" : "true");
        gsap.set(item.chapter, {
          paddingTop: isVisible ? styles.chapterPadding.top : 0,
          paddingRight: isVisible ? styles.chapterPadding.right : 0,
          paddingBottom: isVisible ? styles.chapterPadding.bottom : 0,
          paddingLeft: isVisible ? styles.chapterPadding.left : 0,
        });
        gsap.set(item.text, {
          height: isVisible ? item.height : 0,
          paddingTop: isVisible ? styles.textPadding.top : 0,
          paddingRight: isVisible ? styles.textPadding.right : 0,
          paddingBottom: isVisible ? styles.textPadding.bottom : 0,
          paddingLeft: isVisible ? styles.textPadding.left : 0,
          overflow: "hidden",
        });
      });
    };

    const setFinalStackState = (leadItem) => {
      clearLayoutClips();
      chapters.forEach((c) => c.setAttribute("data-system-visual-collapsed", "false"));
      textItems.forEach((item) => {
        const styles = item.styles;
        gsap.set(item.chapter, {
          paddingTop: styles.stackChapterPadding.top,
          paddingRight: styles.stackChapterPadding.right,
          paddingBottom: styles.stackChapterPadding.bottom,
          paddingLeft: styles.stackChapterPadding.left,
          overflow: "hidden",
        });
        if (item === leadItem) {
          setStackTextProgress(item, 1);
        } else {
          setStackTextProgress(item, 0);
        }
      });
    };

    const setSequenceBridgeBaseState = (leadItem) => {
      clearLayoutClips();
      chapters.forEach((c) => c.setAttribute("data-system-visual-collapsed", "false"));
      textItems.forEach((item) => {
        setChapterSequenceProgress(item);
        if (item === leadItem) setStackTextProgress(item, 1);
        else setStackTextProgress(item, 0);
      });
    };

    const setSequenceThreeExpandedState = (progress, leadItem) => {
      progress = clamp(progress);
      clearLayoutClips();
      chapters.forEach((c) => c.setAttribute("data-system-visual-collapsed", "false"));
      textItems.forEach((item) => {
        setChapterSequenceProgress(item);
        setStackTextProgress(item, item === leadItem ? 1 : progress);
      });
    };

    const resetVisualState = () => {
      setSequenceOrder(false);
      clearLayoutClips();
      chapters.forEach((c) => {
        c.setAttribute("data-system-visual-collapsed", "true");
        gsap.set(c, { marginTop: 0, marginBottom: 0, paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
      });
      textItems.forEach((item) => {
        gsap.set(item.text, { height: 0, paddingTop: 0, paddingRight: 0, paddingBottom: 0, paddingLeft: 0, overflow: "hidden" });
      });
      const first = textItems[0];
      const styles = first.styles;
      first.chapter.setAttribute("data-system-visual-collapsed", "false");
      gsap.set(first.chapter, { paddingTop: styles.chapterPadding.top, paddingRight: styles.chapterPadding.right, paddingBottom: styles.chapterPadding.bottom, paddingLeft: styles.chapterPadding.left });
      gsap.set(first.text, { height: "auto", paddingTop: styles.textPadding.top, paddingRight: styles.textPadding.right, paddingBottom: styles.textPadding.bottom, paddingLeft: styles.textPadding.left });
    };

    const renderVisibleState = (lastVisibleIndex, y) => {
      setSequenceOrder(false);
      clearLayoutClips();
      textItems.forEach((item, index) => {
        const isVisible = index <= lastVisibleIndex;
        setCollapsed(item, !isVisible);
        setChapterProgress(item, isVisible ? 1 : 0);
        setTextProgress(item, isVisible ? 1 : 0);
      });
      gsap.set(tree, { y });
    };

    const renderRevealState = (activeIndex, chapterProgress, revealProgress) => {
      setSequenceOrder(false);
      clearLayoutClips();
      textItems.forEach((item, index) => {
        if (index < activeIndex) {
          setCollapsed(item, false);
          setChapterProgress(item, 1);
          setTextProgress(item, 1);
          return;
        }
        if (index === activeIndex) {
          setCollapsed(item, false);
          setChapterProgress(item, chapterProgress);
          setTextProgress(item, revealProgress);
          return;
        }
        setCollapsed(item, true);
        setChapterProgress(item, 0);
        setTextProgress(item, 0);
      });
    };

    const renderCollapseState = (progress, leadItem) => {
      setSequenceOrder(false);
      clearLayoutClips();
      chapters.forEach((c) => c.setAttribute("data-system-visual-collapsed", "false"));
      textItems.forEach((item) => {
        setChapterStackProgress(item, progress);
        if (item === leadItem) {
          setTextToStackProgress(item, progress);
        } else {
          setTextProgress(item, 1 - progress);
        }
      });
    };

    const renderSequenceBridgeState = (progress, leadItem, leadNestedBlock, beforeHeight, afterHeight, sequenceEase) => {
      progress = clamp(progress);
      const switchPoint = 0.42;
      if (progress < switchPoint) {
        setSequenceOrder(false);
        setSequenceBridgeBaseState(leadItem);
        const collapseProgress = clamp(sequenceEase(progressInRange(progress, 0, switchPoint)));
        setNestedBlockClipHeight(leadNestedBlock, lerp3(beforeHeight, 0, collapseProgress));
        return;
      }
      setSequenceOrder(true);
      setSequenceBridgeBaseState(leadItem);
      const expandProgress = clamp(sequenceEase(progressInRange(progress, switchPoint, 1)));
      setNestedBlockClipHeight(leadNestedBlock, lerp3(0, afterHeight, expandProgress));
    };

    const renderSequenceThreeState = (progress, leadItem, sequenceTopY, sequenceBottomY, focusY, sequenceEase, sequenceScrollEase, nativeTouchMode) => {
      progress = clamp(progress);
      setSequenceOrder(true);
      clearLayoutClips();
      const expandRangeEnd = nativeTouchMode ? 0.18 : 0.24;
      const expandProgress = nativeTouchMode ? progressInRange(progress, 0, expandRangeEnd) : clamp(sequenceEase(progressInRange(progress, 0, expandRangeEnd)));
      const rawScrollProgress = progressInRange(progress, expandRangeEnd, 1);
      const scrollProgress = nativeTouchMode ? clamp(rawScrollProgress) : clamp(sequenceScrollEase(rawScrollProgress));
      setSequenceThreeExpandedState(expandProgress, leadItem);
      const liveSequenceTopY = getItemCenteredY(leadItem, focusY);
      const stableSequenceTopY = Number.isFinite(liveSequenceTopY) ? liveSequenceTopY : sequenceTopY;
      gsap.set(tree, { y: lerp3(stableSequenceTopY, sequenceBottomY, scrollProgress) });
    };

    const shouldMainOwnScroll = () => {
      if (!mainTrigger) return false;
      return getScrollY() >= mainTrigger.start - 1;
    };

    const syncToCurrentScroll = (trigger = mainTrigger) => {
      if (!trigger || !renderMainProgress) return;
      const scrollY = getScrollY();
      if (scrollY < trigger.start - 1) {
        if (introTrigger) introTrigger.update();
        return;
      }
      const distance = trigger.end - trigger.start;
      const progress = distance <= 0 ? 0 : clamp((scrollY - trigger.start) / distance);
      renderMainProgress(progress);
    };

    const buildTimeline = () => {
      if (isKilled) return;
      if (introTrigger) {
        introTrigger.kill();
        introTrigger = null;
      }
      if (mainTrigger) {
        mainTrigger.kill();
        mainTrigger = null;
      }
      const nativeTouchMode = isTouchUnder991();
      setSequenceOrder(false);
      clearLayoutClips();
      const viewportForScroll = pin.clientHeight || window.innerHeight;
      const scrollDistance = Math.round(viewportForScroll * (SCROLL_VH / 100));
      wrap.style.setProperty("--system-visual-scroll-distance", `${scrollDistance}px`);
      wrap.style.minHeight = `${scrollDistance + viewportForScroll}px`;

      resetVisualState();
      measureTextItems();
      const viewportH = pin.clientHeight || window.innerHeight;
      const focusY = viewportH * FOCUS;
      const stackLeadItem = textItems.find((item) => item.number === 5) || [...textItems].sort((a, b) => b.number - a.number)[0];

      const states = textItems.map((item, index) => {
        const visibleCount = index + 1;
        setVisibleCount(visibleCount);
        gsap.set(tree, { y: 0 });
        return {
          item,
          visibleCount,
          y: focusY - getItemCenter(item),
        };
      });

      setFinalStackState(stackLeadItem);
      gsap.set(tree, { y: 0 });
      const sequenceTopItem = stackLeadItem;
      const sequenceBottomItem = textItems.find((item) => item.number === 1) || [...textItems].sort((a, b) => a.number - b.number)[0];
      const leadNestedBlock = getDirectNestedBlock(sequenceTopItem);

      setSequenceOrder(false);
      setSequenceBridgeBaseState(sequenceTopItem);
      clearLayoutClips();
      const bridgeBlockBeforeHeight = leadNestedBlock ? Math.ceil(leadNestedBlock.offsetHeight) : 0;
      setSequenceOrder(true);
      setSequenceBridgeBaseState(sequenceTopItem);
      clearLayoutClips();
      const bridgeBlockAfterHeight = leadNestedBlock ? Math.ceil(leadNestedBlock.offsetHeight) : 0;

      setSequenceThreeExpandedState(1, sequenceTopItem);
      clearLayoutClips();
      gsap.set(tree, { y: 0 });
      const sequenceTopY = focusY - getItemCenter(sequenceTopItem);
      const sequenceBottomY = focusY - getItemCenter(sequenceBottomItem);

      setSequenceOrder(false);
      clearLayoutClips();
      setVisibleCount(1);
      const introStartY = 0;
      centeredStartY = states[0].y;

      const introEase = gsap.parseEase("systemEaseOut") || ((v) => v);
      const revealEase = gsap.parseEase("systemEaseOut") || ((v) => v);
      const chapterEase = gsap.parseEase("systemChapterPop") || ((v) => v);
      const sequenceEase = gsap.parseEase("systemSequenceThree") || ((v) => v);
      const sequenceScrollEase = nativeTouchMode ? (v) => v : gsap.parseEase("systemSequenceNative") || ((v) => v);

      const revealSegmentDuration = Math.max(REVEAL, PAD);
      const revealSteps = textItems.length - 1;
      const totalDuration =
        HOLD +
        revealSteps * (revealSegmentDuration + HOLD) +
        COLLAPSE +
        BRIDGE +
        (nativeTouchMode ? 1.08 : SEQ3) +
        (nativeTouchMode ? 0 : SEQ3_HOLD);
      gsap.set(tree, { y: introStartY });

      renderMainProgress = (progress) => {
        progress = clamp(progress);
        const time = progress * totalDuration;
        let cursor = 0;
        if (time <= cursor + HOLD) {
          renderVisibleState(0, states[0].y);
          return;
        }
        cursor += HOLD;
        for (let index = 1; index < textItems.length; index++) {
          const segmentStart = cursor;
          const segmentEnd = segmentStart + revealSegmentDuration;
          if (time <= segmentEnd) {
            const localTime = time - segmentStart;
            const chapterProgress = progressInDuration(localTime, PAD);
            const revealProgress = progressInDuration(localTime, REVEAL);
            const easedChapterProgress = Math.max(0, chapterEase(chapterProgress));
            const easedRevealProgress = clamp(revealEase(revealProgress));
            renderRevealState(index, easedChapterProgress, easedRevealProgress);
            gsap.set(tree, { y: lerp3(states[index - 1].y, states[index].y, easedRevealProgress) });
            return;
          }
          cursor += revealSegmentDuration;
          if (time <= cursor + HOLD) {
            renderVisibleState(index, states[index].y);
            return;
          }
          cursor += HOLD;
        }
        if (time <= cursor + COLLAPSE) {
          const collapseProgress = progressInDuration(time - cursor, COLLAPSE);
          const easedCollapseProgress = clamp(revealEase(collapseProgress));
          renderCollapseState(easedCollapseProgress, stackLeadItem);
          gsap.set(tree, { y: getItemCenteredY(stackLeadItem, focusY) });
          return;
        }
        cursor += COLLAPSE;
        if (time <= cursor + BRIDGE) {
          const bridgeProgress = progressInDuration(time - cursor, BRIDGE);
          renderSequenceBridgeState(bridgeProgress, sequenceTopItem, leadNestedBlock, bridgeBlockBeforeHeight, bridgeBlockAfterHeight, sequenceEase);
          gsap.set(tree, { y: getItemCenteredY(sequenceTopItem, focusY) });
          return;
        }
        cursor += BRIDGE;
        const seqThreeDuration = nativeTouchMode ? 1.08 : SEQ3;
        if (time <= cursor + seqThreeDuration) {
          const sequenceProgress = progressInDuration(time - cursor, seqThreeDuration);
          renderSequenceThreeState(sequenceProgress, sequenceTopItem, sequenceTopY, sequenceBottomY, focusY, sequenceEase, sequenceScrollEase, nativeTouchMode);
          return;
        }
        cursor += seqThreeDuration;
        const seqThreeHold = nativeTouchMode ? 0 : SEQ3_HOLD;
        if (time <= cursor + seqThreeHold) {
          renderSequenceThreeState(1, sequenceTopItem, sequenceTopY, sequenceBottomY, focusY, sequenceEase, sequenceScrollEase, nativeTouchMode);
          return;
        }
        renderSequenceThreeState(1, sequenceTopItem, sequenceTopY, sequenceBottomY, focusY, sequenceEase, sequenceScrollEase, nativeTouchMode);
      };

      introTrigger = ScrollTrigger.create({
        trigger: wrap,
        start: "top bottom",
        end: "top top",
        invalidateOnRefresh: true,
        onUpdate: (self) => {
          if (shouldMainOwnScroll()) return;
          const easedProgress = introEase(self.progress);
          const y = lerp3(introStartY, centeredStartY, easedProgress);
          renderVisibleState(0, y);
        },
        onEnter: () => {
          if (shouldMainOwnScroll()) return;
          renderVisibleState(0, introStartY);
        },
        onLeave: () => {
          if (shouldMainOwnScroll()) return;
          renderVisibleState(0, centeredStartY);
        },
        onEnterBack: () => {
          if (shouldMainOwnScroll()) return;
          renderVisibleState(0, centeredStartY);
        },
        onLeaveBack: () => {
          if (shouldMainOwnScroll()) return;
          renderVisibleState(0, introStartY);
        },
      });

      mainTrigger = ScrollTrigger.create({
        trigger: wrap,
        start: "top top",
        end: () => `+=${scrollDistance}`,
        pin,
        pinSpacing: false,
        anticipatePin: nativeTouchMode ? 0 : 1,
        invalidateOnRefresh: true,
        onUpdate: (self) => renderMainProgress(self.progress),
        onRefresh: (self) => syncToCurrentScroll(self),
        onEnter: (self) => renderMainProgress(self.progress),
        onEnterBack: (self) => renderMainProgress(self.progress),
        onLeave: () => renderMainProgress(1),
        onLeaveBack: () => renderMainProgress(0),
      });
    };

    const scheduleSync = (forceRefresh = false) => {
      if (isKilled) return;
      if (syncRaf) cancelAnimationFrame(syncRaf);
      syncRaf = requestAnimationFrame(() => {
        syncRaf = null;
        if (isKilled) return;
        if (forceRefresh) ScrollTrigger.refresh();
        syncToCurrentScroll();
      });
    };

    const scheduleRefresh = () => {
      if (isKilled) return;
      if (refreshRaf) cancelAnimationFrame(refreshRaf);
      refreshRaf = requestAnimationFrame(() => {
        refreshRaf = null;
        buildTimeline();
        ScrollTrigger.refresh();
        syncToCurrentScroll();
      });
    };

    const onResize = () => {
      if (window.innerWidth === lastWidth) return;
      lastWidth = window.innerWidth;
      scheduleRefresh();
    };

    buildTimeline();
    scheduleSync(true);
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", scheduleRefresh);
    if (document.fonts?.ready) {
      document.fonts.ready.then(() => {
        if (!isKilled) scheduleRefresh();
      });
    }

    return () => {
      isKilled = true;
      if (refreshRaf) cancelAnimationFrame(refreshRaf);
      if (syncRaf) cancelAnimationFrame(syncRaf);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", scheduleRefresh);
      if (introTrigger) {
        introTrigger.kill();
        introTrigger = null;
      }
      if (mainTrigger) {
        mainTrigger.kill();
        mainTrigger = null;
      }
      wrap.style.removeProperty("--system-visual-scroll-distance");
      wrap.style.removeProperty("min-height");
    };
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
                {renderChapter(CHAPTERS.length, chapterRefs, textRefs)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
