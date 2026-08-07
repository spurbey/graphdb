import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { ModalCloseIcon } from "./icons.jsx";

// Modal system, faithful to modal.js: slide-in-from-left panel
// (xPercent -100→0, open 0.75s ease "main"), backdrop fade to stored
// opacity (default 0.24, power1.out), close 0.32s, backdrop 0.22s.
// Draggable custom scrollbar synced on rAF while open; Esc/backdrop close.

const MODAL_CONTENT = {
  "account-setup": {
    eyebrow: ["Technical", "—", "Account setup"],
    title: "how to setup a account in monolayer",
    body: (
      <>
        <h3>
          Create your monolayer account first. Choose a plan, then open your installation page to copy
          the AWS installer command for your account.
        </h3>
        <p>{"\u200d"}</p>
        <p>What you need before starting:</p>
        <ul role="list">
          <li>Email address for the person who will own the monolayer account</li>
          <li>A password for signing in to monolayer.</li>
          <li>A decision on who should own the workspace and plan.</li>
          <li>
            Access to the monolayer installation page after sign-up. This is where you copy your
            account-specific AWS installer command.
          </li>
        </ul>
        <p>Once you have these questions settled, feel free to open your monolayer app and follow these steps:</p>
        <ol role="list">
          <li>
            Open monolayer
            <br />
            {"\u200d"}
            <br />
            Go to{" "}
            <a href="https://monolayer.dev/" target="_blank" rel="noreferrer">
              monolayer.dev
            </a>{" "}
            and choose Start for free. The button opens the monolayer sign-up flow.
            <br />
            {"\u200d"}
            <br />
            The public landing page explains the operating model before sign-up: monolayer deploys
            full-stack applications directly into your AWS account, and the platform/control plane also
            run in your AWS account after installation.
          </li>
          <li>
            Create your monolayer account
            <br />
            {"\u200d"}
            <br />
            The sign-up screen asks for an email address, a password, and password confirmation.
            <br />
            {"\u200d"}
            <br />
            This creates the identity you use to access monolayer. It is separate from your AWS login.
          </li>
          <li>
            Select a plan
            <br />
            {"\u200d"}
            <br />
            After signing up for monolayer, choose the plan that matches the way you want to start.
            <br />
            {"\u200d"}
            <br />
            The plan controls monolayer product access. It does not move your application data or
            runtime workloads into a monolayer-hosted cloud.
          </li>
          <li>
            Understand the boundary
            <br />
            {"\u200d"}
            <br />
            Your monolayer account identifies you in monolayer, tracks product access, and lets you
            reach the setup and control-plane experience.
            <br />
            {"\u200d"}
            <br />
            Your application infrastructure, workloads, databases, storage, logs, and cloud costs live
            in your AWS account. AWS remains the owner of the runtime boundary, billing boundary, and
            infrastructure control surface.
            <br />
            {"\u200d"}
            <br />
            Your AWS credentials are not your monolayer credentials. AWS authentication happens
            separately during installation, through AWS login and your local AWS CLI session.
          </li>
          <li>
            Open the installation page
            <br />
            {"\u200d"}
            <br />
            Once the account exists and the plan is selected, continue to the monolayer installation
            page.
            <br />
            {"\u200d"}
            <br />
            This is where monolayer provides the account-specific installer command. The command
            includes your license key, so copy it from your own setup screen before continuing to AWS
            installation.
          </li>
        </ol>
        <blockquote>
          aws --version
          <br />
          aws login
        </blockquote>
        <blockquote>npx @monolayer/installer@latest &lt;your-license-key&gt;</blockquote>
        <h4>Where what lives?</h4>
        <p>
          Inside your monolayer account:
          <br />
          Email/password sign-in, plan selection, setup flow, and access to the monolayer experience.
        </p>
        <p>
          {"\u200d"}
          <br />
          your AWS account:
          <br />
          Application workloads, databases, storage, logs, infrastructure resources, CloudFormation
          stacks, and AWS billing.
        </p>
        <p>
          {"\u200d"}
          <br />
          your AWS credentials:
          <br />
          Used through AWS login and your local AWS CLI session. They are not the same as your
          monolayer sign-in password.
        </p>
      </>
    ),
  },
};

