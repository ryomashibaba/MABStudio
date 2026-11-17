"""Development-only source acceptance entry point. Never a distribution QA claim.

Runs the same actual pinned B143 -> Tk path as the packaged acceptance, but
without PyInstaller or an EXE rebuild. No Minecraft contacts or commands.
"""
import sys
from app.acceptance import run_acceptance

if __name__ == '__main__':
    raise SystemExit(run_acceptance(sys.argv[1:]))
