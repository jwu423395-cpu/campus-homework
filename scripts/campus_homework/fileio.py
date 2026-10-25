"""Replace a flushed local file, with an explicit Windows error-17 fallback."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import tempfile
import warnings


def _write_file(path: Path, payload: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def replace_file(temporary: Path, path: Path) -> None:
    """Prefer atomic replacement; keep old bytes recoverable on fallback failure."""
    temporary, path = Path(temporary), Path(path)
    try:
        os.replace(temporary, path)
        return
    except OSError as error:
        if platform.system() != "Windows" or getattr(error, "winerror", None) != 17:
            raise
    warnings.warn("Atomic file replacement is unavailable (Windows error 17); using a verified, backup-assisted non-atomic write. Use one writer; a crash may require recovery from a same-directory .<filename>.backup-*.tmp file.", RuntimeWarning, stacklevel=2)
    payload = temporary.read_bytes()
    previous = path.read_bytes() if path.exists() else None
    backup = None
    started = False
    keep_backup = False
    try:
        if previous is not None:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.backup-", suffix=".tmp", delete=False) as stream:
                backup = Path(stream.name)
            _write_file(backup, previous)
        if previous is None:
            # Exclusive creation must not overwrite a file another writer created.
            with path.open("xb") as stream:
                started = True
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            started = True
            _write_file(path, payload)
        if path.read_bytes() != payload:
            raise OSError("File write could not be verified.")
    except BaseException:
        if started and previous is not None:
            try:
                _write_file(path, previous)
            except OSError as restore_error:
                keep_backup = True
                raise OSError(f"File write and restoration failed; previous contents are retained at {backup}") from restore_error
        elif started:
            path.unlink(missing_ok=True)
        raise
    finally:
        if backup is not None and not keep_backup:
            backup.unlink(missing_ok=True)
