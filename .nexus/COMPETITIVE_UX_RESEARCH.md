# Competitive UX Research: AI Agent Orchestration Dashboards

**Date**: 2026-03-09 | **Sources**: 13 tools analyzed | **Depth**: Deep (Tier 3)

---

## Executive Summary

We analyzed 10 AI agent tools (Cursor, Windsurf, CrewAI Studio, AutoGen Studio, Claude Code, OpenClaw, LangGraph Studio, Dify, n8n, Flowise) to identify what makes users love or leave each product. **Three massive gaps exist across the entire landscape that Nexus can own**: (1) real-time per-agent cost tracking, (2) multi-agent parallel visualization, and (3) granular undo/rollback. No existing tool does all three well. Nexus already has architectural advantages (WebSocket real-time, orbital constellation view, cost forecasting) that competitors lack entirely. This report provides specific, implementable UX patterns with CSS values, component code, and prioritized action items.

---

## 1. Competitive Landscape Overview

### Market Map

| Tool | Stars | Category | Theme | Agent Viz | Cost Tracking | Key Strength |
|------|-------|----------|-------|-----------|---------------|-------------|
| **OpenClaw** | 283K | Personal AI assistant | Dark (red `#ff5c5c`) | Session logs | None in UI | 22+ messaging channels, local-first |
| **n8n** | 178K | Workflow automation | Light | Node canvas | None | 400+ integrations, code+no-code |
| **Dify** | 132K | LLM app platform | Light | Workflow canvas | Per-run tokens | Prompt IDE, RAG pipeline |
| **Flowise** | 51K | Visual agent builder | Light | Drag-drop flow | None | LangChain visual, marketplace |
| **Cursor** | N/A | AI IDE | Dark (`#1a1a2e`) | Step timeline | "Fast requests" counter | Inline diff, one-click apply |
| **Windsurf** | N/A | AI IDE | Dark | Cascade panel | Credits counter | Autonomous multi-step agent |
| **LangGraph** | 26K | Agent framework | Dark | State graph DAG | None in UI | Durable execution, checkpoints |
| **CrewAI** | N/A | Multi-agent framework | Light (SaaS) | Pipeline list | None in UI | Role/goal/backstory abstraction |
| **AutoGen** | N/A | Multi-agent framework | Light (MS) | Group chat | None in UI | Code execution sandbox |
| **Botpress** | 15K | Conversational AI | Dark | Conversation flow | None | Enterprise chat builder |
| **Claude Code** | N/A | CLI agent | Terminal | Tool indicators | Per-session summary | Transparent operations, git-native |

### Key Insight
Developer-facing tools trend **dark theme**. Business-user tools trend **light theme**. Nexus is correct to be dark-first with the Mission Control aesthetic — it positions us squarely in the "pro developer tool" space alongside Cursor, Windsurf, and OpenClaw.

---

## 2. Tool-by-Tool Analysis

### 2.1 Cursor IDE

**What makes it feel premium:**
- Deep charcoal background (`#1a1a2e` to `#16161a`), NOT pure black
- Single indigo accent (`#7c5cfc`) used sparingly — tab underlines, AI suggestion borders, selected highlights
- Primary text `#e0e0e0` (soft white), secondary `#888`, borders at `rgba(255,255,255,0.05-0.08)`
- Panel transitions at 150-200ms ease-out; AI popovers fade+scale from 98%→100%
- Frosted glass (`backdrop-filter: blur()`) on command palette overlays
- Shimmer/skeleton loading states instead of spinners

**Signature UX patterns:**
1. **Inline diff preview** — proposed changes shown as green/red diff *inside* the file, not in a separate panel
2. **Agent step timeline** — vertical collapsible list: "Reading file X", "Editing Y", "Running command Z". Active steps pulse; completed steps auto-collapse to summaries
3. **Streaming token animation** — token-by-token (not character-by-character) with pulsing cursor at insertion point
4. **One-click Apply** — code blocks in chat have "Apply" button → triggers inline diff. This is THE feature users cite as reason for switching from Copilot
5. **File badge system** — modified files get colored dots in sidebar (orange=pending, green=accepted)

