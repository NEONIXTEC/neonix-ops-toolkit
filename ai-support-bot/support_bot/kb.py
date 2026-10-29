"""Knowledge base file with hot reload.

Product facts live in one Markdown file that support staff edit. The bot reads it at call time and
re-reads it when the file's mtime changes, so an edit is live within `recheck_sec` with no restart.

Never raises: a missing, unreadable or oversized file keeps the last good copy (or "" if there never was
one). A broken knowledge file must not take the support bot down with it.
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self, path, recheck_sec=5.0, max_bytes=120_000, clock=time.monotonic):
        self.path = os.fspath(path)
        self.recheck_sec = recheck_sec
        self.max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._text = ""
        self._mtime = None
        self._checked_at = None

    def text(self):
        """# O: current knowledge text ("" if none was ever loaded). Cheap: stats the file at most every recheck_sec."""
        now = self._clock()
        with self._lock:
            if self._checked_at is not None and now - self._checked_at < self.recheck_sec:
                return self._text
            self._checked_at = now
            try:
                st = os.stat(self.path)
            except OSError as e:
                if self._mtime is not None:
                    log.warning("[kb] %s unreadable (%s) - keeping the last copy", self.path, type(e).__name__)
                return self._text
            if st.st_mtime == self._mtime:
                return self._text
            if st.st_size > self.max_bytes:
                log.warning("[kb] %s is %d bytes (> %d) - keeping the last copy", self.path, st.st_size, self.max_bytes)
                return self._text
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._text = f.read()
                self._mtime = st.st_mtime
                log.info("[kb] loaded %s (%d chars)", self.path, len(self._text))
            except (OSError, UnicodeDecodeError) as e:
                log.warning("[kb] read failed (%s) - keeping the last copy", type(e).__name__)
            return self._text
