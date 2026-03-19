# Changelog

All notable changes to Hivemind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-19

### Added

- **Narrative Overhaul**: New README with clear value proposition, architecture diagrams, and getting started guide
- **NPX CLI Package**: `npx create-hivemind@latest` — interactive setup wizard with one-command installation
- **Landing Page**: Dark-themed, responsive landing page with feature showcase and live demo section
- **Visual Assets**: Hero banner, architecture diagrams, demo video script and keyframes
- **OpenClaw Integration**: Multi-runtime abstraction layer supporting Claude Code, OpenClaw, Bash, and HTTP runtimes
- **Project Templates**: 5 pre-built templates — SaaS Starter, REST API, React Dashboard, CLI Tool, Mobile App
- **Organizational Hierarchy**: Corporate management structure (CEO/CTO/VP) with chain of command, escalation paths, and decision authority
- **OrgChart Component**: Interactive visual hierarchy in the dashboard
- **Org API**: REST endpoints for reading/updating project org charts
- **Community Infrastructure**: Updated issue templates, PR template, contribution guide, GitHub labels
- **New Agent Proposal Template**: Structured issue template for proposing new specialist agents
- **Social Media Distribution Kit**: Launch playbook with platform-specific posts for X, Reddit, HN, LinkedIn, Dev.to

### Fixed

- **Project Deletion**: Dashboard now properly removes deleted projects via WebSocket `deleted` status event
- **Delete Error Handling**: ConductorBar and Dashboard delete buttons now show proper error feedback
- **Delete Confirmation**: Updated confirmation text to accurately describe permanent deletion

### Changed

- **Orchestrator Prompt**: Updated to operate as CEO with organizational awareness
- **PM Prompt**: Now includes org hierarchy section for structured task delegation
- **GitHub Description**: Updated to "One prompt. A full AI engineering team. Production-ready code."
- **GitHub Topics**: Added ai-agents, multi-agent, orchestration, claude-code, openclaw, dag-executor

## [0.x.x] - Pre-release

Initial development of Hivemind with core features:
- Multi-agent DAG execution engine
- PM agent with task graph generation
- Specialist agents (frontend, backend, database, tester, reviewer, security, devops, researcher, UX)
- Real-time dashboard with live agent streaming
- Self-healing with circuit breakers
- Artifact flow between agents
- Project isolation and context management
