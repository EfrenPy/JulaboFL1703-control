"""Module entrypoint to run the Julabo CLI with ``python -m julabo_control``."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - module CLI
    raise SystemExit(main())
