import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useScramble } from "../anim/useScramble.js";

const ITEMS = [
  {
    before: "start with setup. choose the stack, prepare the repo, shape the first environment.",
    number: "01",
    label: "create",
    text: "define your intent. start from a prompt, a repo, or an idea",
  },
  {
    before: "push code. wait for checks, pipelines, deploy steps, and handoffs.",
    number: "02",
    label: "commit",
    text: "move intent forward. push changes into the system.",
  },
  {
    before: "wire services by hand. configure data, events, routes, and credentials.",
    number: "03",
    label: "connect",
    text: "link components. data, services, events\u2014express relationships, not infrastructure.",
  },
  {
    before: "watch the system. read dashboards, chase logs, and manage live state.",
    number: "04",
    label: "operate",
    text: "run and observe. interact with a live system without managing it.",
  },
  {
    before: "plan capacity. adjust limits, replicas, queues, regions, and cost.",
    number: "05",
    label: "scale",
    text: "adjust to demand. the system expands or contracts without coordination work.",
  },
];

const TRACK_MULTIPLIER_DESKTOP = 1.8;
const TRACK_MULTIPLIER_MOBILE = 1;
const ANIMATION_MULTIPLIER_MOBILE = 1.8;
const TRACK_START = "top 32%";
const SCRUB = 0.075;
const ITEM_TRAVEL_RATIO = 0.75;
const BAR_PROGRESS_START = 0;
const BAR_PROGRESS_END = 0.7;
const TEXT_PROGRESS_START = 0.18;
const TEXT_PROGRESS_END = 0.52;

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp = (min, max, amount) => min + (max - min) * amount;
const easeInOutCubic = (value) =>
  value < 0.5 ? 4 * value * value * value : 1 - Math.pow(-2 * value + 2, 3) / 2;
const getLocalProgress = (progress, start, end) =>
  end === start ? (progress >= end ? 1 : 0) : clamp((progress - start) / (end - start));
const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim();

function splitGraphemes(text) {
  if (typeof Intl !== "undefined" && typeof Intl.Segmenter !== "undefined") {
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    return [...segmenter.segment(text)].map((part) => part.segment);
  }
  return Array.from(text);
}
const isScrambleCharacter = (char) => {
  if (!char || !char.trim()) return false;
  try {
    return /[\p{L}\p{N}]/u.test(char);
  } catch {
    return /[a-zA-Z0-9]/.test(char);
  }
};
function getDistributedIndexes(length, ratio) {
  if (!length) return [];
  let count = Math.round(length * ratio);
  if (count > 0 && count < 2 && length > 1) count = 2;
  count = clamp(count, 0, length);
  const indexes = [];
  for (let i = 0; i < count; i++) {
    const index = Math.floor(((i + 0.5) / count) * length);
    if (!indexes.includes(index)) indexes.push(index);
  }
  let fallbackIndex = 0;
  while (indexes.length < count && fallbackIndex < length) {
    if (!indexes.includes(fallbackIndex)) indexes.push(fallbackIndex);
    fallbackIndex++;
  }
  return indexes.sort((a, b) => a - b);
}
function shuffleArray(array) {
  const shuffled = [...array];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const randomIndex = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[randomIndex]] = [shuffled[randomIndex], shuffled[i]];
  }
  return shuffled;
}
function rotateArray(array) {
  if (array.length <= 1) return array;
  return [...array.slice(1), array[0]];
}
function scrambleWord(word, progress, ratio) {
  const chars = splitGraphemes(word);
  const scrambleable = chars
    .map((char, index) => ({ char, index }))
    .filter((item) => isScrambleCharacter(item.char))
    .map((item) => item.index);
  const distributed = getDistributedIndexes(scrambleable.length, ratio)
    .map((index) => scrambleable[index])
    .filter((index) => typeof index === "number");
  if (distributed.length < 2) return word;
  const nextChars = [...chars];
  const revealCount = Math.floor(progress * distributed.length);
  const activeIndexes = distributed.slice(revealCount);
  if (activeIndexes.length < 2) return word;
  const originalActive = activeIndexes.map((index) => chars[index]);
  let shuffled = shuffleArray(originalActive);
  if (shuffled.join("") === originalActive.join("")) shuffled = rotateArray(shuffled);
  activeIndexes.forEach((charIndex, i) => {
    nextChars[charIndex] = shuffled[i];
  });
  return nextChars.join("");
}
function scrambleWordsInText(text, progress, ratio) {
  if (progress <= 1e-3 || progress >= 0.999) return text;
  return String(text || "")
    .split(/(\s+)/)
    .map((part) => {
      if (!part) return "";
      if (/^\s+$/.test(part)) return part;
      return scrambleWord(part, progress, ratio);
    })
    .join("");
}
function buildLengthAwareMorphText(beforeText, finalText, progress) {
  const beforeChars = splitGraphemes(beforeText);
  const finalChars = splitGraphemes(finalText);
  const beforeLength = beforeChars.length;
  const finalLength = finalChars.length;
  const lengthProgress = easeInOutCubic(progress);
  const currentLength = Math.max(1, Math.round(lerp(beforeLength, finalLength, lengthProgress)));
  const chars = [];
  for (let i = 0; i < currentLength; i++) {
    const indexRatio = currentLength <= 1 ? 0 : i / (currentLength - 1);
    const charProgress = clamp((progress - indexRatio * 0.22) / 0.78, 0, 1);
    const fromChar = beforeChars[i] || "";
    const toChar = finalChars[i] || "";
    if (charProgress <= 0.18) {
      chars.push(fromChar || toChar);
      continue;
    }
    if (charProgress >= 0.82) {
      chars.push(toChar);
      continue;
    }
    chars.push(toChar || fromChar);
  }
  return normalizeText(chars.join(""));
}