**What users hate:**
- "It ate my code" — destructive edits in agent mode, especially on large files. #1 complaint
- Context window confusion — "it just silently gets dumber" with no context budget indicator
- Cost opacity — "fast requests" counter is tiny, silent fallback to slower models when exhausted
- Chat history is linear, unsearchable
- Agent mode runaway loops — burns through requests retrying the same wrong approach

**Why users switch TO Cursor:** One-click apply, agent step transparency, model selection
**Why users switch AWAY:** Cost surprises, destructive edits, context limit confusion

### 2.2 Windsurf (Codeium)

**Signature UX patterns:**
1. **Cascade autonomous execution** — executes multi-step tasks without stopping for permission (speed over safety)
2. **Mode toggle** — explicit switch between chat mode and agentic mode at panel top
3. **Atomic session rollback** — prominent "Revert" button undoes ALL changes from a Cascade session
4. **Context chips** — file-name pills at top of conversation showing what the agent is "aware of"
5. **Post-completion summary card** — all files changed, lines added/removed per file
6. **Inline terminal output** — command results stream directly in the chat panel, not a separate terminal

**What users hate:**
- Credit system opacity — #1 complaint. Different actions cost different credits, no real-time deduction display, credits exhaust mid-task with no warning
- Context loss on long sessions — agent "goes in circles", undoing its own changes
- Aggressive auto-execution — ran destructive commands (`rm -rf`, migrations) without sufficient warning
- No granular undo — Revert is all-or-nothing. 9 good changes + 1 bad = must revert all
- Pricing changes — multiple repricing events with poor communication

**Why users switch TO Windsurf:** Speed of autonomous execution, free tier generosity, cleaner diffs
**Why users switch AWAY:** Credit surprise, trust issues (destructive actions), context loss

### 2.3 CrewAI Studio

**UX patterns:**
- Vertical pipeline view — agents as cards in linear sequence with connecting arrows
- Task cards with states: `Pending`, `In Progress`, `Completed`, `Failed`
- Role/goal/backstory form-based config — intuitive for non-technical users
- YAML escape hatch for power users
- Light SaaS aesthetic (Notion-like): white cards, subtle shadows, teal accent

**What users hate:**
- No real-time streaming — wait for entire crew to finish, then see all output at once
- Opaque delegation — unclear which agent is actually doing work in hierarchical mode
- No cost tracking in UI at all
- Raw Python tracebacks for errors, no user-friendly summaries
- No conditional branching in workflow
- Linear list view becomes unwieldy with 5+ agents

### 2.4 AutoGen Studio (Microsoft)

**UX patterns:**
- Group chat transcript — all agents in one scrolling chat, color-coded names
- Three-panel Build section: Models → Agents → Teams
- Code execution with Docker sandbox — watch agents write and run Python live
- Gallery system — pre-built team configurations, browse and import
- JSON editor with real-time schema validation

**What users hate:**
- Setup complexity (Docker, API keys, Python deps) — biggest onboarding barrier
- No streaming in early versions
- Most-requested missing feature: visual drag-and-drop workflow builder
- Single chat stream becomes unreadable with 4+ agents
- No cost/token tracking in UI
- State loss on server restart if SQLite not properly persisted
- No way to rate/flag individual agent messages

### 2.5 Claude Code (CLI)

**Signature UX patterns:**
1. **Tool use indicators** — spinner + tool name + description + live duration timer: `> Reading file: src/utils.ts (3s)`
2. **Three-tier permission system** — reads=auto, writes=show diff+approve, bash=show command+approve. "Always allow" option (`a`) reduces fatigue
3. **Streaming diff preview** — before approving, see red/green diff of proposed changes
4. **TodoWrite task tracking** — `[x] Done [>] Working [ ] Pending` — visible execution plan
5. **Per-session cost summary** — shown at end with token breakdown (input/output/cache)
6. **Extended thinking blocks** — dimmed, collapsed by default, "Thought for 12s" expander

