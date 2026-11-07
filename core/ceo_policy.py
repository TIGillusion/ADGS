"""Versioned role material for the fixed Hermes CEO, independent of Worker skills."""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
CEO_SKILL = "adgs-ceo"


def sync_ceo_role(home):
    home = Path(home)
    for source, destination in (
        (ROOT / "config/ceo/SOUL.md", home / "SOUL.md"),
        (ROOT / "skills" / CEO_SKILL / "SKILL.md", home / "skills" / CEO_SKILL / "SKILL.md"),
    ):
        content = source.read_bytes()
        if destination.is_file() and destination.read_bytes() == content:
            continue
        if destination.is_file():
            previous = destination.read_bytes()
            backup = home / "backups/adgs-role" / (destination.name + "." + hashlib.sha256(previous).hexdigest()[:16])
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(previous)
        destination.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        temporary = destination.with_suffix(destination.suffix + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)


def coordinator_guidance():
    return (ROOT / "config/ceo/SOUL.md").read_text(encoding="utf-8") + "\n\n" + (
        ROOT / "skills" / CEO_SKILL / "SKILL.md").read_text(encoding="utf-8")
