"""
AsyncLoopRunner — bridges the existing threaded architecture with asyncio-based MCP SDK.

Provides a dedicated asyncio event loop running in a daemon background thread,
allowing synchronous code to submit and await coroutines thread-safely.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


class AsyncLoopRunner:
    """Runs a dedicated asyncio event loop in a background thread.

    This class bridges the GA2A threaded architecture with the asyncio-based
    MCP SDK. It starts a new event loop in a daemon thread and provides methods
    to submit coroutines from synchronous code, either blocking until complete
    or returning a Future for later inspection.

    Usage:
        runner = AsyncLoopRunner()
        runner.start()

        # Blocking call — waits for result
        result = runner.run_coroutine(some_async_function())

        # Non-blocking — returns a Future
        future = runner.schedule(some_async_function())

        runner.stop()
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background event loop thread.

        Idempotent — calling multiple times has no additional effect.
        Blocks until the loop is confirmed running.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._started.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="AsyncLoopRunner",
                daemon=True,
            )
            self._thread.start()
            self._started.wait()

    def stop(self) -> None:
        """Gracefully stop the background event loop and thread.

        Cancels all pending tasks, stops the loop, and joins the thread.
        Safe to call even if the loop is not running.
        """
        with self._lock:
            if self._loop is None or self._thread is None:
                return

            loop = self._loop
            thread = self._thread

        # Schedule shutdown from the loop's thread
        asyncio.run_coroutine_threadsafe(self._shutdown(loop), loop)
        thread.join(timeout=5.0)

        with self._lock:
            self._loop = None
            self._thread = None

    def run_coroutine(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit a coroutine and block until it completes.

        Args:
            coro: The coroutine to execute on the background loop.

        Returns:
            The result of the coroutine.

        Raises:
            RuntimeError: If the loop is not running.
            Exception: Any exception raised by the coroutine is re-raised.
        """
        loop = self._get_running_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def schedule(self, coro: Coroutine[Any, Any, T]) -> asyncio.Future[T]:
        """Submit a coroutine without blocking. Returns a Future.

        The returned Future can be polled or used with concurrent.futures
        utilities. It is a concurrent.futures.Future compatible object.

        Args:
            coro: The coroutine to schedule on the background loop.

        Returns:
            A concurrent.futures.Future representing the eventual result.

        Raises:
            RuntimeError: If the loop is not running.
        """
        loop = self._get_running_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The underlying asyncio event loop.

        Raises:
            RuntimeError: If the loop has not been started.
        """
        return self._get_running_loop()

    # --- Private methods ---

    def _run_loop(self) -> None:
        """Entry point for the background thread. Creates and runs the loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    @staticmethod
    async def _shutdown(loop: asyncio.AbstractEventLoop) -> None:
        """Cancel all pending tasks then stop the loop. Runs inside the loop."""
        # Gather all tasks except the current one (which is this shutdown task)
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks(loop) if t is not current]

        for task in pending:
            task.cancel()

        # Wait for tasks to handle their cancellation
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        loop.stop()

    def _get_running_loop(self) -> asyncio.AbstractEventLoop:
        """Return the loop or raise if not started."""
        if self._loop is None or (self._thread is not None and not self._thread.is_alive()):
            raise RuntimeError(
                "AsyncLoopRunner is not running. Call start() first."
            )
        return self._loop
