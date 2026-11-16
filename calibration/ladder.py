"""Render the preregistered memory ladder without changing supplied entity fields.

The templates own all prose and whitespace. Callers supply canonical DESIGN 5.1
entity records, so aliases, possessives and brand casing use the bank's forms.
L0/PC have L1's bytes for audit purposes; delivery switches their memory off.
"""
import hashlib
import re
from pathlib import Path

TYPES = ("individual", "organization", "nation")
LEVELS = tuple(f"L{i}" for i in range(8)) + ("PC",)
TEMPLATES = Path(__file__).with_name("ladder")
SLOT = re.compile(r"⟦([TX])\.([A-Za-z_]+)⟧")


def render(level, target, control=None, agent="claude"):
    """Return exact UTF-8 memory bytes, resolving L7 against the control record.

    The replacement is one pass, and incomplete/malformed slots are refused.
    Target/control fields are not inferred from display names or aliases.
    """
    if level not in LEVELS or target.get("type") not in TYPES:
        raise ValueError("unknown ladder level or target type")
    if agent not in ("claude", "codex"):
        raise ValueError("agent must be claude or codex")
    if level == "L7" and (not control or control.get("type") != target["type"]):
        raise ValueError("L7 needs the run's same-type control entity")
    text = (TEMPLATES / f"{level}.{target['type']}.md.tpl").read_text(encoding="utf-8")
    roles = {"T": target, "X": control}

    def replace(match):
        role, field = match.groups()
        value = (roles.get(role) or {}).get(field)
        if not isinstance(value, str) or not value or "⟦" in value or "⟧" in value:
            raise ValueError(f"missing or invalid entity field {role}.{field}")
        return value

    text = SLOT.sub(replace, text)
    if "⟦" in text or "⟧" in text:
        raise ValueError("unresolved or malformed ladder slot")
    if agent == "codex":
        text = text.replace("# Notes for Claude\n", "# Notes for Codex\n", 1)
    return text.encode("utf-8")


def describe(level, target, control=None, agent="claude"):
    """Return the bytes, delivery mode and hash together for spec generation."""
    data = render(level, target, control, agent)
    return {"bytes": data, "memory": "off" if level in ("L0", "PC") else "on",
            "sha256": hashlib.sha256(data).hexdigest()}
