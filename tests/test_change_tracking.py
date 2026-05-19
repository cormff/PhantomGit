"""
End-to-end tests for the file-change tracking pipeline.

These tests focus on the watchdog-based code path in `main.py`:

* `_ProjectEventHandler.on_any_event` must actually run when watchdog
  dispatches events (regression test for the MRO bug where
  `FileSystemEventHandler.on_any_event` — a no-op — shadowed our
  handler).
* `FileWatcher.drain_pending` must respect the debounce window and only
  return a project once until a new event arrives.
* `_is_relevant` must filter out `.git/`, excluded dirs, noisy
  extensions and editor swap files.
"""

import importlib.util
import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_main_module():
    """Import main.py without running the real `main()` entry point.

    The module is imported by spec so the `if __name__ == "__main__":`
    guard isn't triggered and the on-disk config in `~/.config/phantomgit`
    is never required.
    """
    config_dir = Path(tempfile.mkdtemp(prefix="phantomgit-test-"))
    os.environ.setdefault("HOME", str(config_dir))

    spec = importlib.util.spec_from_file_location("phantomgit_main", REPO_ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["phantomgit_main"] = module
    spec.loader.exec_module(module)
    return module


main = _load_main_module()


class ProjectEventHandlerDispatchTests(unittest.TestCase):
    """Watchdog must actually deliver events to our handler."""

    def _make_handler(self):
        from watchdog.events import FileSystemEventHandler

        evt_queue = queue.Queue()
        handler_cls = main._build_event_handler_class(FileSystemEventHandler)
        handler = handler_cls(
            project_path="/tmp/proj",
            event_queue=evt_queue,
            exclude_dirs={"node_modules"},
            ignore_extensions=main.FileWatcher.NOISY_EXTENSIONS,
        )
        return evt_queue, handler

    def test_mro_puts_project_handler_first(self):
        """`_ProjectEventHandler` must precede `FileSystemEventHandler` in MRO.

        If the base class wins, its no-op `on_any_event` shadows ours.
        """
        from watchdog.events import FileSystemEventHandler

        handler_cls = main._build_event_handler_class(FileSystemEventHandler)
        mro_names = [c.__name__ for c in handler_cls.__mro__]
        self.assertLess(
            mro_names.index("_ProjectEventHandler"),
            mro_names.index("FileSystemEventHandler"),
            f"_ProjectEventHandler must come before FileSystemEventHandler in MRO; got {mro_names}",
        )

    def test_dispatch_routes_to_our_on_any_event(self):
        """A simulated watchdog dispatch must queue an event."""
        from watchdog.events import FileModifiedEvent

        evt_queue, handler = self._make_handler()
        handler.dispatch(FileModifiedEvent("/tmp/proj/src/foo.py"))
        self.assertEqual(evt_queue.qsize(), 1, "Event was not queued — handler not invoked")
        project_path, ts = evt_queue.get_nowait()
        self.assertEqual(project_path, "/tmp/proj")
        self.assertIsInstance(ts, float)

    def test_dispatch_ignores_directory_events(self):
        from watchdog.events import DirModifiedEvent

        evt_queue, handler = self._make_handler()
        handler.dispatch(DirModifiedEvent("/tmp/proj/src"))
        self.assertEqual(evt_queue.qsize(), 0)

    def test_dispatch_ignores_git_internals(self):
        from watchdog.events import FileModifiedEvent

        evt_queue, handler = self._make_handler()
        handler.dispatch(FileModifiedEvent("/tmp/proj/.git/index"))
        self.assertEqual(evt_queue.qsize(), 0)

    def test_dispatch_ignores_excluded_dirs(self):
        from watchdog.events import FileModifiedEvent

        evt_queue, handler = self._make_handler()
        handler.dispatch(FileModifiedEvent("/tmp/proj/node_modules/foo.js"))
        self.assertEqual(evt_queue.qsize(), 0)

    def test_dispatch_ignores_noisy_extensions(self):
        from watchdog.events import FileModifiedEvent

        evt_queue, handler = self._make_handler()
        handler.dispatch(FileModifiedEvent("/tmp/proj/src/foo.pyc"))
        self.assertEqual(evt_queue.qsize(), 0)

    def test_dispatch_ignores_editor_swap_files(self):
        from watchdog.events import FileModifiedEvent

        evt_queue, handler = self._make_handler()
        for swap in (".#emacs.lock", "foo.py~", "foo.py.swp"):
            handler.dispatch(FileModifiedEvent(f"/tmp/proj/src/{swap}"))
        self.assertEqual(evt_queue.qsize(), 0)


class ProjectEventHandlerWithRealObserverTests(unittest.TestCase):
    """End-to-end: spin up a real watchdog Observer and write a file."""

    def test_real_filesystem_event_reaches_queue(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        evt_queue = queue.Queue()
        handler_cls = main._build_event_handler_class(FileSystemEventHandler)

        with tempfile.TemporaryDirectory() as tmp:
            handler = handler_cls(
                project_path=tmp,
                event_queue=evt_queue,
                exclude_dirs=set(),
                ignore_extensions=main.FileWatcher.NOISY_EXTENSIONS,
            )
            observer = Observer()
            observer.schedule(handler, tmp, recursive=True)
            observer.start()
            try:
                # Give the observer a moment to settle before writing.
                time.sleep(0.2)
                Path(tmp, "hello.txt").write_text("hi", encoding="utf-8")

                deadline = time.time() + 5.0
                while time.time() < deadline and evt_queue.empty():
                    time.sleep(0.05)
            finally:
                observer.stop()
                observer.join(timeout=5)

        self.assertGreater(
            evt_queue.qsize(),
            0,
            "Real filesystem write produced no events — change tracking is broken",
        )


class FileWatcherDebounceTests(unittest.TestCase):
    """`drain_pending` must collapse bursts within the debounce window."""

    def _make_watcher(self, debounce: float = 0.2):
        config = {"snapshot": {"debounce_seconds": debounce}, "scanning": {"exclude_dirs": []}}
        watcher = main.FileWatcher(config, projects=[], excluded=[])
        watcher.event_queue = queue.Queue()
        return watcher

    def test_returns_empty_when_queue_is_empty(self):
        watcher = self._make_watcher()
        self.assertEqual(watcher.drain_pending(), [])

    def test_holds_project_until_debounce_elapses(self):
        watcher = self._make_watcher(debounce=0.3)
        watcher.event_queue.put_nowait(("/tmp/proj-a", time.time()))
        self.assertEqual(watcher.drain_pending(), [])
        time.sleep(0.35)
        self.assertEqual(watcher.drain_pending(), ["/tmp/proj-a"])

    def test_burst_collapses_to_one_emission(self):
        watcher = self._make_watcher(debounce=0.2)
        for _ in range(50):
            watcher.event_queue.put_nowait(("/tmp/proj-a", time.time()))
        # Not yet debounced.
        self.assertEqual(watcher.drain_pending(), [])
        time.sleep(0.25)
        self.assertEqual(watcher.drain_pending(), ["/tmp/proj-a"])
        # Subsequent drain (no new events) must NOT re-emit.
        self.assertEqual(watcher.drain_pending(), [])

    def test_new_event_after_emit_re_arms_debounce(self):
        watcher = self._make_watcher(debounce=0.2)
        watcher.event_queue.put_nowait(("/tmp/proj-a", time.time()))
        time.sleep(0.25)
        self.assertEqual(watcher.drain_pending(), ["/tmp/proj-a"])

        watcher.event_queue.put_nowait(("/tmp/proj-a", time.time()))
        self.assertEqual(watcher.drain_pending(), [])
        time.sleep(0.25)
        self.assertEqual(watcher.drain_pending(), ["/tmp/proj-a"])

    def test_multiple_projects_drain_independently(self):
        watcher = self._make_watcher(debounce=0.2)
        watcher.event_queue.put_nowait(("/tmp/proj-a", time.time()))
        time.sleep(0.1)
        watcher.event_queue.put_nowait(("/tmp/proj-b", time.time()))
        # proj-a is older — only it should be ready first.
        time.sleep(0.15)
        ready = watcher.drain_pending()
        self.assertEqual(ready, ["/tmp/proj-a"])
        time.sleep(0.15)
        self.assertEqual(watcher.drain_pending(), ["/tmp/proj-b"])


if __name__ == "__main__":
    unittest.main()
