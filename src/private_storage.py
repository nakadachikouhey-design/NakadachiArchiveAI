from __future__ import annotations

import os
import stat
from pathlib import Path


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
SYSTEM_PATH_ALIASES = {Path("/etc"), Path("/tmp"), Path("/var")}


def set_private_umask() -> None:
    """Prevent new archive derivatives from being group/world-readable."""
    os.umask(0o077)


def ensure_private_directory(path: Path, *, harden_existing: bool = False) -> Path:
    set_private_umask()
    reject_symlink_components(path)
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    reject_symlink_components(path)
    path.chmod(PRIVATE_DIRECTORY_MODE)
    if harden_existing:
        harden_private_tree(path)
    return path


def secure_file(path: Path) -> None:
    if path.exists() and not path.is_symlink():
        path.chmod(PRIVATE_FILE_MODE)


def write_private_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    ensure_private_directory(path.parent)
    reject_symlink_components(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding=encoding) as handle:
        handle.write(text)
    secure_file(path)


def harden_private_tree(root: Path) -> None:
    """Restrict one private tree without following symlinks or touching its parent."""
    set_private_umask()
    reject_symlink_components(root)
    root.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    reject_symlink_components(root)
    root.chmod(PRIVATE_DIRECTORY_MODE)
    for current_root, directories, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        if not current.is_symlink():
            current.chmod(PRIVATE_DIRECTORY_MODE)
        for name in directories:
            directory = current / name
            if not directory.is_symlink():
                directory.chmod(PRIVATE_DIRECTORY_MODE)
        for name in files:
            file_path = current / name
            if not file_path.is_symlink():
                file_path.chmod(PRIVATE_FILE_MODE)


def reject_symlink_components(path: Path) -> None:
    """Reject every existing symlink component before any private write."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) and current not in SYSTEM_PATH_ALIASES:
            raise ValueError(f"Private storage path must not contain symlinks: {current}")