**What users hate:**
- Permission fatigue — constantly pressing `y`
- Cost surprise — sessions get expensive without budget controls
- Terminal limitations — no visual diff viewer, limited syntax highlighting
- Context window overflow — loses earlier context in long sessions
- No undo except git
- Output noise in complex operations

**Nexus's advantages over CLI Claude Code:**
- Visual diffs (already have `FileDiff.tsx`)
- Multi-agent orchestration visualization (unique — CLI can't do this)
- Cost forecasting before execution (novel — CLI doesn't have this)
- Rich permission UX with approval modal
- Orbital constellation view for agent relationships

### 2.6 OpenClaw

**What it is:** Personal AI assistant (283K stars), NOT an agent orchestration platform. Multi-channel inbox (22+ channels: WhatsApp, Telegram, Slack, Discord, etc.) routed to isolated agents.

**Design system:**
- Dark-first with `#12141a` background, `#1a1d25` elevated
- Signature red accent `#ff5c5c`, secondary teal `#14b8a6`
- Lit web components (not React), signals-based state
- Spring easing `cubic-bezier(0.34, 1.56, 0.64, 1)` for bouncy animations
- Stagger delays at 50ms increments

**Navigation structure:** Chat | Control (overview, channels, instances, sessions, usage, cron) | Agent (agents, skills, nodes) | Settings (config, debug, logs)

**What users hate:**
- Channel reliability (WhatsApp linking stuck, Telegram streaming broken)
- Tool execution reliability ("exec and tools keep breaking")
- API key security concerns
- 60-second hangs on multi-step tool calls

### 2.7 Additional Tools (LangGraph, Dify, n8n, Flowise)

**LangGraph Studio:** DAG visualization of agent state graphs, step-through debugging, checkpoint-based recovery. Desktop Electron app. Pain points: PostgreSQL checkpoint SSL errors, serialization bugs.

**Dify:** Visual workflow canvas, prompt IDE, RAG pipeline UI, 131K stars. Light theme, Next.js + Tailwind. Enterprise-oriented.

**n8n:** Node-based workflow editor, 400+ integrations, 178K stars. Vue.js frontend. Strong for connecting AI agents to external services.

**Flowise:** Drag-and-drop LangChain visual builder, 51K stars. React + MUI. Major stability issues: white screen bugs, OOMs on View Messages, Agentflow v2 conditional branching broken.

---

## 3. Universal UX Gaps (Nexus Opportunities)

These gaps exist across **ALL** competitors — filling them creates genuine differentiation:

| Gap | Who Has It? | Nexus Status | Priority |
|-----|-------------|-------------|----------|
| **Real-time per-agent cost tracking** | Nobody (Cursor/Windsurf have weak counters) | Partial (ConductorBar cost pill) | 🔴 P0 |
| **Multi-agent parallel visualization** | Nobody (all show linear chat/log) | ✅ Have (FlowGraph constellation) | Enhance |
| **Granular per-step undo** | Nobody (Windsurf has all-or-nothing revert) | Not implemented | 🔴 P0 |
| **Pre-execution cost forecast** | Nobody | ✅ Have (ConductorMode forecast) | Promote |
| **Context window indicator** | Nobody | Not implemented | 🟡 P1 |
| **Agent-stuck circuit breaker UI** | Nobody (agents loop without warning) | Partial (stuck detector backend) | 🟡 P1 |
| **Session replay** | Nobody (some have read-only history) | Not implemented | 🟢 P2 |
| **Visual workflow builder** | Dify, n8n, Flowise (not for Claude agents) | Not implemented | 🟢 P2 |
| **Dark theme** (for agent orchestration) | Only CLI tools | ✅ Have (Mission Control theme) | Maintain |
| **Cost analytics over time** | Nobody | Not implemented | 🟡 P1 |

---

## 4. Design System Recommendations

### 4.1 Color Palette (Verified Against Current CSS)

Your existing palette is strong. Add these for completeness:

```css
:root {
  /* KEEP — your current values are excellent */
  --bg-void: #0a0b0f;        /* L=4  — deepest background */
  --bg-panel: #0f1117;        /* L=6  — sidebar, headers */
  --bg-card: #13151d;         /* L=8  — cards, containers */
  --bg-elevated: #191c27;     /* L=11 — hover states, inputs */

  /* ADD — two new elevation levels */
  --bg-overlay: #1e2233;      /* L=13 — modals, dropdowns, tooltips */
  --bg-surface-bright: #252a3a; /* L=16 — active tabs, selected items */

  /* ADD — semantic surface tints (for status card backgrounds) */
  --surface-blue: rgba(99, 140, 255, 0.06);
  --surface-green: rgba(61, 214, 140, 0.06);
  --surface-amber: rgba(245, 166, 35, 0.06);
  --surface-red: rgba(245, 71, 91, 0.06);
  --surface-purple: rgba(167, 139, 250, 0.06);

  /* ADD — semantic borders */
  --border-blue: rgba(99, 140, 255, 0.15);
  --border-green: rgba(61, 214, 140, 0.15);
  --border-amber: rgba(245, 166, 35, 0.15);
  --border-red: rgba(245, 71, 91, 0.15);

  /* ADD — text hierarchy additions */
  --text-disabled: #353849;    /* L=23 — decorative only, never sole info carrier */
  --text-inverse: #0a0b0f;    /* For text on bright accent backgrounds */
}
```

### 4.2 Animation Timing (Calibrated Values)

| Animation Type | Duration | Easing | Status |
|---|---|---|---|
| Data value change | 400-600ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Your 600ms ✅ |
| Card enter/exit | 200-300ms | `ease-out` | Your 300ms ✅ |
| Pulse/breathing | 2000-3000ms | `ease-in-out` | Your 2.5s ✅ |
| Loading shimmer | 1500-2000ms | `ease-in-out` | Your 1.5s ✅ |
| Status transitions | 300-500ms | `ease-out` | Implement |
| Micro-hover lift | 150-200ms | `ease-out` | Implement |
| Toast notification | 250ms in / 200ms out | ease-out / ease-in | Implement |

### 4.3 Glow Effects (What Works vs. Cheap)

**Premium (keep/add):**
- `box-shadow: 0 0 20px -4px rgba(accent, 0.2)` — negative spread contains the glow ✅
- Opacity range 0.06 (ambient) to 0.25 (active/focused) — your 0.12-0.15 is perfect ✅
- `breathingGlow` at 2.5s with brightness 1.0→1.15 ✅
- Noise overlay at opacity 0.015 (CRT/film grain effect) ✅

**Cheap (avoid):**
- `box-shadow` with spread >10px
- Multiple stacked glows with different colors
- `text-shadow` glow on body text (only headings, max `0 0 8px`)
- Neon glow on borders >2px
- Any glow opacity >0.4

### 4.4 Premium Card Pattern

```css
.premium-card {
  box-shadow:
    0 25px 50px -12px rgba(0, 0, 0, 0.3),     /* depth */
    0 0 0 1px rgba(255, 255, 255, 0.03),        /* edge */
    inset 0 1px 0 0 rgba(255, 255, 255, 0.03);  /* top highlight */
}
.premium-card:hover {
  transform: translateY(-1px);  /* 1px max — 2px+ looks cartoonish */
  box-shadow:
    0 25px 60px -10px rgba(0, 0, 0, 0.35),
    0 0 0 1px rgba(255, 255, 255, 0.05),
    inset 0 1px 0 0 rgba(255, 255, 255, 0.05);
}
```

### 4.5 WCAG Accessibility Audit

| Foreground | Background | Ratio | Status |
|---|---|---|---|
| `#e2e5f0` on `#0a0b0f` | | 15.2:1 | ✅ Pass |
| `#e2e5f0` on `#13151d` | | 12.8:1 | ✅ Pass |
| `#8b90a5` on `#13151d` | | 5.2:1 | ✅ Pass |
| `#4a4e63` on `#13151d` | | 2.4:1 | ⚠️ Decorative only |
| `#638cff` on `#13151d` | | 5.1:1 | ✅ Pass |
| `#3dd68c` on `#13151d` | | 8.6:1 | ✅ Pass |
| `#f5a623` on `#13151d` | | 7.5:1 | ✅ Pass |
| `#f5475b` on `#13151d` | | 4.8:1 | ✅ Pass |

**Action:** ConductorBar "Send a task to begin" uses `--text-muted` (`#4a4e63`) as sole info carrier — bump to `--text-secondary`.

---

## 5. Patterns to Steal (Best of Each Tool)

### From Cursor — Adopt
| Pattern | Implementation | Nexus Adaptation |
|---|---|---|
| Collapsible agent step timeline | Vertical list with icons, auto-collapse completed | Each agent gets a collapsible activity log in AgentStatusPanel |
| Streaming token animation | Token-by-token with pulsing cursor | `<StreamingText>` component for agent output via WebSocket |
| One-click apply with diff | "Apply" button → inline diff preview | "Approve" button on agent recommendations |
| Frosted glass overlays | `backdrop-filter: blur()` on modals | Apply to ApprovalModal, command palette |
| File badge system | Colored dots in file tree | Status badges on constellation nodes |

### From Windsurf — Adopt
| Pattern | Implementation | Nexus Adaptation |
|---|---|---|
| Autonomous/supervised mode toggle | Explicit switch in panel header | Trust level selector in settings/ConductorBar |
| Atomic session rollback | "Revert" button per session | Per-agent-session undo |
| Context chips | File-name pills showing agent awareness | Show files/tools each agent is using as chips |
| Post-completion summary card | Files changed + lines added/removed | Task completion card with agent stats |
| Inline terminal output | Command results in chat stream | Terminal events in ActivityFeed, not separate panel |

### From Claude Code — Adopt
| Pattern | Implementation | Nexus Adaptation |
|---|---|---|
| Tool use indicators with duration | `> Reading file: X (3s)` | Add file paths + duration counter to ToolActivity |
| Three-tier trust levels | reads=auto, writes=approve, bash=approve | Trust level selector to solve permission fatigue |
| Extended thinking blocks | Dimmed, collapsed, "Thought for 12s" | Collapsible thinking indicator per agent |
| TodoWrite task tracking | `[x] [>] [ ]` visible plan | Already superior with PlanView — enhance with time estimates |

### From OpenClaw — Adopt
| Pattern | Implementation | Nexus Adaptation |
|---|---|---|
| Tab-group navigation | Chat \| Control \| Agent \| Settings | Consider organizing sidebar into logical groups |
| Spring easing animations | `cubic-bezier(0.34, 1.56, 0.64, 1)` | Use for button clicks and modal appearances |
| `openclaw doctor` self-check | CLI diagnostic command | Health check dashboard with system status |

### From LangGraph — Adopt
| Pattern | Implementation | Nexus Adaptation |
|---|---|---|
| Step-through debugging | Inspect state at each node transition | Add expandable state view per agent step |
| Human-in-the-loop interrupts | Pause, inspect, modify, resume | Enhance approval flow with state modification |
| Checkpoint-based recovery | Resume from last good checkpoint | Add "retry from here" per agent step |

---

## 6. Patterns to Avoid (Anti-Patterns from Competitors)

| Anti-Pattern | Who Does It | Why It Fails | Our Approach |
|---|---|---|---|
| **Silent quality degradation** | Cursor (model fallback) | Users don't know quality dropped | Always announce model/tier changes prominently |
| **Hidden cost counters** | Cursor, Windsurf | Users get surprise bills | Cost tracking front-and-center in ConductorBar |
| **All-or-nothing revert** | Windsurf | 9 good changes + 1 bad = lose all | Per-step granular undo |
| **Raw stack traces as errors** | CrewAI, AutoGen | Non-actionable for users | Human-readable summaries + expand for details |
| **No streaming output** | CrewAI (early), AutoGen | Users wait blindly, think it's frozen | Already have WebSocket streaming ✅ |
| **Single linear chat for multi-agent** | AutoGen | Unreadable with 4+ agents | Already have separate agent panels ✅ |
| **Credits that exhaust mid-task** | Windsurf | Work left in incomplete state | Budget guardrails + pre-execution forecast |
| **No dark mode** | CrewAI, AutoGen, Flowise | Developer audience expects it | Already dark-first ✅ |
| **Complex setup** | AutoGen (Docker), OpenClaw | High onboarding friction | One-command startup + health check |

---

## 7. Actionable Implementation Roadmap

### 7.1 Quick Wins (< 1 hour each)

| # | Task | Impact | Effort | Details |
|---|------|--------|--------|---------|
| 1 | Add `--bg-overlay` and `--bg-surface-bright` CSS vars | Medium | 5 min | Two new elevation levels in index.css |
| 2 | Add `costFlash` animation to cost pill | High | 15 min | Green flash on cost increase (0.6s ease-out) |
| 3 | Bump "Send a task to begin" to `--text-secondary` | Low | 1 min | WCAG fix in ConductorBar |
| 4 | Add `aria-live="polite"` to connection indicator | Low | 5 min | Accessibility for screen readers |
| 5 | Add semantic surface tints + border CSS vars | Medium | 10 min | Status card backgrounds |

### 7.2 High-Impact Features (1-3 hours each)

| # | Task | Impact | Effort | Details |
|---|------|--------|--------|---------|
| 6 | **BudgetGauge component** | 🔴 Critical | 30 min | Progress bar: `$0.42 / $5.00`, color transitions at 70%/90%/100% |
| 7 | **CostBreakdown stacked bar** | High | 1 hr | Per-agent cost breakdown as horizontal bar in project view |
| 8 | **CostSparkline component** | High | 1 hr | Zero-dependency SVG sparkline on project cards |
| 9 | **StreamingText component** | High | 1 hr | Token-by-token text with blinking cursor for active agent output |
| 10 | **Trust level selector** | 🔴 Critical | 2 hr | 4-level slider: Approve All → Auto-Read → Auto-Write → Full Auto |
| 11 | **Connection state machine** | High | 2 hr | 4 states: connected/reconnecting/degraded/disconnected with timing thresholds |
| 12 | **Event batching hook** | Medium | 1 hr | `useBatchedWSEvents` — batch rapid WS events to prevent re-render thrashing |

### 7.3 Premium Polish (3-6 hours each)

| # | Task | Impact | Effort | Details |
|---|------|--------|--------|---------|
| 13 | **Agent thinking ripple** | High | 1 hr | Concentric expanding circles on FlowGraph nodes during agent work |
| 14 | **AgentTelemetry readout** | High | 1 hr | 3-column grid (COST / TURNS / TIME) on agent cards — mission control feel |
| 15 | **AgentTimeline component** | High | 2-3 hr | Horizontal Gantt-style view showing parallel agent execution |
| 16 | **Optimistic UI for messages** | Medium | 2-3 hr | Show sent message immediately at 60% opacity, confirm/fail on API response |
| 17 | **Framer Motion integration** | Medium | 2 hr | `AnimatePresence` for activity feed exits, `motion.div layout` for agent cards |
| 18 | **Context window indicator** | High | 2 hr | Per-agent gauge: "Context: 45% of 200K tokens" with warning at 80% |
| 19 | **"Thinking" indicator** | Medium | 1 hr | Pulsing brain icon + "Reasoning..." + collapsed "Thought for 12s" expander |
| 20 | **Session replay** | High | 4-6 hr | Store WS events, replay with scrubber timeline (unique differentiator) |

### 7.4 Implementation Priority Matrix

```
          HIGH IMPACT
              │
    ┌─────────┼─────────┐
    │ P0      │ P1      │
    │ Budget  │ Think   │
    │ Trust   │ Context │
    │ Cost    │ Replay  │
    │ Stream  │ Timeline│
LOW ──────────┼──────────── HIGH EFFORT
    │ P0      │ P2      │
    │ Flash   │ Framer  │
    │ CSS vars│ Optim.  │
    │ a11y    │ Visual  │
    │         │ Builder │
    └─────────┼─────────┘
              │
          LOW IMPACT
```

---

## 8. What Makes Users Switch (Decision Factors)

### Switch-TO triggers (what attracts users):
1. **Visible progress** — seeing what the agent is doing (Cursor's step timeline, Claude Code's tool indicators)
2. **One-click actions** — reducing steps from 5 to 2 (Cursor's Apply button)
3. **Speed** — autonomous execution without approval gates (Windsurf's Cascade)
4. **Cost transparency** — knowing what you're spending (universal unmet need)
5. **Dark theme** — developer aesthetic expectation
6. **Reliability** — agents that don't lose context or loop infinitely

### Switch-AWAY triggers (what drives users out):
1. **Cost surprise** — #1 churn driver across Cursor and Windsurf
2. **Data loss** — "it ate my code" (Cursor), no granular undo (Windsurf)
3. **Silent degradation** — quality drops without notification (Cursor model fallback)
4. **Agent loops** — burning resources on repeated failures (universal)
5. **Setup complexity** — can't get it working in 5 minutes (AutoGen, OpenClaw)
6. **Context loss** — agent forgets earlier conversation (Windsurf, Claude Code)

### Nexus's positioning opportunity:
**"The only AI agent dashboard where you can SEE what every agent is doing, KNOW what it costs, and UNDO any mistake."**

---

## 9. Design System Comparison (Competitor Palettes)

| Tool | Background | Elevated | Text Primary | Accent | Font |
|------|------------|----------|-------------|--------|------|
| **Nexus** | `#0a0b0f` | `#13151d` | `#e2e5f0` | `#638cff` blue | DM Sans + JetBrains Mono |
| **Cursor** | `#1a1a2e` | `#1e1e30` | `#e0e0e0` | `#7c5cfc` indigo | Inter |
| **OpenClaw** | `#12141a` | `#1a1d25` | `#e4e4e7` | `#ff5c5c` red | System + JetBrains Mono |
| **VS Code** | `#1e1e1e` | `#252526` | `#d4d4d4` | `#007acc` blue | Menlo/Consolas |
| **LangGraph** | `#1a1a2e` | `#252535` | `#e0e0e0` | LangChain brand | System |

**Assessment:** Nexus's palette is the darkest (L=4 vs. competitors at L=8-12), which is deliberate for the "void of space" Mission Control feel. This is a differentiator — keep it. The warm charcoal tones (`#0f1117`, `#13151d`) avoid the "flat CSS" look that pure `#111` would create.

---

## 10. Library Recommendations

### Add
- **Framer Motion** — `AnimatePresence` for exit animations, `motion.div layout` for agent card reflows. Required for: activity feed entry removal, agent card add/remove. ~30KB gzipped.

### Do NOT Add
- **D3.js** — overkill for 4-6 node graphs. Your SVG `FlowGraph.tsx` is simpler and faster.
- **Recharts** — adds 200KB+ for charts achievable with 30-line SVG sparklines.
- **react-spring** — overlaps with Framer Motion. Pick one (Framer has better React 18 support).
- **react-flow/xyflow** — 150KB+, forces its own styling system. Your FlowGraph is already built and themed.

### Consider Later
- **Recharts/Nivo** — only if adding a dedicated analytics page with complex charts
- **@tanstack/react-virtual** — only if activity feed exceeds 200 entries

---

## 11. Key Component Patterns (Ready to Implement)

### BudgetGauge
```tsx
function BudgetGauge({ cost, maxBudget }: { cost: number; maxBudget: number }) {
  const pct = maxBudget > 0 ? Math.min((cost / maxBudget) * 100, 100) : 0;
  const color = pct > 90 ? 'var(--accent-red)'
    : pct > 70 ? 'var(--accent-amber)'
    : 'var(--accent-green)';

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden"
           style={{ background: 'var(--border-dim)' }}>
        <div className="h-full rounded-full transition-all duration-700"
             style={{
               width: `${pct}%`,
               background: `linear-gradient(90deg, ${color}, ${color}cc)`,
               boxShadow: pct > 90 ? `0 0 8px ${color}30` : 'none',
             }} />
      </div>
      <span className="telemetry" style={{ color, fontSize: '9px' }}>
        ${cost.toFixed(2)} / ${maxBudget.toFixed(2)}
      </span>
    </div>
  );
}
```

### CostSparkline (Zero Dependencies)
```tsx
function CostSparkline({ data, width = 120, height = 24 }: Props) {
  if (data.length < 2) return null;
  const max = Math.max(...data), min = Math.min(...data);
  const range = max - min || 1;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height}>
      <polyline points={points} fill="none" stroke="var(--accent-green)"
        strokeWidth="1.5" strokeLinecap="round" />
      <circle cx={width} cy={/* last point y */} r="2" fill="var(--accent-green)" />
    </svg>
  );
}
```

### StreamingText
```tsx
function StreamingText({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  return (
    <span>
      {text}
      {isStreaming && (
        <span className="inline-block w-[2px] h-[14px] ml-0.5 align-text-bottom"
          style={{ background: 'var(--accent-blue)', animation: 'blink 1s step-end infinite' }} />
      )}
    </span>
  );
}
```

### Agent Thinking Ripple (SVG)
```tsx
{agent.state === 'working' && (
  <>
    <circle cx={x} cy={y} r="24" fill="none" stroke={color} strokeWidth="2" opacity="0.4">
      <animate attributeName="r" values="24;40" dur="1.5s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.4;0" dur="1.5s" repeatCount="indefinite" />
    </circle>
    <circle cx={x} cy={y} r="24" fill="none" stroke={color} strokeWidth="2" opacity="0.4">
      <animate attributeName="r" values="24;40" dur="1.5s" begin="0.75s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.4;0" dur="1.5s" begin="0.75s" repeatCount="indefinite" />
    </circle>
  </>
)}
```

---

## 12. Sources

### Primary Tools Analyzed
1. [Cursor IDE](https://cursor.com) — AI-first IDE, VS Code fork
2. [Windsurf/Codeium](https://windsurf.com) — AI IDE with Cascade agent
3. [CrewAI](https://crewai.com) — Multi-agent orchestration framework
4. [AutoGen Studio](https://github.com/microsoft/autogen) — Microsoft's multi-agent UI
5. [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's CLI agent tool
6. [OpenClaw](https://github.com/openclaw/openclaw) — Personal AI assistant (283K stars)
7. [LangGraph Studio](https://github.com/langchain-ai/langgraph) — LangChain's agent IDE (26K stars)
8. [Dify](https://github.com/langgenius/dify) — LLM app platform (132K stars)
9. [n8n](https://github.com/n8n-io/n8n) — Workflow automation (178K stars)
10. [Flowise](https://github.com/FlowiseAI/Flowise) — Visual agent builder (51K stars)

### Community Sentiment Sources
- Reddit: r/cursor, r/windsurf, r/LangChain, r/LocalLLaMA
- Hacker News discussions on each tool
- GitHub Issues (bug reports and feature requests) for each repository
- YouTube demos and comparison videos

### Design System References
- Material Design 3 dark theme guidelines
- WCAG 2.1 contrast ratio requirements
- Apple Human Interface Guidelines (dark mode)
- Linear, Vercel, and Raycast as premium dark UI benchmarks

---

*Report generated for Nexus project — multi-agent Claude orchestration dashboard*
*Next update: After implementing P0 items from Section 7*
