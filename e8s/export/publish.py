"""
publish.py --- old path of the publishing names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from e8s.core.deprecation import warn_leaving

LEAVING_PLAIN = frozenset(
    {
        "claim_spot",
        "claims",
        "join_link",
        "notify_changes",
        "pause",
        "performer_view",
        "publish",
        "qr_code",
        "reassign",
        "release",
        "swap_claims",
        "unclaimed",
        "unpublish",
        "verify_join_token",
        "who_is_on",
    }
)


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("e8s.export._publish")
    if name in LEAVING_PLAIN:
        warn_leaving(
            f"e8s.export.publish.{name}",
            since="0.4.0",
            remove="0.6.0",
            replacement=None,
        )
        return getattr(private, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
