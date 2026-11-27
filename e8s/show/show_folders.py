"""
show_folders.py --- names the folder that holds one show in a layout-2 repository

Contains:
    SHOW_FOLDER_DOMAIN: prefixes the show ID in the folder hash; a frozen wire name
    SLUG_MAX_CHARS: caps the readable part of a show folder name
    FOLDER_HASH_CHARS: sets how many hex digits of the hash a folder name keeps
    SHOW_FOLDER_PATTERN: matches every name show_folder returns
    show_folder(): names a show's folder: a lower-case slug, a dash and 16 hex digits of a hash
"""

import hashlib
import re

SHOW_FOLDER_DOMAIN = b"e8s.show-folder\n"
SLUG_MAX_CHARS = 32
FOLDER_HASH_CHARS = 16
SHOW_FOLDER_PATTERN = re.compile(r"^[a-z0-9_]{0,32}-[0-9a-f]{16}$")
OUTSIDE_SLUG = re.compile(r"[^a-z0-9_]")


def show_folder(show_id: str) -> str:
    """Names the folder that holds a show, distinct for IDs that differ only by case.

    The slug is the ID lower-cased, with every character outside `[a-z0-9_]` replaced by `_`,
    cut to 32 characters. The hash is the first 16 hex digits of SHA-256 over the domain and the
    ID's UTF-8 bytes. So `Show` and `show` get different folders on a case-insensitive file
    system, a trailing dot never reaches the file system, no name is a Windows device name, and
    no name passes 49 characters.

    Args:
        show_id: The show's ID as stored in its manifest.

    Returns:
        folder: The folder name under the repository's `shows/` folder.

    >>> show_folder("Show"), show_folder("show")
    ('show-f2227dcc569c0978', 'show-7e56e2f6502dbdf4')
    """
    slug = OUTSIDE_SLUG.sub("_", show_id.lower())[:SLUG_MAX_CHARS]
    digest = hashlib.sha256(SHOW_FOLDER_DOMAIN + show_id.encode()).hexdigest()
    return f"{slug}-{digest[:FOLDER_HASH_CHARS]}"
