"""observe.log. See ../../capabilities/observe/log.md."""
from __future__ import annotations

from registry import tool
from tools.observe import _store


@tool(name="log", category="observe", doc="observe/log.md")
def log(title: str, issue: str, rule: str = "", fix: str = "", area: str = "",
        source: str = "claude", action_class: str = "") -> dict:
    """Record one observation: a rule violation, a user correction, or a gap worth fixing later.

    Args:
        title: short one-line summary.
        issue: what happened (observed vs expected).
        rule: the rule id this breaks, e.g. "RULE-D-PROJECTS-006": a label only. Anything that isn't a rule id is
            kept in the body instead.
        fix: proposed improvement, if known.
        area: free-form tag, e.g. "mcp-server", "hooks", "delegation".
        source: who logged it ("claude", "user", another agent's name).
        action_class: the kind of slip, e.g. "shell.grep-command" or "shell.inline-code-backslash" (a guard's class):
            what repeat detection keys on. Leave it empty and an observation with the same words joins its class.

    Returns:
        {"id", "file", "status": "open", "class", "repeat_count", "escalate", "bypassed", "hint"}. When escalate is
        true, the same kind of slip happened before (earlier observations plus guard stops of the class, declined
        ones aside): per RULE-D-PROJECTS-007 the fix must be a mechanical guard, not reworded text. bypassed lists
        actioned observations of the class: their fix didn't hold.
    """
    return _store.create(title=title, issue=issue, rule=rule, fix=fix, area=area, source=source,
                         action_class=action_class)
