"""Interface graphique OCTOPUS : Workbench entrepreneurial principal.

Import paresseux : `agents.gui.strategy` et `agents.gui.workspaces` restent importables
sans customtkinter (logique pure, testable en CI sans affichage).
"""

__all__ = ["EntrepreneurialWorkbench", "PodaluxWorkbench", "main"]


def __getattr__(name: str):
    if name in __all__:
        from . import intelligence
        if name == "PodaluxWorkbench":
            return intelligence.EntrepreneurialWorkbench
        return getattr(intelligence, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
