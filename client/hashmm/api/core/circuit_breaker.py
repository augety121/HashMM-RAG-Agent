"""Circuit Breaker for external service calls (LLM API, embedding API).

States:
  CLOSED    — normal operation, all calls go through
  OPEN      — too many failures, calls fail immediately with fallback message
  HALF_OPEN — after recovery_timeout, allow ONE probe call to test recovery

Transition:
  CLOSED → OPEN      when consecutive failures >= failure_threshold
  OPEN → HALF_OPEN   when time since last failure >= recovery_timeout
  HALF_OPEN → CLOSED when probe call succeeds
  HALF_OPEN → OPEN   when probe call fails

Usage:
    breaker = CircuitBreaker(name="deepseek", failure_threshold=5, recovery_timeout=30)

    try:
        result = breaker.call(llm_fn, prompt)
    except CircuitOpenError:
        # Return degraded response
        return "AI 服务暂时不可用，请稍后再试"
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from hashmm.utils import get_logger

logger = get_logger("hashmm.circuit_breaker")

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is OPEN and blocking calls."""
    pass


class CircuitBreaker:
    """Thread-safe circuit breaker for unreliable external services."""

    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2

    _STATE_NAMES = {0: "CLOSED", 1: "OPEN", 2: "HALF_OPEN"}

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        excluded_exceptions: tuple[type, ...] = (),
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.excluded_exceptions = excluded_exceptions

        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> int:
        with self._lock:
            if (self._state == self.OPEN
                    and time.time() - self._last_failure_time >= self.recovery_timeout):
                self._state = self.HALF_OPEN
                logger.info(f"[CircuitBreaker:{self.name}] OPEN → HALF_OPEN (probing)")
            return self._state

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn through the circuit breaker.

        Raises CircuitOpenError if the breaker is OPEN.
        """
        current = self.state

        if current == self.OPEN:
            raise CircuitOpenError(
                f"Circuit breaker [{self.name}] is OPEN — "
                f"{self._failure_count} consecutive failures, "
                f"recovering in {self.recovery_timeout - (time.time() - self._last_failure_time):.0f}s"
            )

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            if isinstance(e, self.excluded_exceptions):
                raise
            self._on_failure(e)
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                logger.info(f"[CircuitBreaker:{self.name}] HALF_OPEN → CLOSED (probe succeeded)")
            self._state = self.CLOSED
            self._failure_count = 0

    def _on_failure(self, error: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                logger.warning(
                    f"[CircuitBreaker:{self.name}] HALF_OPEN → OPEN (probe failed: {error})")
            elif self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning(
                    f"[CircuitBreaker:{self.name}] CLOSED → OPEN "
                    f"({self._failure_count} consecutive failures: {error})")

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            logger.info(f"[CircuitBreaker:{self.name}] Manually reset to CLOSED")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self._STATE_NAMES.get(self.state, "UNKNOWN"),
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }
