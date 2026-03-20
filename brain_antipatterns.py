"""Brain Hive — Anti-Pattern Catalog.

A formal registry of known failure patterns with:
- Pattern ID and description
- Detection rules (predicate functions on TaskOutput)
- Severity levels
- Suggested remediations
- Match history for learning

The catalog is pre-loaded with 15+ common multi-agent failure patterns
observed in production. New patterns can be registered at runtime from
Reflexion critiques and experience ledger entries.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AntiPatternMatch:
    """Record of a detected anti-pattern match."""

    pattern_id: str
    task_id: str
    role: str
    severity: str
    remediation: str
    timestamp: float = field(default_factory=time.time)
    details: str = ""


@dataclass
class AntiPattern:
    """A registered anti-pattern with detection logic."""

    id: str
    name: str
    description: str
    severity: Severity
    category: str  # e.g., "output_quality", "token_usage", "scope_creep"
    remediation: str
    detector: Callable[[Any, Any | None], bool]  # (task_output, task_input) -> match?
    match_count: int = 0
    last_matched: float | None = None

    def check(self, task_output: Any, task_input: Any | None = None) -> bool:
        """Run the detector and track matches."""
        try:
            matched = self.detector(task_output, task_input)
            if matched:
                self.match_count += 1
                self.last_matched = time.time()
            return matched
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category,
            "remediation": self.remediation,
            "match_count": self.match_count,
            "last_matched": self.last_matched,
        }


class AntiPatternCatalog:
    """Registry of known failure patterns with automatic detection."""

    def __init__(self) -> None:
        self._patterns: dict[str, AntiPattern] = {}
        self._match_history: list[AntiPatternMatch] = []
        self._max_history = 500
        # Load built-in patterns
        self._register_builtins()

    def register(self, pattern: AntiPattern) -> None:
        """Register a new anti-pattern."""
        self._patterns[pattern.id] = pattern

    def scan(
        self,
        task_output: Any,
        task_input: Any | None = None,
    ) -> list[AntiPatternMatch]:
        """Scan a task output for all known anti-patterns.

        Returns list of matches found.
        """
        matches: list[AntiPatternMatch] = []
        task_id = getattr(task_output, "task_id", "unknown")
        role = ""
        if task_input:
            r = getattr(task_input, "role", None)
            role = r.value if hasattr(r, "value") else str(r) if r else ""

        for pattern in self._patterns.values():
            if pattern.check(task_output, task_input):
                match = AntiPatternMatch(
                    pattern_id=pattern.id,
                    task_id=task_id,
                    role=role,
                    severity=pattern.severity.value,
                    remediation=pattern.remediation,
                )
                matches.append(match)
                self._match_history.append(match)

        # Trim history
        if len(self._match_history) > self._max_history:
            self._match_history = self._match_history[-self._max_history:]

        if matches:
            logger.info(
                "[AntiPattern] %d pattern(s) detected for %s: %s",
                len(matches), task_id,
                ", ".join(m.pattern_id for m in matches),
            )

        return matches

    def get_pattern(self, pattern_id: str) -> AntiPattern | None:
        return self._patterns.get(pattern_id)

    def list_patterns(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._patterns.values()]

    def get_match_history(
        self, limit: int = 50, role: str | None = None
    ) -> list[dict[str, Any]]:
        history = self._match_history
        if role:
            history = [m for m in history if m.role == role]
        return [
            {
                "pattern_id": m.pattern_id,
                "task_id": m.task_id,
                "role": m.role,
                "severity": m.severity,
                "remediation": m.remediation,
                "timestamp": m.timestamp,
            }
            for m in history[-limit:]
        ]

    def get_top_patterns(self, n: int = 5) -> list[dict[str, Any]]:
        """Return the N most frequently matched patterns."""
        sorted_patterns = sorted(
            self._patterns.values(),
            key=lambda p: p.match_count,
            reverse=True,
        )
        return [p.to_dict() for p in sorted_patterns[:n]]

    def get_summary(self) -> dict[str, Any]:
        total_matches = sum(p.match_count for p in self._patterns.values())
        by_severity: dict[str, int] = {}
        for p in self._patterns.values():
            if p.match_count > 0:
                sev = p.severity.value
                by_severity[sev] = by_severity.get(sev, 0) + p.match_count
        return {
            "total_patterns": len(self._patterns),
            "total_matches": total_matches,
            "matches_by_severity": by_severity,
            "top_patterns": self.get_top_patterns(5),
            "recent_matches": self.get_match_history(10),
        }

    # ------------------------------------------------------------------
    # Built-in patterns (10+ as required)
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register the built-in anti-pattern catalog."""

        # 1. Default confidence — agent didn't set confidence
        self.register(AntiPattern(
            id="AP-001",
            name="Default Confidence",
            description="Agent returned default confidence (0.5) without explicitly assessing quality",
            severity=Severity.LOW,
            category="output_quality",
            remediation="Update agent prompt to require explicit confidence assessment",
            detector=lambda out, _: getattr(out, "confidence", 0.5) == 0.5,
        ))

        # 2. Empty artifacts — task completed but produced no files
        self.register(AntiPattern(
            id="AP-002",
            name="Empty Artifacts",
            description="Task completed successfully but produced no file artifacts",
            severity=Severity.MEDIUM,
            category="output_quality",
            remediation="Verify task actually modified files; may indicate a read-only run",
            detector=lambda out, _: (
                _status_is(out, "completed")
                and len(getattr(out, "artifacts", [])) == 0
            ),
        ))

        # 3. Token explosion — excessive token usage (>3x baseline)
        self.register(AntiPattern(
            id="AP-003",
            name="Token Explosion",
            description="Task consumed >60K tokens, indicating possible infinite loop or scope creep",
            severity=Severity.HIGH,
            category="token_usage",
            remediation="Add tighter scope constraints; check for recursive tool calls or file reads",
            detector=lambda out, _: getattr(out, "total_tokens", 0) > 60_000,
        ))

        # 4. Scope creep — too many files modified
        self.register(AntiPattern(
            id="AP-004",
            name="Scope Creep",
            description="Task modified significantly more files than specified in files_scope",
            severity=Severity.MEDIUM,
            category="scope_creep",
            remediation="Tighten files_scope constraint and add scope discipline to prompt",
            detector=_detect_scope_creep,
        ))

        # 5. Silent failure — task failed but gave no failure details
        self.register(AntiPattern(
            id="AP-005",
            name="Silent Failure",
            description="Task failed without providing failure details or category",
            severity=Severity.HIGH,
            category="error_handling",
            remediation="Ensure agent prompt requires structured error reporting on failure",
            detector=lambda out, _: (
                _status_is(out, "failed")
                and not getattr(out, "failure_details", "")
                and getattr(out, "failure_category", None) is None
            ),
        ))

        # 6. Missing required artifacts — required artifact types not produced
        self.register(AntiPattern(
            id="AP-006",
            name="Missing Required Artifacts",
            description="Task did not produce one or more required structured artifact types",
            severity=Severity.HIGH,
            category="output_quality",
            remediation="Add explicit artifact creation instructions to the agent prompt",
            detector=_detect_missing_artifacts,
        ))

        # 7. Cascade failure — remediation of a remediation
        self.register(AntiPattern(
            id="AP-007",
            name="Remediation Cascade",
            description="This is a remediation task for another remediation (chain depth > 1)",
            severity=Severity.CRITICAL,
            category="execution_flow",
            remediation="Break the cycle: reassign to a different role or escalate to human",
            detector=lambda out, inp: (
                inp is not None
                and getattr(inp, "is_remediation", False)
                and "fix_fix_" in getattr(inp, "id", "")
            ),
        ))

        # 8. Hallucinated imports — summary mentions files not in artifacts
        self.register(AntiPattern(
            id="AP-008",
            name="Summary-Artifact Mismatch",
            description="Agent summary claims work on files not listed in artifacts",
            severity=Severity.MEDIUM,
            category="output_quality",
            remediation="Cross-validate summary claims against actual file modifications",
            detector=_detect_summary_mismatch,
        ))

        # 9. Zero-turn completion — suspiciously fast task
        self.register(AntiPattern(
            id="AP-009",
            name="Zero-Turn Completion",
            description="Task completed in 0-1 turns — likely didn't do meaningful work",
            severity=Severity.MEDIUM,
            category="execution_flow",
            remediation="Verify output quality; agent may have produced template/stub output",
            detector=lambda out, _: (
                _status_is(out, "completed")
                and getattr(out, "turns_used", 0) <= 1
            ),
        ))

        # 10. Blocker without escalation — has blockers but still marked completed
        self.register(AntiPattern(
            id="AP-010",
            name="Ignored Blockers",
            description="Task marked as completed despite having listed blockers",
            severity=Severity.HIGH,
            category="output_quality",
            remediation="If blockers exist, task status should be 'blocked' or 'needs_followup'",
            detector=lambda out, _: (
                _status_is(out, "completed")
                and len(getattr(out, "blockers", [])) > 0
            ),
        ))

        # 11. Repeated failure category — same failure type keeps recurring
        self.register(AntiPattern(
            id="AP-011",
            name="Repeated Failure",
            description="Same failure category occurring across multiple tasks for this role",
            severity=Severity.HIGH,
            category="execution_flow",
            remediation="Address root cause: missing dependency, incorrect prompt, or env issue",
            detector=lambda out, _: False,  # Detected at aggregate level, not per-task
        ))

        # 12. Low confidence completion — completed but with very low confidence
        self.register(AntiPattern(
            id="AP-012",
            name="Low Confidence Completion",
            description="Task completed but agent reported very low confidence (<0.3)",
            severity=Severity.MEDIUM,
            category="output_quality",
            remediation="Review output carefully; consider requesting a second opinion from reviewer",
            detector=lambda out, _: (
                _status_is(out, "completed")
                and getattr(out, "confidence", 0.5) < 0.3
            ),
        ))

        # 13. Excessive issues — too many issues reported
        self.register(AntiPattern(
            id="AP-013",
            name="Excessive Issues",
            description="Task reported more than 10 issues, suggesting deeper problems",
            severity=Severity.MEDIUM,
            category="output_quality",
            remediation="Break the task into smaller, focused subtasks",
            detector=lambda out, _: len(getattr(out, "issues", [])) > 10,
        ))

        # 14. Orphaned followup — suggests followups without completing own task
        self.register(AntiPattern(
            id="AP-014",
            name="Incomplete with Followups",
            description="Task failed but suggests followup work — may be kicking the can",
            severity=Severity.MEDIUM,
            category="execution_flow",
            remediation="Ensure core task is completed before suggesting extensions",
            detector=lambda out, _: (
                _status_is(out, "failed")
                and len(getattr(out, "followups", [])) > 2
            ),
        ))

        # 15. Cost anomaly — task cost significantly exceeds expected budget
        self.register(AntiPattern(
            id="AP-015",
            name="Cost Anomaly",
            description="Task consumed an unusually high number of output tokens (>20K)",
            severity=Severity.MEDIUM,
            category="token_usage",
            remediation="Review for unnecessary verbosity; add output length constraints",
            detector=lambda out, _: getattr(out, "output_tokens", 0) > 20_000,
        ))


