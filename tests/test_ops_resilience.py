"""
Tests for the quick bleed-stop: the connection-limit stream watchdog and the
log de-spam filter. No network — a FakeStream simulates alpaca-py's loop.
Run:  ./venv/bin/python -m unittest tests.test_ops_resilience -v
"""

import asyncio
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.log_setup import DedupeRateLimitFilter      # noqa: E402
from modules.data_feed import _supervise_stream          # noqa: E402


class FakeStream:
    """Mimics alpaca-py's _run_forever: _running flips True on connect; if it
    never connects it spins with zero backoff (the real bug)."""

    def __init__(self, fail_forever=False, connect_after=0.0):
        self._running = False
        self._should_run = False
        self._fail_forever = fail_forever
        self._connect_after = connect_after
        self.iters = 0

    async def _run_forever(self):
        self._should_run = True
        self._running = False
        while True:
            if not self._should_run:
                return
            if not self._running:
                if self._fail_forever:
                    self.iters += 1
                    await asyncio.sleep(0)   # zero-backoff tight loop, like the bug
                    continue
                await asyncio.sleep(self._connect_after)
                self._running = True
            await asyncio.sleep(0.01)


class TestStreamWatchdog(unittest.TestCase):
    def test_degrades_when_never_connects(self):
        s = FakeStream(fail_forever=True)
        flagged = {"v": False}

        async def go():
            run_task, degraded = await _supervise_stream(
                s, 0.15, lambda: flagged.__setitem__("v", True))
            await run_task          # returns promptly once we signalled stop
            return degraded

        degraded = asyncio.run(go())
        self.assertTrue(degraded)
        self.assertTrue(flagged["v"])
        self.assertFalse(s._should_run)   # loop was told to stop

    def test_healthy_when_connects(self):
        s = FakeStream(connect_after=0.01)

        async def go():
            run_task, degraded = await _supervise_stream(s, 1.0, lambda: None)
            running = s._running
            run_task.cancel()               # healthy stream runs forever — clean up
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            return degraded, running

        degraded, running = asyncio.run(go())
        self.assertFalse(degraded)
        self.assertTrue(running)


class TestDedupeFilter(unittest.TestCase):
    @staticmethod
    def _rec(msg, name="alpaca.ws"):
        return logging.LogRecord(name, logging.ERROR, __file__, 1, msg, None, None)

    def test_caps_repeated_messages(self):
        f = DedupeRateLimitFilter(min_interval=100)
        self.assertTrue(f.filter(self._rec("connection limit exceeded")))
        self.assertFalse(f.filter(self._rec("connection limit exceeded")))
        self.assertFalse(f.filter(self._rec("connection limit exceeded")))
        self.assertTrue(f.filter(self._rec("a different message")))   # distinct passes

    def test_zero_interval_passes_everything(self):
        f = DedupeRateLimitFilter(min_interval=0)
        self.assertTrue(f.filter(self._rec("m")))
        self.assertTrue(f.filter(self._rec("m")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
