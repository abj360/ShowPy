"""
assets.py --- the in-memory asset store

Contains:
    InMemoryAssetStore: keeps assets in process memory under content-hash URIs
"""

from e8s.core.errors import (
    SchemaError,
    UnknownRefError,
)
from e8s.core.util import ASSET_PREFIX, asset_uri


class InMemoryAssetStore:
    """Keeps assets in process memory under `asset:sha256:<hex>` URIs."""

    def __init__(self) -> None:
        self._assets: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; equal bytes give the same URI.

        >>> InMemoryAssetStore().put(b"x").startswith(ASSET_PREFIX)
        True
        """
        uri = asset_uri(data)
        self._assets[uri] = data
        return uri

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        if not uri.startswith(ASSET_PREFIX):
            raise SchemaError(
                f"{uri!r} is not an asset URI",
                hint=f"pass a URI returned by put(), starting with {ASSET_PREFIX}",
                context={"uri": uri},
            )
        if uri not in self._assets:
            raise UnknownRefError(
                f"no asset {uri}", hint="store it first with put()", context={"uri": uri}
            )
        return self._assets[uri]