export default function Modal() {
  const [openItem, setOpenItem] = useState(null);
  const modalRef = useRef(null);
  const itemRef = useRef(null);
  const backdropRef = useRef(null);
  const barRef = useRef(null);
  const scrollRef = useRef(null);
  const contentRef = useRef(null);

  useEffect(() => {
    const modal = modalRef.current;
    if (!modal) return;

    const backdropOpacity = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--modal-backdrop-opacity") || "0.24"
    );

    // delegated open triggers
    const onDocClick = (e) => {
      const trigger = e.target.closest("[data-modal-trigger]");
      if (!trigger) return;
      e.preventDefault();
      const name = Object.keys(MODAL_CONTENT)[0];
      setOpenItem((prev) => (prev === name ? null : name));
    };
    document.addEventListener("click", onDocClick);

    return () => document.removeEventListener("click", onDocClick);
  }, []);

  // animate + lock scroll when openItem changes
  useEffect(() => {
    const item = itemRef.current;
    const backdrop = backdropRef.current;
    if (!item || !backdrop) return;

    if (openItem) {
      document.documentElement.dataset.modalOpen = "true";
      const nav = document.querySelector(".nav");
      if (nav) {
        const h = nav.offsetHeight;
        document.documentElement.style.setProperty("--modal-nav-height", `${h}px`);
      }
      const saved = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      document.body.dataset.prevOverflow = saved;

      gsap.fromTo(
        item,
        { xPercent: -100, visibility: "visible" },
        { xPercent: 0, duration: 0.75, ease: "main", overwrite: "auto" }
      );
      gsap.fromTo(
        backdrop,
        { autoAlpha: 0 },
        { autoAlpha: backdropOpacity, duration: 0.35, ease: "power1.out", overwrite: "auto" }
      );
      const closeBtn = item.querySelector("[data-modal-close]");
      closeBtn?.focus?.();
    } else {
      delete document.documentElement.dataset.modalOpen;
      document.documentElement.style.setProperty("--modal-nav-height", "0px");
      const saved = document.body.dataset.prevOverflow;
      document.body.style.overflow = saved || "";
      delete document.body.dataset.prevOverflow;

      if (item.style.visibility !== "hidden") {
        gsap.to(item, { xPercent: -100, duration: 0.32, ease: "main", overwrite: "auto" });
        gsap.to(backdrop, { autoAlpha: 0, duration: 0.22, ease: "power1.out", overwrite: "auto" });
      }
    }
  }, [openItem]);

  // custom scrollbar sync
  useEffect(() => {
    const scrollEl = scrollRef.current;
    const contentEl = contentRef.current;
    const bar = barRef.current;
    if (!scrollEl || !contentEl || !bar) return;

    let raf = 0;
    const sync = () => {
      const maxScroll = contentEl.scrollHeight - contentEl.clientHeight;
      const ratio = maxScroll > 0 ? scrollEl.scrollTop / maxScroll : 0;
      bar.style.top = `${ratio * 100}%`;
      raf = 0;
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(sync);
    };
    scrollEl.addEventListener("scroll", onScroll, { passive: true });

    // keep a manually scrollable viewport
    if (window.matchMedia("(pointer: coarse)").matches) {
      scrollEl.style.overflowY = "auto";
      scrollEl.style.touchAction = "pan-y";
    } else {
      scrollEl.style.overflowY = "auto";
    }

    return () => {
      scrollEl.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [openItem]);

  const close = () => setOpenItem(null);

  const content = openItem ? MODAL_CONTENT[openItem] : null;

  return (
    <div
      className={`modal${openItem ? " open" : ""}`}
      data-modal-preview="false"
      data-modal-init
      ref={modalRef}
    >
      {openItem && content ? (
        <div className="modal__collection">
          <div className="modal__list" role="list">
            <div className="modal__item" role="listitem" data-modal-item={openItem} ref={itemRef}>
              <div className="modal__scroll" ref={scrollRef}>
                <div className="modal__content" data-scroll-content-inner ref={contentRef}>
                  <div className="modal__headline">
                    <div className="modal-headline__info">
                      {content.eyebrow.map((p) => (
                        <p className="paragraph-m text-align-center" key={p}>
                          {p}
                        </p>
                      ))}
                    </div>
                    <h2 className="heading-m" text-indent="">
                      {content.title}
                    </h2>
                  </div>
                  <div className="rich-text-regular">{content.body}</div>
                </div>
              </div>
              <div className="modal__scrollbar" data-modal-scrollbar>
                <div className="modal-scrollbar__bar" data-modal-scrollbar-bar ref={barRef} />
              </div>
              <button className="modal__close" data-modal-close aria-label="Close modal" onClick={close}>
                <ModalCloseIcon />
                <div className="modal-close__bg" />
              </button>
            </div>
          </div>
        </div>
      ) : null}
      <div className="modal__backdrop" data-modal-backdrop ref={backdropRef} onClick={close} />
    </div>
  );
}