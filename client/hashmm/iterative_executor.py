"""Iterative Tool Executor — auto-retry tool calls with error correction.

When a tool call (especially code execution) fails, this module:
1. Captures the error
2. Sends the error back to the LLM for analysis
3. Gets a corrected tool call
4. Retries (up to max_retries)

This is similar to how Claude Code works: execute → check → fix → retry.

Enterprise scenarios:
  - Code execution fails with ImportError → auto-install and retry
  - SQL query fails with syntax error → LLM fixes and retries
  - File not found → suggest alternatives
  - Timeout → reduce data size and retry
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Any
from hashmm.utils import get_logger

logger = get_logger("hashmm.iterative_executor")


@dataclass
class ExecutionStep:
    """Record of one tool execution attempt."""
    tool_name: str
    input_args: dict
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    success: bool = False


@dataclass
class ExecutionResult:
    """Final result of iterative execution."""
    success: bool
    output: str
    steps: list[ExecutionStep] = field(default_factory=list)
    total_retries: int = 0
    total_duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "success": self.success, "output": self.output[:500],
            "retries": self.total_retries,
            "duration_ms": self.total_duration_ms,
            "steps": len(self.steps),
        }


class IterativeExecutor:
    """Execute tool calls with automatic error correction.

    Args:
        llm_fn: Function to call LLM for error correction.
                 Signature: llm_fn(messages: list[dict]) -> str
        max_retries: Maximum retry attempts
        tool_registry: Dict of tool_name → callable
    """

    def __init__(self, llm_fn: Callable | None = None,
                 max_retries: int = 3,
                 tool_registry: dict[str, Callable] | None = None):
        self.llm_fn = llm_fn
        self.max_retries = max_retries
        self.tool_registry = tool_registry or {}

    def register_tool(self, name: str, fn: Callable):
        """Register a tool function."""
        self.tool_registry[name] = fn

    def execute(self, tool_name: str, args: dict,
                context: str = "") -> ExecutionResult:
        """Execute a tool call, retrying on failure with LLM correction.

        Args:
            tool_name: Name of the tool to call
            args: Tool arguments
            context: Optional context about what the user is trying to do

        Returns:
            ExecutionResult with success status and output
        """
        if tool_name not in self.tool_registry:
            return ExecutionResult(
                success=False,
                output=f"Tool '{tool_name}' not found. Available: {list(self.tool_registry.keys())}",
            )

        t0 = time.time()
        steps: list[ExecutionStep] = []
        current_args = dict(args)

        for attempt in range(self.max_retries + 1):
            step = ExecutionStep(tool_name=tool_name, input_args=dict(current_args))
            step_t0 = time.time()

            try:
                tool_fn = self.tool_registry[tool_name]
                output = tool_fn(**current_args)
                step.output = str(output)[:2000]
                step.success = True
                step.duration_ms = round((time.time() - step_t0) * 1000)
                steps.append(step)

                logger.info(f"Tool '{tool_name}' succeeded on attempt {attempt + 1}")
                return ExecutionResult(
                    success=True, output=str(output),
                    steps=steps, total_retries=attempt,
                    total_duration_ms=round((time.time() - t0) * 1000),
                )

            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                step.error = error_msg
                step.duration_ms = round((time.time() - step_t0) * 1000)
                steps.append(step)

                logger.warning(f"Tool '{tool_name}' failed (attempt {attempt + 1}/{self.max_retries + 1}): {error_msg}")

                # If we have more retries and an LLM, ask it to fix the error
                if attempt < self.max_retries and self.llm_fn:
                    corrected = self._ask_llm_to_fix(
                        tool_name, current_args, error_msg, context, steps
                    )
                    if corrected:
                        current_args = corrected
                        continue
                    else:
                        break
                elif attempt >= self.max_retries:
                    break

        # All retries exhausted
        all_errors = "; ".join(s.error for s in steps if s.error)
        return ExecutionResult(
            success=False,
            output=f"Failed after {len(steps)} attempts: {all_errors}",
            steps=steps, total_retries=len(steps) - 1,
            total_duration_ms=round((time.time() - t0) * 1000),
        )

    def _ask_llm_to_fix(self, tool_name: str, args: dict,
                         error: str, context: str,
                         history: list[ExecutionStep]) -> dict | None:
        """Ask the LLM to fix the tool call based on the error."""
        if not self.llm_fn:
            return None

        prev_attempts = "\n".join(
            f"Attempt {i+1}: args={s.input_args}, error={s.error}"
            for i, s in enumerate(history) if s.error
        )

        prompt = (
            f"A tool call failed. Please provide corrected arguments.\n\n"
            f"Tool: {tool_name}\n"
            f"Context: {context}\n"
            f"Current args: {args}\n"
            f"Error: {error}\n"
            f"Previous attempts:\n{prev_attempts}\n\n"
            f"Respond with ONLY the corrected JSON arguments, nothing else."
        )

        try:
            messages = [
                {"role": "system", "content": "You are a tool-call error fixer. Respond ONLY with corrected JSON arguments."},
                {"role": "user", "content": prompt},
            ]

            if callable(self.llm_fn):
                response = self.llm_fn(messages)
            else:
                return None

            # Parse response as JSON
            import json
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            corrected = json.loads(cleaned)
            if isinstance(corrected, dict):
                logger.info(f"LLM suggested fix: {corrected}")
                return corrected
        except Exception as e:
            logger.debug(f"LLM fix parsing failed: {e}")

        return None
