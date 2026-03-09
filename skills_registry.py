"""Skills registry — scans .claude/skills/*/SKILL.md and exposes content for sub-agent prompts."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill → Agent Role Mapping (multi-role support)
# Each skill maps to ONE OR MORE agent roles that should receive it.
# 3-layer architecture:
#   Layer 1 (Brain):   pm, orchestrator
#   Layer 2 (Hands):   frontend_developer, backend_developer, database_expert, devops
#   Layer 3 (Quality): security_auditor, test_engineer, reviewer, researcher
# ---------------------------------------------------------------------------
SKILL_AGENT_MAP: dict[str, list[str]] = {

    # ── Layer 1: Brain ────────────────────────────────────────────────────────────
    "planning-with-files":  ["pm", "developer"],
    "mermaid-diagrams":     ["pm", "researcher"],
    "strategic-compact":    ["pm", "memory"],
    "session-logs":         ["orchestrator", "memory"],
    "model-usage":          ["orchestrator"],

    # ── Layer 2: Frontend Developer ───────────────────────────────────────
    "react-typescript":     ["frontend_developer"],
    "tailwind-css":         ["frontend_developer"],
    "frontend-design":      ["frontend_developer"],
    "state-management":     ["frontend_developer"],
    "webapp-testing":       ["frontend_developer", "test_engineer"],
    "typescript-patterns":  ["frontend_developer"],
    "nextjs-fullstack":     ["frontend_developer"],
    "mobile-react-native":  ["frontend_developer"],
    "web-artifacts-builder":["frontend_developer"],
    "algorithmic-art":      ["frontend_developer"],
    "theme-factory":        ["frontend_developer"],
    "frontend-slides":      ["frontend_developer", "researcher"],
    "canvas-design":        ["frontend_developer", "researcher"],

    # ── Layer 2: Backend Developer ────────────────────────────────────────
    "fastapi-backend":      ["backend_developer"],
    "async-python":         ["backend_developer"],
    "jwt-authentication":   ["backend_developer", "security_auditor"],
    "redis-caching":        ["backend_developer"],
    "celery-tasks":         ["backend_developer"],
    "websockets-realtime":  ["backend_developer"],
    "s3-file-storage":      ["backend_developer"],
    "email-service":        ["backend_developer"],
    "stripe-payments":      ["backend_developer"],
    "nodejs-express":       ["backend_developer"],
    "graphql-api":          ["backend_developer"],
    "claude-api":           ["backend_developer"],
    "mcp-builder":          ["backend_developer"],
    "git-workflow":         ["backend_developer", "devops"],

    # ── Layer 2: Database Expert ──────────────────────────────────────────
    "sqlalchemy-orm":       ["database_expert"],
    "postgres-database":    ["database_expert", "devops"],
    "prisma-orm":           ["database_expert"],

    # ── Layer 2: DevOps ───────────────────────────────────────────────────
    "docker-deployment":    ["devops"],
    "healthcheck":          ["devops"],
    "microservices":        ["devops"],
    "tmux":                 ["devops"],

    # ── Layer 3: Security Auditor ─────────────────────────────────────────
    "security-review":      ["security_auditor", "reviewer"],
    "api-design":           ["security_auditor", "reviewer"],
    "web-research":         ["security_auditor", "researcher"],

    # ── Layer 3: Test Engineer ────────────────────────────────────────────
    "pytest-patterns":      ["test_engineer"],
    "tdd-workflow":         ["test_engineer"],
    "e2e-testing":          ["test_engineer"],
    "verification-loop":    ["test_engineer"],

    # ── Layer 3: Reviewer ─────────────────────────────────────────────────
    "diffs":                ["reviewer"],
    "brand-guidelines":     ["reviewer"],

    # ── Layer 3: Researcher ───────────────────────────────────────────────
    "web-scraping":         ["researcher"],
    "summarize":            ["researcher", "memory"],
    "article-writing":      ["researcher"],
    "market-research":      ["researcher"],
    "investor-materials":   ["researcher"],
    "content-engine":       ["researcher"],
    "weather":              ["researcher"],
    "prose":                ["researcher"],

    # ── Legacy: map old "developer" role to backend_developer ─────────────
    "coding-agent":         ["backend_developer", "developer"],
    "github":               ["backend_developer", "developer"],
    "gh-issues":            ["backend_developer", "developer"],
    "skill-creator":        ["developer"],
    "doc-coauthoring":      ["developer"],

    # ── Document/media creation ───────────────────────────────────────────
    "pdf":                  ["researcher", "developer"],
    "docx":                 ["researcher", "developer"],
    "xlsx":                 ["researcher", "developer"],
    "pptx":                 ["researcher", "developer"],

    # ── Misc ──────────────────────────────────────────────────────────────
    "apple-notes":          ["developer"],
    "apple-reminders":      ["developer"],
    "openai-whisper":       ["developer", "researcher"],
    "video-frames":         ["developer"],
    "camsnap":              ["developer"],
    "peekaboo":             ["developer"],
    "internal-comms":       ["developer"],
    "oracle":               ["developer"],
    "slack-gif-creator":    ["developer"],
}

_skills_cache: dict[str, str] = {}
_skills_dir: Path | None = None


def _find_skills_dir() -> Path | None:
    """Find the .claude/skills directory relative to the project root."""
    global _skills_dir
    if _skills_dir is not None:
        return _skills_dir
    # Check relative to this file (project root)
    root = Path(__file__).resolve().parent
    candidate = root / ".claude" / "skills"
    if candidate.is_dir():
        _skills_dir = candidate
        return _skills_dir
    return None


def scan_skills(extra_dirs: list[str | Path] | None = None) -> dict[str, str]:
    """Scan .claude/skills/*/SKILL.md and cache the contents. Returns {name: content}.

    Args:
        extra_dirs: Additional directories to scan for skills (e.g., project-specific
                    .claude/skills folders). Skills found here are merged into the cache;
                    project-specific skills override global ones with the same name.
    """
    global _skills_cache
    _skills_cache.clear()

    # Scan global skills first
    skills_dir = _find_skills_dir()
    if skills_dir:
        _scan_dir(skills_dir)

    # Scan extra dirs (project-specific skills override global ones)
    if extra_dirs:
        for d in extra_dirs:
            p = Path(d) / ".claude" / "skills"
            if p.is_dir():
                _scan_dir(p)
                logger.info(f"Scanned project-specific skills from {p}")

    logger.info(f"Skills registry: {len(_skills_cache)} skills loaded")
    return _skills_cache


def _scan_dir(skills_dir: Path):
    """Scan a single skills directory and add to cache."""
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            _skills_cache[skill_dir.name] = content
            logger.info(f"Loaded skill: {skill_dir.name} ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"Failed to load skill {skill_dir.name}: {e}")


def get_skill_content(name: str) -> str | None:
    """Get the SKILL.md content for a named skill."""
    if not _skills_cache:
        scan_skills()
    return _skills_cache.get(name)


def list_skills() -> list[str]:
    """Return all available skill names."""
    if not _skills_cache:
        scan_skills()
    return list(_skills_cache.keys())


def get_skills_for_agent(agent_role: str) -> list[str]:
    """Return skill names auto-mapped to the given agent role.

    Supports multi-role skills: a skill is included if the agent_role
    appears in its roles list.
    """
    if not _skills_cache:
        scan_skills()
    return [
        name for name, roles in SKILL_AGENT_MAP.items()
        if agent_role in roles and name in _skills_cache
    ]


def select_skills_for_task(agent_role: str, task: str, max_skills: int = 2) -> list[str]:
    """Select the most relevant skills for a task using keyword + description matching.

    Instead of injecting ALL skills for a role (e.g. 48 developer skills = ~43K tokens),
    this picks only the top N most relevant skills based on the task text.
    Falls back to all skills if the role has <= max_skills total.

    Reduced from 5 to 2 to prevent Context Rot (per Anthropic's context engineering guide).
    More skills = more noise = the agent loses focus on the actual task.

    Args:
        agent_role: The agent role (frontend_developer, backend_developer, etc.)
        task: The task description to match against.
        max_skills: Maximum number of skills to inject (default 2 → ~2-3K tokens).
    """
    if not _skills_cache:
        scan_skills()

    all_skills = get_skills_for_agent(agent_role)
    if len(all_skills) <= max_skills:
        return all_skills  # Small enough — inject all

    task_lower = task.lower()
    # Tokenise task into words (strip punctuation)
    import re as _re
    task_words = set(_re.sub(r"[^a-z0-9 ]", " ", task_lower).split())

    scores: dict[str, int] = {}
    for skill_name in all_skills:
        content = _skills_cache.get(skill_name, "")

        # 1. Score from skill name (highest signal)
        name_words = set(skill_name.replace("-", " ").split())
        name_score = len(name_words & task_words) * 3

        # 2. Score from frontmatter description line
        desc_score = 0
        for line in content.splitlines()[:10]:
            if line.lower().startswith("description:"):
                desc = _re.sub(r"[^a-z0-9 ]", " ", line[12:].lower())
                desc_words = set(desc.split())
                desc_score = len(desc_words & task_words) * 2
                break

        # 3. Bonus: skill name appears verbatim as substring in task
        verbatim_bonus = 4 if skill_name.replace("-", " ") in task_lower else 0

        scores[skill_name] = name_score + desc_score + verbatim_bonus

    # Sort by score descending; tie-break alphabetically for determinism
    ranked = sorted(all_skills, key=lambda s: (-scores[s], s))

    # Only include skills with a positive relevance score — never inject irrelevant fillers
    relevant = [s for s in ranked if scores[s] > 0]

    result = relevant[:max_skills]
    logger.debug(
        f"select_skills_for_task({agent_role}): {len(all_skills)} total → {result} selected "
        f"(scores: {[(s, scores[s]) for s in result]})"
    )
    return result


def build_skill_prompt(skill_names: list[str]) -> str:
    """Build a skill context string to append to a sub-agent's system prompt.

    Uses XML tags for clear separation (per Anthropic's prompt engineering guide).
    Truncates each skill to 2500 chars to keep total injection under ~5K tokens.
    """
    if not _skills_cache:
        scan_skills()

    parts = []
    for name in skill_names:
        content = _skills_cache.get(name)
        if content:
            # Truncate to keep prompt reasonable — 2500 chars per skill
            truncated = content[:2500]
            if len(content) > 2500:
                truncated += "\n... (truncated)"
            parts.append(f'<skill name="{name}">\n{truncated}\n</skill>')

    if not parts:
        return ""

    return (
        "\n\n<skills>\n"
        "Reference these skills for best practices relevant to your task:\n"
        + "\n".join(parts)
        + "\n</skills>"
    )
