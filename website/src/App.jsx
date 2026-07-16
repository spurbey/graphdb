import React from 'react';
import { motion } from 'framer-motion';
import { ChevronRight, Database, Search, Clock, Network, CheckCircle2, XCircle } from 'lucide-react';
import './index.css';

const Reveal = ({ children, delay = 0 }) => (
  <motion.div
    initial={{ opacity: 0, y: 24 }}
    whileInView={{ opacity: 1, y: 0 }}
    viewport={{ once: true, margin: "-100px" }}
    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay }}
  >
    {children}
  </motion.div>
);

function App() {
  return (
    <>
      <header className="header">
        <div className="container header-inner">
          <a href="/" className="logo">
            <Database size={24} color="var(--color-brand)" />
            <span>GraphDB</span>
          </a>
          <nav className="nav-links">
            <a href="#problem" className="nav-link">The Problem</a>
            <a href="#features" className="nav-link">Features</a>
            <a href="#benchmark" className="nav-link">Benchmarks</a>
          </nav>
          <div>
            <a href="https://github.com/spurbey/graphdb" target="_blank" rel="noreferrer" className="btn-primary">
              View on GitHub
            </a>
          </div>
        </div>
      </header>

      <main>
        <section className="section" style={{ paddingTop: '160px', paddingBottom: '0px' }}>
          <div className="container">
            <Reveal>
              <div className="text-center">
                <h1>
                  Your agent searches files.<br />
                  GraphDB understands <span className="text-gradient">architecture</span>.
                </h1>
                <p className="lead" style={{ marginTop: '24px' }}>
                  It ingests your running code while your agent codes, tracks structural dependencies, and points to the <span className="text-gradient alert">exact function to fix</span>.
                </p>
                <div style={{ marginTop: '32px' }}>
                  <a href="#features" className="btn-primary">
                    <Database size={16} /> Get the HelixDB SDK <ChevronRight size={16} className="arrow" />
                  </a>
                </div>
              </div>
            </Reveal>

            <Reveal delay={0.2}>
              <div className="hero-wrapper">
                <div className="terminal-card">
                  <div className="terminal-inner">
                    <div style={{ color: 'var(--color-ink-muted)', marginBottom: '16px' }}>
                      $ agent-query "find where session snapshots are exported"
                    </div>
                    <div style={{ color: 'var(--color-signal)' }}>
                      &gt; GraphDB Vector Search [2048-dim Llama Nemotron]
                    </div>
                    <div style={{ paddingLeft: '16px', borderLeft: '2px solid var(--color-ink-muted)', margin: '8px 0' }}>
                      ↳ Hit: export_snapshot (sandbox/snapshots.py)
                    </div>
                    <div style={{ color: 'var(--color-brand-soft)', marginTop: '16px' }}>
                      &gt; GraphDB Time-Travel Diff
                    </div>
                    <div style={{ paddingLeft: '16px', borderLeft: '2px solid var(--color-ink-muted)', margin: '8px 0' }}>
                      <span style={{ color: '#ff5470' }}>- v1 (2026-06-24):</span> no validation<br />
                      <span style={{ color: '#35e6b0' }}>+ v2 (HEAD):</span> validate_username, JSON response
                    </div>
                    <div style={{ color: 'var(--color-alert)', marginTop: '16px' }}>
                      &gt; Temporal Vulnerability Trace [depth=3]
                    </div>
                    <div style={{ paddingLeft: '16px', borderLeft: '2px solid var(--color-ink-muted)', margin: '8px 0' }}>
                      ↳ Found 1 stale caller predating security upgrade:<br />
                      &nbsp;&nbsp;auth/service.py : signup()
                    </div>
                    <div style={{ color: 'var(--color-signal)', marginTop: '16px' }}>
                      ✓ Graph traversal completed in 4.03ms. Exact line located.
                    </div>
                  </div>
                </div>
              </div>
            </Reveal>
          </div>
        </section>

        <section id="problem" className="section" style={{ background: 'var(--color-skin-2)' }}>
          <div className="container">
            <Reveal>
              <div className="text-center">
                <p className="mono-label">The bugs that look like features</p>
                <h2>The dependencies that hide behind a <span className="text-gradient alert">flat file list</span></h2>
              </div>
            </Reveal>

            <div className="card-grid">
              <Reveal delay={0.1}>
                <div className="card" style={{ height: '100%' }}>
                  <h3>The Ghost Caller</h3>
                  <p className="body-text">You changed a function signature. The agent updated the file. But a file three folders away still calls the old signature. Ripgrep won't save you.</p>
                  <div className="code-block alert-text">
                    TypeError: signup() missing 1 required positional argument: 'role'
                  </div>
                </div>
              </Reveal>
              <Reveal delay={0.2}>
                <div className="card" style={{ height: '100%' }}>
                  <h3>The Silent Regression</h3>
                  <p className="body-text">The new code works. But a security fix applied three weeks ago was accidentally overwritten because the agent's context window lacked the history.</p>
                  <div className="code-block alert-text">
                    auth/service.py (HEAD) · missing validation
                  </div>
                </div>
              </Reveal>
              <Reveal delay={0.3}>
                <div className="card" style={{ height: '100%' }}>
                  <h3>The God-Node Trap</h3>
                  <p className="body-text">Vector search surfaces 'make_settings' for every query because it's massive. The actual implementation details get buried in noise.</p>
                  <div className="code-block alert-text">
                    Vector Rank 1: init_db (PPR Dilution)
                  </div>
                </div>
              </Reveal>
            </div>
            
            <Reveal delay={0.4}>
              <div className="text-center" style={{ marginTop: '64px' }}>
                <p className="lead">Other tools search your repo from the outside, treating code as dead text. GraphDB sits inside HelixDB, tracking every request, inheritance, and temporal state as a graph.</p>
                <p style={{ fontSize: '1.5rem', fontWeight: 500, color: 'var(--color-ink)' }}>
                  Grep gates files. <span className="text-gradient">GraphDB gates logic.</span>
                </p>
              </div>
            </Reveal>
          </div>
        </section>

        <section id="features" className="section">
          <div className="container">
            <Reveal>
              <div className="text-center">
                <p className="mono-label">What it does</p>
                <h2>It connects your codebase the way you would, then <span className="text-gradient">never forgets how</span>.</h2>
              </div>
            </Reveal>

            <div className="card-grid">
              <Reveal delay={0.1}>
                <div className="card" style={{ height: '100%' }}>
                  <div className="feature-tag"><Search size={12} style={{ display: 'inline', marginRight: '4px' }} /> Semantic Vector Search</div>
                  <h3>Finds code by intention</h3>
                  <p className="body-text">Using a 2048-dim embedding model, GraphDB matches natural language to active function states. Our Personalized PageRank (PPR) suppresses god-nodes, returning clean, architecturally relevant subgraphs.</p>
                </div>
              </Reveal>

              <Reveal delay={0.2}>
                <div className="card" style={{ height: '100%' }}>
                  <div className="feature-tag"><Clock size={12} style={{ display: 'inline', marginRight: '4px' }} /> Time-Travel Diff</div>
                  <h3>Proof on every commit</h3>
                  <p className="body-text">GraphDB maintains `PREVIOUS_VERSION` edges. Instantly pull the current vs previous version of a function to see exactly how its behavior evolved, without slow git checkouts.</p>
                </div>
              </Reveal>

              <Reveal delay={0.3}>
                <div className="card" style={{ height: '100%' }}>
                  <div className="feature-tag"><Network size={12} style={{ display: 'inline', marginRight: '4px' }} /> Vulnerability Trace</div>
                  <h3>Understands the blast radius</h3>
                  <p className="body-text">Single-query Rust traversal finds all functions that call a target, up to N hops deep. Identify callers whose code predates a security upgrade in 4 milliseconds.</p>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        <section id="benchmark" className="section benchmark-section">
          <div className="container">
            <Reveal>
              <div className="text-center">
                <p className="mono-label">Benchmark</p>
                <h2>Measured. Reproducible. Including where we lose.</h2>
                <p className="lead">Every number comes from a committed harness. Clone the repo, run <span className="text-gradient">tools/_test_bench.py</span>, get the same numbers.</p>
              </div>
            </Reveal>

            <div className="stats-grid">
              <Reveal delay={0.1}>
                <div className="stat-card">
                  <div className="stat-number">4.03<span className="stat-unit">ms</span></div>
                  <div className="stat-desc">To run a 3-hop blast radius trace in Rust. Nested Python loops take ~34ms. <b>8.4x faster.</b></div>
                </div>
              </Reveal>

              <Reveal delay={0.2}>
                <div className="stat-card">
                  <div className="stat-number">10<span className="stat-unit">/13</span></div>
                  <div className="stat-desc">Queries passing in the AMO cold discovery pipeline using Vector + PPR at k=20 seeds.</div>
                </div>
              </Reveal>

              <Reveal delay={0.3}>
                <div className="stat-card">
                  <div className="stat-number">0%<span className="stat-unit">flake</span></div>
                  <div className="stat-desc">Every run yields the same deterministic traversal outcome. No sampling.</div>
                </div>
              </Reveal>
            </div>
            
            <Reveal delay={0.4}>
               <div style={{ marginTop: '48px', padding: '32px', background: 'white', borderRadius: '16px', border: '1px solid var(--color-skin-line)' }}>
                 <h3 style={{ marginBottom: '16px' }}>GraphDB vs The Rest</h3>
                 <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '16px', fontSize: '0.875rem' }}>
                   <div style={{ fontWeight: 600, color: 'var(--color-ink)' }}>Capability</div>
                   <div style={{ width: '80px', textAlign: 'center', fontWeight: 600, color: 'var(--color-signal-deep)' }}>GraphDB</div>
                   <div style={{ width: '80px', textAlign: 'center', fontWeight: 600, color: 'var(--color-ink-muted)' }}>Grep / AST</div>
                   
                   <div style={{ gridColumn: '1 / -1', height: '1px', background: 'var(--color-skin-line)' }}></div>
                   
                   <div style={{ color: 'var(--color-ink-soft)' }}>Tracks temporal state changes across commits</div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><CheckCircle2 size={18} color="var(--color-signal-deep)" /></div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><XCircle size={18} color="var(--color-ink-muted)" opacity={0.5} /></div>
                   
                   <div style={{ gridColumn: '1 / -1', height: '1px', background: 'var(--color-skin-line)' }}></div>
                   
                   <div style={{ color: 'var(--color-ink-soft)' }}>Resolves 3-hop callers in a single query</div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><CheckCircle2 size={18} color="var(--color-signal-deep)" /></div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><XCircle size={18} color="var(--color-ink-muted)" opacity={0.5} /></div>
                   
                   <div style={{ gridColumn: '1 / -1', height: '1px', background: 'var(--color-skin-line)' }}></div>
                   
                   <div style={{ color: 'var(--color-ink-soft)' }}>Suppresses god-node contamination via PPR</div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><CheckCircle2 size={18} color="var(--color-signal-deep)" /></div>
                   <div style={{ display: 'flex', justifyContent: 'center' }}><XCircle size={18} color="var(--color-ink-muted)" opacity={0.5} /></div>
                 </div>
               </div>
            </Reveal>
          </div>
        </section>
      </main>
    </>
  );
}

export default App;
