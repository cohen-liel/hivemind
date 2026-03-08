"""Skills registry — scans .claude/skills/*/SKILL.md and exposes content for sub-agent prompts."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Skill name → agent role auto-mapping
# These skills are automatically appended to the agent's system prompt when delegated
SKILL_AGENT_MAP: dict[str, str] = {
    # Built-in Claude Code skills
    "webapp-testing": "tester",
    "frontend-design": "developer",
    "claude-api": "developer",
    "doc-coauthoring": "developer",
    "mcp-builder": "developer",
    "web-artifacts-builder": "developer",
    "planning-with-files": "developer",  # Uses task_plan.md/findings.md — conflicts with orchestrator manifest system if injected at orchestrator level
    "skill-creator": "developer",

    # Backend skills — developer
    "fastapi-backend": "developer",
    "sqlalchemy-orm": "developer",
    "jwt-authentication": "developer",
    "redis-caching": "developer",
    "celery-tasks": "developer",
    "async-python": "developer",
    "nodejs-express": "developer",
    "prisma-orm": "developer",
    "typescript-patterns": "developer",
    "git-workflow": "developer",
    "websockets-realtime": "developer",
    "s3-file-storage": "developer",
    "email-service": "developer",
    "stripe-payments": "developer",
    "graphql-api": "developer",
    "state-management": "developer",
    "mobile-react-native": "developer",

    # Frontend skills — developer
    "react-typescript": "developer",
    "nextjs-fullstack": "developer",
    "tailwind-css": "developer",

    # Infrastructure — devops
    "docker-deployment": "devops",
    "postgres-database": "devops",
    "tmux": "devops",
    "healthcheck": "devops",
    "microservices": "devops",

    # Quality & security — reviewer / tester
    "security-review": "reviewer",
    "api-design": "reviewer",
    "brand-guidelines": "reviewer",
    "diffs": "reviewer",
    "pytest-patterns": "tester",
    "tdd-workflow": "tester",
    "e2e-testing": "tester",
    "verification-loop": "tester",

    # Research & analysis — researcher
    "web-research": "researcher",
    "web-scraping": "researcher",
    "market-research": "researcher",
    "summarize": "researcher",
    "article-writing": "researcher",
    "content-engine": "researcher",
    "mermaid-diagrams": "researcher",
    "investor-materials": "researcher",
    "strategic-compact": "researcher",
    "weather": "researcher",

    # Integrations — developer
    "coding-agent": "developer",
    "github": "developer",
    "gh-issues": "developer",
    "apple-notes": "developer",
    "apple-reminders": "developer",

    # Document/media creation — developer
    "pdf": "developer",
    "docx": "developer",
    "xlsx": "developer",
    "pptx": "developer",
    "frontend-slides": "developer",
    "canvas-design": "developer",
    "algorithmic-art": "developer",
    "theme-factory": "developer",
    "openai-whisper": "developer",
    "video-frames": "developer",
    "camsnap": "developer",
    "peekaboo": "developer",
    "internal-comms": "developer",
    "prose": "developer",
    "oracle": "developer",
    "slack-gif-creator": "developer",

    # Orchestration utilities
    "session-logs": "orchestrator",
    "model-usage": "orchestrator",
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
    """Return skill names auto-mapped to the given agent role."""
    if not _skills_cache:
        scan_skills()
    return [
        name for name, role in SKILL_AGENT_MAP.items()
        if role == agent_role and name in _skills_cache
    ]


def select_skills_for_task(agent_role: str, task: str, max_skills: int = 5) -> list[str]:
    """Select the most relevant skills for a task using keyword + description matching.

    Instead of injecting ALL skills for a role (e.g. 48 developer skills = ~43K tokens),
    this picks only the top N most relevant skills based on the task text.
    Falls back to all skills if the role has <= max_skills total.

    Args:
        agent_role: The agent role (developer, tester, reviewer, etc.)
        task: The task description to match against.
        max_skills: Maximum number of skills to inject (default 5 → ~5K tokens).
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

    # Always include skills with a positive score; fill up to max_skills with top-ranked
    relevant = [s for s in ranked if scores[s] > 0]
    if len(relevant) < max_skills:
        # Add highest-ranked skills to fill the quota even if score==0
        extras = [s for s in ranked if s not in relevant]
        relevant.extend(extras[: max_skills - len(relevant)])

    result = relevant[:max_skills]
    logger.debug(
        f"select_skills_for_task({agent_role}): {len(all_skills)} total → {result} selected "
        f"(scores: {[(s, scores[s]) for s in result]})"
    )
    return result


def build_skill_prompt(skill_names: list[str]) -> str:
    """Build a skill context string to append to a sub-agent's system prompt."""
    if not _skills_cache:
        scan_skills()

    parts = []
    for name in skill_names:
        content = _skills_cache.get(name)
        if content:
            # Truncate very long skills to keep prompt reasonable
            truncated = content[:4000]
            if len(content) > 4000:
                truncated += "\n... (skill content truncated)"
            parts.append(f"\n--- Skill: {name} ---\n{truncated}")

    if not parts:
        return ""

    return (
        "\n\nAVAILABLE SKILLS (reference these for best practices):\n"
        + "\n".join(parts)
    )