export default function Different() {
  const scopeRef = useRef(null);
  useScramble(scopeRef, null);

  useEffect(() => {
    const container = scopeRef.current;
    if (!container) return;

    const ctx = gsap.context(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const mobileTrackMedia = window.matchMedia("(max-width: 767px)");
      const isMobile = () => mobileTrackMedia.matches;
      const getLayoutTrackMultiplier = () => (isMobile() ? TRACK_MULTIPLIER_MOBILE : TRACK_MULTIPLIER_DESKTOP);
      const getAnimationTrackMultiplier = () => (isMobile() ? ANIMATION_MULTIPLIER_MOBILE : TRACK_MULTIPLIER_DESKTOP);

      container.querySelectorAll("[data-different-item]").forEach((itemEl) => {
        const progressBar = itemEl.querySelector(".different__progress-bar");
        const contentText = itemEl.querySelector(".different__content-text");
        const textElement = contentText
          ? contentText.querySelector("p.heading-m, h1, h2, h3, h4, h5, h6")
          : null;
        if (!progressBar || !contentText || !textElement) return;

        const finalHTML = textElement.innerHTML;
        const finalText = normalizeText(textElement.textContent);
        const beforeText = normalizeText(
          itemEl.getAttribute("before-text") ||
            itemEl.getAttribute("data-before-text") ||
            "configure the tools, connect the services, and manage the system by hand."
        );

        const measureState = (text) => {
          contentText.style.height = "";
          contentText.style.minHeight = "";
          contentText.style.overflow = "";
          itemEl.style.minHeight = "";
          textElement.textContent = text;
          const contentHeight = Math.ceil(contentText.offsetHeight);
          const itemHeight = Math.ceil(itemEl.offsetHeight);
          textElement.innerHTML = finalHTML;
          return { contentHeight, itemHeight };
        };

        const beforeMeasure = measureState(beforeText);
        const finalMeasure = measureState(finalText);
        const beforeContentHeight = beforeMeasure.contentHeight;
        const finalContentHeight = finalMeasure.contentHeight;
        const beforeItemHeight = beforeMeasure.itemHeight;
        const finalItemHeight = finalMeasure.itemHeight;
        const maxItemHeight = Math.max(beforeItemHeight, finalItemHeight);

        itemEl.style.minHeight = `${beforeItemHeight}px`;
        itemEl.style.width = "100%";
        contentText.style.boxSizing = "border-box";
        contentText.style.overflow = "hidden";
        contentText.style.height = `${beforeContentHeight}px`;
        textElement.textContent = beforeText;

        let track = itemEl.parentElement && itemEl.parentElement.hasAttribute("data-different-track")
          ? itemEl.parentElement
          : null;
        if (!track) {
          track = document.createElement("div");
          track.className = "different__track";
          track.setAttribute("data-different-track", "");
          track.style.position = "relative";
          track.style.width = "100%";
          track.style.display = "block";
          track.style.overflow = "visible";
          itemEl.parentNode.insertBefore(track, itemEl);
          track.appendChild(itemEl);
        }

        let trackMetrics = null;
        const setTrackHeight = () => {
          const itemHeight = Math.max(1, Math.ceil(maxItemHeight));
          const trackHeight = Math.ceil(itemHeight * getLayoutTrackMultiplier());
          const scrollDistance = Math.max(0, trackHeight - itemHeight);
          const animationTrackHeight = Math.ceil(itemHeight * getAnimationTrackMultiplier());
          const animationDistance = Math.max(1, animationTrackHeight - itemHeight);
          track.style.minHeight = `${trackHeight}px`;
          return { itemHeight, trackHeight, scrollDistance, animationDistance };
        };
        trackMetrics = setTrackHeight();

        if (reduced) {
          textElement.innerHTML = finalHTML;
          contentText.style.height = `${finalContentHeight}px`;
          itemEl.style.minHeight = `${finalItemHeight}px`;
          gsap.set(progressBar, { scaleY: 1, transformOrigin: "top center" });
          return;
        }

        gsap.set(itemEl, { y: 0, willChange: "transform", force3D: true });
        gsap.set(progressBar, { scaleY: 0, transformOrigin: "top center", willChange: "transform" });

        let lastRenderedText = beforeText;
        let lastRenderedProgress = -1;
        let lastRenderTime = -Infinity;
        const updateInterval = 26;

        const showBeforeText = () => {
          textElement.textContent = beforeText;
          contentText.style.height = `${beforeContentHeight}px`;
          itemEl.style.minHeight = `${beforeItemHeight}px`;
          lastRenderedText = beforeText;
          lastRenderedProgress = -1;
        };
        const showFinalText = () => {
          textElement.textContent = finalText;
          contentText.style.height = `${finalContentHeight}px`;
          itemEl.style.minHeight = `${finalItemHeight}px`;
          lastRenderedText = finalText;
          lastRenderedProgress = 1;
        };

        const renderScrubScramble = (progress, force = false) => {
          const localProgress = getLocalProgress(progress, TEXT_PROGRESS_START, TEXT_PROGRESS_END);
          if (localProgress <= 1e-3) {
            showBeforeText();
            return;
          }
          if (localProgress >= 0.999) {
            showFinalText();
            return;
          }
          const easedProgress = easeInOutCubic(localProgress);
          contentText.style.height = `${lerp(beforeContentHeight, finalContentHeight, easedProgress)}px`;
          itemEl.style.minHeight = `${lerp(beforeItemHeight, finalItemHeight, easedProgress)}px`;
          const now = performance.now();
          if (!force && now - lastRenderTime < updateInterval && localProgress > 0 && localProgress < 1) return;
          lastRenderTime = now;
          if (!force && Math.abs(localProgress - lastRenderedProgress) < 1e-3) return;
          lastRenderedProgress = localProgress;
          const morphText = buildLengthAwareMorphText(beforeText, finalText, localProgress);
          const scrambledText = scrambleWordsInText(morphText, localProgress, 0.75);
          if (scrambledText === lastRenderedText) return;
          textElement.textContent = scrambledText;
          lastRenderedText = scrambledText;
        };

        const renderProgressBar = (progress) => {
          gsap.set(progressBar, { scaleY: getLocalProgress(progress, BAR_PROGRESS_START, BAR_PROGRESS_END) });
        };
        const renderProgress = (progress, force = false) => {
          renderProgressBar(progress);
          renderScrubScramble(progress, force);
        };

        const scrubProxy = { progress: 0 };
        const tl = gsap.timeline({
          defaults: { ease: "none" },
          scrollTrigger: {
            trigger: track,
            start: TRACK_START,
            end: () => {
              trackMetrics = setTrackHeight();
              return `+=${trackMetrics.animationDistance}`;
            },
            scrub: SCRUB,
            invalidateOnRefresh: true,
            onRefreshInit: () => {
              trackMetrics = setTrackHeight();
            },
            onRefresh: (self) => {
              trackMetrics = setTrackHeight();
              if (self.progress <= 1e-3) {
                scrubProxy.progress = 0;
                renderProgress(0, true);
              } else if (self.progress >= 0.999) {
                scrubProxy.progress = 1;
                renderProgress(1, true);
              } else {
                renderProgress(scrubProxy.progress, true);
              }
            },
            onLeave: () => {
              if (scrubProxy.progress >= 0.999) renderProgress(1, true);
            },
            onLeaveBack: () => {
              if (scrubProxy.progress <= 1e-3) renderProgress(0, true);
            },
          },
        });
        tl.to(
          itemEl,
          {
            y: () => {
              trackMetrics = setTrackHeight();
              return trackMetrics.scrollDistance * ITEM_TRAVEL_RATIO;
            },
            duration: 1,
          },
          0
        );
        tl.to(
          scrubProxy,
          {
            progress: 1,
            duration: 1,
            onUpdate: () => renderProgress(scrubProxy.progress),
          },
          0
        );
      });
    }, container);

    return () => ctx.revert();
  }, []);

  return (
    <section
      className="different"
      data-nav-progress-section="different"
      data-theme-change="nav:base"
      ref={scopeRef}
    >
      <div className="container">
        <div className="different_text" space-top space-bottom="m">
          <div className="different__headline">
            <h2 className="heading-s" data-scramble="scroll" data-scramble-intensity>
              what used to be managed by hand, is now automated with monolayer
            </h2>
          </div>
          <div className="different__subtext">
            <p className="paragraph-l" data-scramble="scroll" data-scramble-intensity>
              a different surface
            </p>
          </div>
        </div>
      </div>
      <div className="container-right">
        {ITEMS.map((item, i) => (
          <div
            className="different__item"
            data-different-item
            key={item.number}
            before-text={item.before}
            space-bottom={i === ITEMS.length - 1 ? "l" : ""}
          >
            <div className="different__progress">
              <div className="different__counter">
                <p className="paragraph-m" data-scramble="3" data-scramble-intensity="scroll">
                  {item.number}
                </p>
                <div className="different__counter-line" />
              </div>
              <div className="different__progress-bar" />
            </div>
            <div className="different__label">
              <h3 className="paragraph-m" data-scramble="scroll" data-scramble-intensity="3">
                {item.label}
              </h3>
            </div>
            <div className="different__content">
              <div className="different__content-line is--top" />
              <div className="different__content-line is--left" />
              <div className="different__content-text">
                <p className="heading-m" data-scramble="scroll" data-scramble-intensity="3" text-indent="0">
                  {item.text}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
