"""Diagnostics sub-package.

Each diagnostic is importable on demand via sub-module access, e.g.::

    from strategy_health.diagnostics import performance
    performance.compute_performance_diagnostic(...)

Eager top-level imports are deferred to avoid ModuleNotFoundError
when a single new module is added before its siblings.
"""

__all__ = [
    "compute_performance_diagnostic",
    "compute_regime_diagnostic",
    "compute_cost_diagnostic",
    "compute_signal_drift_diagnostic",
    "compute_drawdown_diagnostic",
    "compute_freshness_diagnostic",
]


def __getattr__(name):  # PEP 562 lazy attribute access
    if name == "compute_performance_diagnostic":
        from .performance import compute_performance_diagnostic as fn
        return fn
    if name == "compute_regime_diagnostic":
        from .regime import compute_regime_diagnostic as fn
        return fn
    if name == "compute_cost_diagnostic":
        from .cost import compute_cost_diagnostic as fn
        return fn
    if name == "compute_signal_drift_diagnostic":
        from .signal import compute_signal_drift_diagnostic as fn
        return fn
    if name == "compute_drawdown_diagnostic":
        from .drawdown import compute_drawdown_diagnostic as fn
        return fn
    if name == "compute_freshness_diagnostic":
        from .freshness import compute_freshness_diagnostic as fn
        return fn
    raise AttributeError(f"module 'strategy_health.diagnostics' has no attribute {name!r}")
