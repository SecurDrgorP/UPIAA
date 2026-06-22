"""
Circuit Breaker — Thread-safe implementation.
5 failures / 60s window, 120s reset, with threading.Lock for safety.
"""

import threading
import time
from typing import Optional


class CircuitBreakerOpenError(Exception):
    pass


class CircuitBreaker:
    """
    Thread-safe circuit breaker with configurable thresholds.

    States:
      CLOSED    — normal operation, requests pass through
      OPEN      — requests are rejected immediately
      HALF_OPEN — one test request is allowed through
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 120.0,
        window_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.window_seconds = window_seconds

        self.state = "CLOSED"
        self._failure_timestamps: list[float] = []
        self.last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    def _clean_window(self):
        """Remove failures outside the sliding window."""
        cutoff = time.monotonic() - self.window_seconds
        self._failure_timestamps = [
            t for t in self._failure_timestamps if t > cutoff
        ]

    async def call(self, func, *args, **kwargs):
        """Execute a function through the circuit breaker."""
        with self._lock:
            now = time.monotonic()

            if self.state == "OPEN":
                if self.last_failure_time and (now - self.last_failure_time) > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker is OPEN. Try again in "
                        f"{int(self.recovery_timeout - (now - (self.last_failure_time or now)))}s."
                    )

        try:
            result = await func(*args, **kwargs)
            with self._lock:
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                    self._failure_timestamps.clear()
            return result
        except Exception as e:
            with self._lock:
                now = time.monotonic()
                self._failure_timestamps.append(now)
                self.last_failure_time = now
                self._clean_window()

                if self.state == "HALF_OPEN" or len(self._failure_timestamps) >= self.failure_threshold:
                    self.state = "OPEN"
            raise e

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self.state == "OPEN"

    def reset(self):
        """Manually reset the circuit breaker."""
        with self._lock:
            self.state = "CLOSED"
            self._failure_timestamps.clear()
            self.last_failure_time = None
