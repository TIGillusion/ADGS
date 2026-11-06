"""Launch only the installed Hermes CLI, after explicit ADGS provider setup.

The gate allows the runtime to assign this process to a Job Object before
Hermes imports can create subprocesses. This file does not import ADGS config.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path


class TurnReceipt(logging.Handler):
    """Record a confirmed CLI turn budget stop without parsing terminal text."""

    def emit(self, record):
        message = record.getMessage()
        if not re.search(r"Turn ended: reason=max_iterations_reached\(\d+/\d+\)", message):
            return
        session = re.search(r"\bsession=([A-Za-z0-9_-]+)", message)
        path = os.environ.get("ADGS_HERMES_RECEIPT")
        if not path or not session:
            return
        try:
            target = Path(path)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"termination_reason": "iteration_limit", "session_id": session[1]}), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            pass  # Observability must not interrupt the CLI.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--gate", required=True)
    args, command = parser.parse_known_args()
    source = Path(args.source).resolve()
    if not (source / "hermes_cli" / "main.py").is_file():
        raise SystemExit("Installed Hermes source missing")
    deadline = time.monotonic() + 30
    while not Path(args.gate).exists():
        if time.monotonic() > deadline:
            raise SystemExit("ADGS process assignment gate timed out")
        time.sleep(0.05)
    sys.path.insert(0, str(source))
    # Hermes resolves --profile during import, before modules cache HOME.
    # Establish its real argv before importing any installed Hermes module.
    sys.argv = ["hermes", *(command[1:] if command and command[0] == "--" else command)]
    from hermes_cli.main import main as hermes_main
    # Patch before main() performs provider discovery and agent/tool startup.
    # PYTHONSTARTUP cannot patch a console-script launch.
    if os.environ.get("ADGS_HERMES_RUN") == "1":
        from provider_compat import install_openai_compat
        install_openai_compat()
        if "--resume" in sys.argv:
            from resume_projection import install_resume_projection
            install_resume_projection(Path(os.environ["ADGS_HERMES_RECEIPT"]).parent)
        from hermes_cli import auth
        from providers import list_providers
        def plugin_aliases():
            aliases = dict(auth._PROVIDER_ALIASES)
            for provider in list_providers():
                for alias in provider.aliases:
                    aliases[alias] = provider.name
            return aliases
        auth._plugin_aliases = plugin_aliases
        from hermes_cli import models
        original = models.opencode_provider_family
        def family(provider_id):
            if str(provider_id or "").strip().lower() == "opencode":
                return None
            return original(provider_id)
        models.opencode_provider_family = family
    logger = logging.getLogger("agent.conversation_loop")
    receipt = TurnReceipt()
    logger.addHandler(receipt)
    try:
        hermes_main()
    finally:
        logger.removeHandler(receipt)


if __name__ == "__main__":
    main()
