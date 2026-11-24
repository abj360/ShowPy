"""
core.pyi --- typing stub of e8s._core.core, the compiled counterpart of e8s.core

Contains:
    _finite_probe(): returns a finite number and raises SchemaError for NaN or an infinity
"""

def _finite_probe(value: float) -> float: ...
