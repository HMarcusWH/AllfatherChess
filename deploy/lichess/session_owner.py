"""Single-host OFFLINE-4B session ownership and epoch fencing.

Acquire one nonblocking flock for the entire lifetime of a bridge instance.
A new epoch may be activated only after the old process tree is shut down.
Child processes verify the epoch from SQLite before every API operation.
"""
from __future__ import annotations
import fcntl
import os
import uuid
from pathlib import Path

from deploy.lichess.move_journal import MoveJournal, SessionFenced


class OwnerError(RuntimeError):
    pass


class SessionOwner:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.lock_fd = None
        self.epoch = None
        self.journal = MoveJournal(self.directory / "move-journal.sqlite3")

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / "owner.lock"
        if lock.is_symlink():
            raise OwnerError("symlinked owner lock refused")
        self.lock_fd = os.open(str(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise OwnerError("another bridge owns the bot session") from exc
        try:
            self.journal.initialize()
            self.new_epoch()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def new_epoch(self):
        if self.lock_fd is None:
            raise OwnerError("cannot fence a session without exclusive owner lock")
        self.epoch = uuid.uuid4().hex
        self.journal.activate_owner(self.epoch)
        return self.epoch

    def environment(self) -> dict[str, str]:
        if self.epoch is None:
            raise OwnerError("session owner has no epoch")
        return {
            "ALLFATHER_RECOVERY_DB": str(self.journal.path),
            "ALLFATHER_RECOVERY_EPOCH": self.epoch,
            "ONLINE4B_ISOLATED_CONTAINER": "1",
        }

    def __exit__(self, *_):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None
