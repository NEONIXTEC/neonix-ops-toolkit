import os

from conftest import FakeClock
from support_bot import KnowledgeBase


def _write(path, text, mtime):
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_hot_reload_on_mtime_change(tmp_path):
    clock = FakeClock()
    f = tmp_path / "faq.md"
    _write(f, "v1", 100)
    kb = KnowledgeBase(f, recheck_sec=5, clock=clock)
    assert kb.text() == "v1"

    _write(f, "v2", 200)
    assert kb.text() == "v1"          # inside recheck window: no stat, cached copy
    clock.t += 5
    assert kb.text() == "v2"          # window passed, mtime changed: reloaded without a restart


def test_missing_or_broken_file_keeps_last_copy(tmp_path):
    clock = FakeClock()
    f = tmp_path / "faq.md"
    _write(f, "good", 100)
    kb = KnowledgeBase(f, recheck_sec=1, clock=clock)
    assert kb.text() == "good"

    f.unlink()
    clock.t += 1
    assert kb.text() == "good"        # file gone: never raises, keeps the last good copy

    f.write_bytes(b"\xff\xfe broken")
    os.utime(f, (300, 300))
    clock.t += 1
    assert kb.text() == "good"        # undecodable: keeps the last copy


def test_never_loaded_returns_empty(tmp_path):
    kb = KnowledgeBase(tmp_path / "nope.md")
    assert kb.text() == ""


def test_oversized_file_is_not_loaded(tmp_path):
    clock = FakeClock()
    f = tmp_path / "faq.md"
    _write(f, "small", 100)
    kb = KnowledgeBase(f, recheck_sec=1, max_bytes=10, clock=clock)
    assert kb.text() == "small"
    _write(f, "x" * 50, 200)
    clock.t += 1
    assert kb.text() == "small"
