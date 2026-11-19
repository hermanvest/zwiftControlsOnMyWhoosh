"""Run from a checkout: ``uv run main.py`` (same as the ``zwhoosh`` command)."""

from zwiftcontrols.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
