"""
set_keys.py --- picks stable set keys and maps workspace paths to key-addressed stored paths

Workspace paths address sets by display ID (`/sets/2/set.py`); the stored tree addresses them
by key (`/sets/1A/set.py`), so a renumber moves no file.

Contains:
    set_key(): returns the storage key of a set
    free_set_key(): returns a new set's key: its ID, or the first free `<id>.<n>`
    SetFolders: maps set and transition folders of one show between display IDs and keys
    SetFolders.to_stored(): maps a workspace path to its stored path
    SetFolders.to_display(): maps a stored path to its workspace path
    to_stored_path(): maps a workspace path to its stored path, given the show
    to_display_path(): maps a stored path to its workspace path, given the show
"""

from collections.abc import Iterable, Mapping

from showpy.choreo.models import Set
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import StorageError
from showpy.core.ids import check_id
from showpy.show.models import Show

SETS_ROOT = "sets"
TRANSITIONS_ROOT = "transitions"
PAIR_SEPARATOR = "-"
PAIR_PARTS = 2
ROOT_INDEX = 1
FOLDER_INDEX = 2
FIRST_SUFFIX = 2


def set_key(current: Set) -> str:
    """Returns the folder a set is stored under; the Set validator always fills the key.

    >>> from showpy.examples import two_set_show
    >>> set_key(two_set_show().sets[1])
    '2'
    """
    return current.key or current.id


def free_set_key(taken: Iterable[str], set_id: str) -> str:
    """Returns `set_id` as a new set's key, or the first `<set_id>.<n>` (n from 2) still free.

    Keys compare ignoring case.

    Args:
        taken: Keys of the sets already in the show.
        set_id: Display ID of the new set.

    Returns:
        key: A key no live set holds, checked against the ID pattern.

    >>> free_set_key(["1", "2", "2.2"], "2")
    '2.3'
    """
    folded = {key.casefold() for key in taken}
    if set_id.casefold() not in folded:
        return check_id(set_id, kind="set key")
    # Pigeonhole: one of len(folded) + 1 suffixed candidates is always free.
    candidates = (f"{set_id}.{n}" for n in range(FIRST_SUFFIX, FIRST_SUFFIX + len(folded) + 1))
    return check_id(next(c for c in candidates if c.casefold() not in folded), kind="set key")


def _ambiguous(path: str, folder: str, owner: str, target: str) -> StorageError:
    return StorageError(
        f"{path} names folder {folder}, which matches the folder {target} of set {owner}",
        hint=f"address that set as /sets/{owner}/…; add sets with add_set() and save_show(),"
        " which pick a free key",
        context={"path": path, "folder": folder, "set": owner, "matches": target},
        code=ErrorCode.AMBIGUOUS_PATH,
    )


class SetFolders:
    """Maps the set folders of one show between display IDs and storage keys.

    A folder that names no set maps to itself, unless it matches another set's folder in the
    target space ignoring case; then the path is refused rather than guessed.

    Attributes:
        key_by_id: Storage key of each set, by display ID.
        id_by_key: Display ID of each set, by storage key.
    """

    def __init__(self, show: Show | None) -> None:
        sets = () if show is None else show.sets
        self.key_by_id: Mapping[str, str] = {s.id: set_key(s) for s in sets}
        self.id_by_key: Mapping[str, str] = {set_key(s): s.id for s in sets}
        self._id_by_folded_key = {key.casefold(): i for i, key in self.key_by_id.items()}
        self._key_by_folded_id = {i.casefold(): key for key, i in self.id_by_key.items()}

    def to_stored(self, path: str) -> str:
        """Maps a workspace path such as `/sets/2/set.py` to its stored path.

        >>> from showpy.examples import two_set_show
        >>> SetFolders(two_set_show()).to_stored("/transitions/1-2/paths.json")
        '/transitions/1-2/paths.json'
        """
        return _mapped(path, self.key_by_id, self._id_by_folded_key)

    def to_display(self, path: str) -> str:
        """Maps a stored path such as `/sets/1A/set.py` to its workspace path.

        >>> from showpy.examples import two_set_show
        >>> SetFolders(two_set_show()).to_display("/sets/2/set.py")
        '/sets/2/set.py'
        """
        return _mapped(path, self.id_by_key, self._key_by_folded_id)


def _folder(path: str, folder: str, names: Mapping[str, str], owners: Mapping[str, str]) -> str:
    if folder in names:
        return names[folder]
    owner = owners.get(folder.casefold())
    if owner is None:
        return folder
    raise _ambiguous(path, folder, owner, names[owner])


def _mapped(path: str, names: Mapping[str, str], owners: Mapping[str, str]) -> str:
    parts = path.split("/")
    if len(parts) <= FOLDER_INDEX or parts[ROOT_INDEX] not in (SETS_ROOT, TRANSITIONS_ROOT):
        return path
    is_pair = parts[ROOT_INDEX] == TRANSITIONS_ROOT
    folder = parts[FOLDER_INDEX]
    ends = folder.split(PAIR_SEPARATOR) if is_pair else [folder]
    if is_pair and len(ends) != PAIR_PARTS:
        return path
    mapped = PAIR_SEPARATOR.join(_folder(path, end, names, owners) for end in ends)
    return "/".join([*parts[:FOLDER_INDEX], mapped, *parts[FOLDER_INDEX + 1 :]])


def to_stored_path(path: str, show: Show | None) -> str:
    """Maps a workspace path to its stored path; paths outside sets and transitions are kept.

    Args:
        path: Workspace path, addressing sets by display ID.
        show: Show at the workspace revision; None before the first save.

    Returns:
        stored_path: The path addressing sets by key.

    >>> from showpy.examples import two_set_show
    >>> to_stored_path("/sets/2/set.py", two_set_show())
    '/sets/2/set.py'
    """
    return SetFolders(show).to_stored(path)


def to_display_path(path: str, show: Show | None) -> str:
    """Maps a stored path to its workspace path; paths outside sets and transitions are kept.

    Args:
        path: Stored path, addressing sets by key.
        show: Show at the stored revision; None before the first save.

    Returns:
        display_path: The path addressing sets by display ID.

    >>> to_display_path("/renders/a.png", None)
    '/renders/a.png'
    """
    return SetFolders(show).to_display(path)
