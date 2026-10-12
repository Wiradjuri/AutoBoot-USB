"""Concurrent status replacement while the GUI polls, as during a real USB write.

The elevated helper republishes status.json up to ten times a second while the
GUI reads it five times a second. On Windows, replacing a file that another
handle has open (or opening one mid-replacement) fails transiently. Neither
side may turn that ordinary overlap into a failed or cancelled USB write.
"""

import shutil
import threading
import time
import unittest

from autoboot_usb.services.writer_session import atomic_publish, create_session, read_message


class StatusConcurrencyTests(unittest.TestCase):
    def test_rapid_replacement_while_polling_never_errors(self):
        session = create_session()
        self.addCleanup(shutil.rmtree, session, True)
        atomic_publish(session, "status.json", {"state": "writing", "count": 0, "total": 1})
        stop = threading.Event()
        errors: list[BaseException] = []
        published = [0]

        def publisher():
            try:
                for count in range(1, 1501):
                    if stop.is_set():
                        return
                    atomic_publish(
                        session, "status.json", {"state": "writing", "count": count, "total": 1500}
                    )
                    published[0] = count
            except BaseException as exc:  # recorded and asserted below
                errors.append(exc)
            finally:
                stop.set()

        thread = threading.Thread(target=publisher)
        thread.start()
        reads = 0
        deadline = time.monotonic() + 120
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                try:
                    value = read_message(session)
                except BaseException as exc:
                    errors.append(exc)
                    break
                if value:
                    self.assertEqual(value["state"], "writing")
                reads += 1
        finally:
            stop.set()
            thread.join(30)
        self.assertFalse(errors, f"after {published[0]} publishes and {reads} reads: {errors!r}")
        self.assertEqual(published[0], 1500)
        self.assertEqual(read_message(session)["count"], 1500)


if __name__ == "__main__":
    unittest.main()
