"""Skills registry — scans .claude/skills/*/SKILL.md and exposes content for sub-agent prompts."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Skill name → agent role auto-mapping
SKILL_AGENT_MAP: dict[str, str] = {
    "webapp-testing": "tester",
    "frontend-design": "developer",
    "claude-api": "developer",
    "doc-coauthoring": "developer",
    "mcp-builder": "developer",
    "web-artifacts-builder": "developer",
    "planning-with-files": "orchestrator",
    "skill-creator": "developer",
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