# ------------------------------------------------------------------
# Detector helpers
# ------------------------------------------------------------------

def _status_is(output: Any, status: str) -> bool:
    """Check task output status value."""
    s = getattr(output, "status", None)
    if s is None:
        return False
    val = s.value if hasattr(s, "value") else str(s)
    return val == status


def _detect_scope_creep(output: Any, task_input: Any | None) -> bool:
    """Detect when artifacts exceed files_scope by >50%."""
    if task_input is None:
        return False
    scope = getattr(task_input, "files_scope", [])
    artifacts = getattr(output, "artifacts", [])
    if not scope or not artifacts:
        return False
    allowed = len(scope)
    actual = len(artifacts)
    return actual > allowed * 1.5 and actual - allowed >= 3


def _detect_missing_artifacts(output: Any, task_input: Any | None) -> bool:
    """Detect when required artifact types are not produced."""
    if task_input is None:
        return False
    required = getattr(task_input, "required_artifacts", [])
    if not required:
        return False
    if not _status_is(output, "completed"):
        return False

    produced_types = set()
    for art in getattr(output, "structured_artifacts", []):
        t = getattr(art, "type", None)
        val = t.value if hasattr(t, "value") else str(t)
        produced_types.add(val)

    for req in required:
        val = req.value if hasattr(req, "value") else str(req)
        if val not in produced_types:
            return True
    return False


def _detect_summary_mismatch(output: Any, _: Any | None) -> bool:
    """Detect when summary mentions files not in artifacts list."""
    summary = getattr(output, "summary", "")
    artifacts = getattr(output, "artifacts", [])
    if not summary or not artifacts:
        return False

    # Look for file path patterns in summary
    file_pattern = re.compile(r'[\w/]+\.\w{1,5}')
    mentioned_files = set(file_pattern.findall(summary))
    artifact_stems = {a.rsplit("/", 1)[-1] for a in artifacts if isinstance(a, str)}

    # If summary mentions >3 files not in artifacts, flag it
    unmatched = mentioned_files - artifact_stems
    return len(unmatched) > 3


# Module-level singleton
_catalog = AntiPatternCatalog()


def get_anti_pattern_catalog() -> AntiPatternCatalog:
    """Return the module-level singleton catalog."""
    return _catalog
