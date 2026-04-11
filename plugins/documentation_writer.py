"""Documentation Writer Plugin — sample Hivemind custom agent role.

This plugin registers a ``documentation_writer`` agent role that generates
or updates Markdown documentation for every Python or TypeScript file that
was modified as part of a task.

The agent reads the modified source files and produces/updates matching
``.md`` files in a ``docs/api/`` directory.
"""

from __future__ import annotations

from typing import Any

from plugin_registry import PluginBase


class DocumentationWriterPlugin(PluginBase):
    """Generates Markdown docs for modified source files."""

    # ------------------------------------------------------------------
    # Required PluginBase properties
    # ------------------------------------------------------------------

    @property
    def role_name(self) -> str:
        return "documentation_writer"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a technical documentation specialist embedded in an AI engineering team.\n\n"
            "## Your Mission\n"
            "For every source file listed in your task context that was created or modified,\n"
            "produce or update a corresponding Markdown documentation file under ``docs/api/``.\n\n"
            "## Documentation Format\n"
            "Each Markdown file should include:\n"
            "- **Overview** — one-paragraph description of the module's purpose.\n"
            "- **Public API** — list every exported function, class, or constant with:\n"
            "  - Signature (with type hints)\n"
            "  - One-sentence description\n"
            "  - Parameters table (name | type | description)\n"
            "  - Return value description\n"
            "  - Example usage (as a fenced code block)\n"
            "- **Design Notes** — non-obvious implementation decisions, edge cases, or caveats.\n\n"
            "## Rules\n"
            "- Write clear, concise English. No marketing language.\n"
            "- Do NOT modify the source files themselves — documentation only.\n"
            "- Output path convention: ``docs/api/<module_name>.md``\n"
            "  (e.g. ``orchestrator.py`` → ``docs/api/orchestrator.md``)\n"
            "- If a ``.md`` file already exists, update it in place rather than replacing it.\n"
            "- Preserve any existing hand-written sections marked with ``<!-- keep -->``.\n"
            "- Use GitHub-Flavored Markdown.\n"
        )

    @property
    def file_scope_patterns(self) -> list[str]:
        return [
            "**/*.py",
            "**/*.ts",
            "**/*.tsx",
            "docs/api/**/*.md",
        ]

    @property
    def is_writer(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Optional hooks
    # ------------------------------------------------------------------

    def build_prompt(self, context: dict[str, Any] | None = None) -> str:
        """Inject the list of modified files into the system prompt if available."""
        base = self.system_prompt
        if not context:
            return base

        modified_files: list[str] = context.get("modified_files", [])
        if modified_files:
            file_list = "\n".join(f"  - {f}" for f in modified_files)
            base += (
                f"\n\n## Files to Document (from task context)\n"
                f"Focus on these files first:\n{file_list}\n"
            )
        return base

    def on_load(self) -> None:
        import logging

        logging.getLogger(__name__).info(
            "DocumentationWriterPlugin loaded — role_name='%s'", self.role_name
        )

    def on_unload(self) -> None:
        import logging

        logging.getLogger(__name__).info("DocumentationWriterPlugin unloaded")
