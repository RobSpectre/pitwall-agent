"""Hermes plugin entry module: ``register(ctx)``.

Hermes loads this directory as a package, so the relative import below is the
normal path.  Tools that import the repository root as a bare module (pytest
collecting the tree) get the absolute form instead.
"""

if __package__:
    from .pitwall.plugin import register
else:  # pragma: no cover - only when the root is imported outside its package
    from pitwall.plugin import register

__all__ = ["register"]
