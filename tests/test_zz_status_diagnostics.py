"""TEMPORARY CI diagnostics for Windows status replacement conflicts."""

import os
import shutil
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

from autoboot_usb.services import writer_session
from autoboot_usb.services.writer_session import create_session


@unittest.skipUnless(os.name == "nt", "Windows diagnostics")
class Diagnostics(unittest.TestCase):
    def test_report(self):
        session = create_session()
        self.addCleanup(shutil.rmtree, session, True)
        events = []
        t0 = time.monotonic()
        real_replace = os.replace
        real_open = Path.open

        def logged_replace(src, dst):
            try:
                return real_replace(src, dst)
            except OSError as exc:
                events.append(("replace", getattr(exc, "winerror", None), time.monotonic() - t0))
                raise

        def logged_open(path, *a, **k):
            try:
                return real_open(path, *a, **k)
            except OSError as exc:
                events.append(("open", getattr(exc, "winerror", None), time.monotonic() - t0))
                raise

        writer_session.os.replace = logged_replace
        Path.open = logged_open
        stop = threading.Event()
        pub_errors, read_errors, published = [], [], [0]

        def publisher():
            for count in range(400):
                if stop.is_set():
                    return
                try:
                    writer_session.atomic_publish(session, "status.json", {"count": count})
                    published[0] += 1
                except OSError as exc:
                    pub_errors.append((getattr(exc, "winerror", None), str(exc)[:80]))
            stop.set()

        writer_session.atomic_publish(session, "status.json", {"count": -1})
        thread = threading.Thread(target=publisher)
        thread.start()
        reads = 0
        try:
            while not stop.is_set():
                try:
                    writer_session.read_message(session)
                    reads += 1
                except OSError as exc:
                    cause = exc.__cause__
                    read_errors.append(
                        (getattr(cause, "winerror", None), time.monotonic() - t0, str(cause)[:80])
                    )
                    if len(read_errors) > 5:
                        break
        finally:
            stop.set()
            thread.join(60)
            writer_session.os.replace = real_replace
            Path.open = real_open
        kinds = Counter((side, code) for side, code, _ in events)
        first = events[:12]
        spans = {}
        for side, code, at in events:
            lo, hi = spans.get((side, code), (at, at))
            spans[(side, code)] = (min(lo, at), max(hi, at))
        self.fail(
            f"published={published[0]} reads={reads} pub_errors={pub_errors[:3]} "
            f"read_errors={read_errors[:3]} kinds={dict(kinds)} spans={spans} first={first}"
        )
