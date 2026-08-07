import { useRef } from "react";
import { useScramble } from "../anim/useScramble.js";
import { ArrowIcon } from "./icons.jsx";

const TIERS = [
  {
    name: "Starter",
    cells: [
      { label: "users", value: "1" },
      { label: "projects", value: "1" },
      { label: "autopilot", value: "Full" },
      { label: "support", value: "Standard" },
    ],
    cost: ["$", "0", "/month"],
  },
  {
    name: "Pro",
    cells: [
      { label: "users", value: "3" },
      { label: "projects", value: "5" },
      { label: "autopilot", value: "Full" },
      { label: "support", value: "Priority" },
    ],
    cost: ["$", "79", "/month"],
  },
];

const NOTE_ROWS = [
  "additional user: $29/month",
  "additional project: $10/month",
];

export default function Pricing() {
  const scopeRef = useRef(null);
  useScramble(scopeRef, null);

  return (
    <section
      className="pricing"
      data-nav-progress-section="pricing"
      data-autopilot-trigger
      data-theme-change="nav:olive"
      ref={scopeRef}
    >
      <div className="container">
        <div className="pricing__text" space-top="s" space-bottom="l">
          <div className="pricing__headline">
            <h2 className="heading-m" data-scramble="scroll" data-scramble-intensity text-indent="2">
              Your infrastructure runs in your AWS account and is billed directly by AWS
            </h2>
          </div>
          <div className="pricing__subtext">
            <p className="paragraph-l" data-scramble="scroll" data-scramble-intensity>
              simple. transparent. aligned with your cloud.
            </p>
          </div>
        </div>

        <div className="pricing__grid">
          <div className="pricing__collection">
            <div className="pricing__list" role="list">
              {TIERS.map((tier) => (
                <div className="pricing__item" role="listitem" key={tier.name}>
                  <div className="pricing__top">
                    <div className="pricing__row is--top">
                      <h3 className="paragraph-l" data-scramble>{tier.name === "Starter" ? "plan" : "plan"}</h3>
                      <p className="heading-s" data-scramble="scroll" data-scramble-intensity>
                        {tier.name}
                      </p>
                    </div>
                    <div className="pricing__row is--top">
                      <div className="pricing__cost">
                        {tier.cost.map((part) => (
                          <p className="heading-s" data-scramble="scroll" data-scramble-intensity key={part}>
                            {part}
                          </p>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="pricing__details">
                    {tier.cells.map((cell) => (
                      <div className="pricing__row" key={cell.label}>
                        <p className="paragraph-m">{cell.label}</p>
                        <p className="paragraph-m">{cell.value}</p>
                      </div>
                    ))}
                    <div className="pricing__cta">
                      <a
                        className="main-btn"
                        href="https://app.monolayer.dev/sign-up"
                        target="_blank"
                        rel="noreferrer"
                        data-modal-trigger
                      >
                        <p className="main-btn__text">turn on autopilot</p>
                        <div className="main-btn__box">
                          <ArrowIcon />
                        </div>
                      </a>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="pricing__note-collection">
            <div className="pricing__note-list">
              <div className="pricing__note-item">
                <div className="pricing__note-top">
                  <p className="paragraph-s" data-scramble data-scramble-intensity>
                    No hidden infrastructure markup. No shared hosting model. No platform tax on your
                    cloud usage.
                  </p>
                </div>
                <div className="pricing__note-details">
                  {NOTE_ROWS.map((row) => (
                    <div className="pricing__note-row" key={row}>
                      <p className="paragraph-m">{row}</p>
                    </div>
                  ))}
                  <div className="pricing__note-row is--bottom" />
                  <div className="pricing__note-row is--bottom" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}