"""
core.pyi --- typing stub of e8s._core.core, the compiled counterpart of e8s.core

Contains:
    _finite_probe(): returns a finite number and raises SchemaError for NaN or an infinity
    _cache_probe(): caches a converted Box2 and returns the cached value's address
    _cache_stats(): returns the probe cache's hits, misses and live entries
"""

from e8s.core.types import Box2

def _finite_probe(value: float) -> float: ...
def _cache_probe(part: Box2) -> int: ...
def _cache_stats() -> dict[str, int]: ...
