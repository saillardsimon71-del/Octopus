"""Interface graphique Podalux (cockpit)."""

from . import app as _app

PodaluxApp = _app.PodaluxApp


def main() -> None:
    """Stable entrypoint used by the CLI and standalone launcher."""
    PodaluxApp().mainloop()


# Compatibility: existing callers import `main` from `agents.gui.app`.
_app.main = main

__all__ = ["PodaluxApp", "main"]
