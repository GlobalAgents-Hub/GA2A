"""Unit tests for AsyncLoopRunner."""

import asyncio
import time
import threading

import pytest

from a2a.mcp.loop_runner import AsyncLoopRunner


@pytest.fixture
def runner():
    """Create and start an AsyncLoopRunner, stop it after the test."""
    r = AsyncLoopRunner()
    r.start()
    yield r
    r.stop()


class TestAsyncLoopRunnerStart:
    """Tests for start() behavior."""

    def test_start_creates_running_loop(self, runner):
        """After start(), the loop should be running."""
        assert runner.loop is not None
        assert runner.loop.is_running()

    def test_start_is_idempotent(self, runner):
        """Calling start() twice does not create a second thread."""
        loop_before = runner.loop
        runner.start()
        assert runner.loop is loop_before

    def test_loop_runs_in_daemon_thread(self, runner):
        """The background thread should be a daemon thread."""
        assert runner._thread is not None
        assert runner._thread.daemon is True


class TestAsyncLoopRunnerStop:
    """Tests for stop() behavior."""

    def test_stop_terminates_loop(self):
        """After stop(), the loop should no longer be running."""
        r = AsyncLoopRunner()
        r.start()
        loop = r.loop
        r.stop()
        assert not loop.is_running()

    def test_stop_when_not_started_is_safe(self):
        """Calling stop() without start() should not raise."""
        r = AsyncLoopRunner()
        r.stop()  # Should not raise

    def test_stop_cancels_pending_tasks(self):
        """Pending tasks should be cancelled on stop."""
        r = AsyncLoopRunner()
        r.start()

        # Schedule a long-running task
        async def long_running():
            await asyncio.sleep(100)

        future = r.schedule(long_running())
        r.stop()

        # The future should be done (cancelled or exception)
        assert future.done()


class TestRunCoroutine:
    """Tests for run_coroutine() behavior."""

    def test_run_coroutine_returns_result(self, runner):
        """run_coroutine() blocks and returns the coroutine's result."""
        async def add(a, b):
            return a + b

        result = runner.run_coroutine(add(3, 4))
        assert result == 7

    def test_run_coroutine_propagates_exception(self, runner):
        """Exceptions in the coroutine are re-raised in the calling thread."""
        async def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            runner.run_coroutine(fail())

    def test_run_coroutine_without_start_raises(self):
        """Calling run_coroutine() before start() raises RuntimeError."""
        r = AsyncLoopRunner()

        async def noop():
            pass

        with pytest.raises(RuntimeError, match="not running"):
            r.run_coroutine(noop())

    def test_run_coroutine_with_async_sleep(self, runner):
        """Verifies that async operations actually work end-to-end."""
        async def delayed_value():
            await asyncio.sleep(0.01)
            return 42

        result = runner.run_coroutine(delayed_value())
        assert result == 42


class TestSchedule:
    """Tests for schedule() behavior."""

    def test_schedule_returns_future(self, runner):
        """schedule() returns a Future that resolves to the coroutine's result."""
        async def compute():
            return 99

        future = runner.schedule(compute())
        assert future.result(timeout=2.0) == 99

    def test_schedule_is_non_blocking(self, runner):
        """schedule() returns immediately without waiting for completion."""
        async def slow():
            await asyncio.sleep(0.5)
            return "done"

        start = time.time()
        future = runner.schedule(slow())
        elapsed = time.time() - start

        # schedule should return nearly instantly
        assert elapsed < 0.1

        # But the future eventually resolves
        assert future.result(timeout=2.0) == "done"

    def test_schedule_without_start_raises(self):
        """Calling schedule() before start() raises RuntimeError."""
        r = AsyncLoopRunner()

        async def noop():
            pass

        with pytest.raises(RuntimeError, match="not running"):
            r.schedule(noop())


class TestLoopProperty:
    """Tests for the loop property."""

    def test_loop_property_returns_event_loop(self, runner):
        """The loop property should return an asyncio event loop."""
        assert isinstance(runner.loop, asyncio.AbstractEventLoop)

    def test_loop_property_without_start_raises(self):
        """Accessing loop before start() raises RuntimeError."""
        r = AsyncLoopRunner()
        with pytest.raises(RuntimeError, match="not running"):
            _ = r.loop


class TestThreadSafety:
    """Tests verifying thread-safe behavior."""

    def test_concurrent_submissions(self, runner):
        """Multiple threads can submit coroutines concurrently."""
        results = []
        errors = []

        async def compute(n):
            await asyncio.sleep(0.01)
            return n * 2

        def submit(n):
            try:
                result = runner.run_coroutine(compute(n))
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors
        assert sorted(results) == [i * 2 for i in range(10)]
