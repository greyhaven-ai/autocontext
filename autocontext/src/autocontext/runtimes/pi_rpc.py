"""Pi RPC runtime — stdin/stdout JSONL subprocess communication (AC-375).

Pi RPC mode (`pi --mode rpc`) communicates over the process's stdin/stdout
using strict JSONL framing (LF-delimited). This is NOT an HTTP protocol.

Protocol reference:
  https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from autocontext.runtimes.base import AgentOutput, AgentRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PiRPCConfig:
    """Configuration for the Pi RPC runtime.

    Pi RPC is a subprocess protocol over stdin/stdout JSONL — not HTTP.
    The ``endpoint`` field is intentionally absent.
    """

    pi_command: str = "pi"
    model: str = ""
    timeout: float = 120.0
    session_persistence: bool = True
    no_context_files: bool = False
    branch_on_retry: bool = True
    extra_args: list[str] = field(default_factory=list)


class PiRPCRuntime(AgentRuntime):
    """Agent runtime that communicates with Pi via stdin/stdout RPC.

    Launches ``pi --mode rpc`` as a subprocess and exchanges JSONL
    messages on stdin/stdout per Pi's documented RPC protocol.
    """

    def __init__(self, config: PiRPCConfig | None = None) -> None:
        self._config = config or PiRPCConfig()
        self._pi_path = shutil.which(self._config.pi_command)
        self._current_session_id: str | None = None

    @property
    def available(self) -> bool:
        return self._pi_path is not None

    def _build_args(self) -> list[str]:
        """Build the pi --mode rpc argument list."""
        pi = self._pi_path or self._config.pi_command
        args = [pi, "--mode", "rpc"]
        if self._config.model:
            args.extend(["--model", self._config.model])
        if self._config.no_context_files:
            args.append("--no-context-files")
        if not self._config.session_persistence:
            args.append("--no-session")
        args.extend(self._config.extra_args)
        return args

    def _build_prompt_command(self, prompt: str) -> dict[str, Any]:
        """Build a Pi RPC prompt command.

        Pi's documented RPC protocol expects the user payload under ``message``.
        """
        return {
            "type": "prompt",
            "id": uuid.uuid4().hex[:8],
            "message": prompt,
        }

    def _nonzero_exit_output(self, exit_code: int, stderr: str, stdout: str = "") -> AgentOutput:
        metadata: dict[str, Any] = {
            "error": "nonzero_exit",
            "exit_code": exit_code,
        }
        if stderr:
            metadata["stderr"] = stderr[:500]
        if stdout:
            metadata["stdout"] = stdout[:500]
        return AgentOutput(text="", metadata=metadata)

    def _read_stream(self, stream: Any, sink: queue.Queue[str | None]) -> None:
        try:
            for line in stream:
                sink.put(line)
        finally:
            sink.put(None)

    def _drain_queue(self, source: queue.Queue[str | None], lines: list[str]) -> None:
        while True:
            try:
                line = source.get_nowait()
            except queue.Empty:
                return
            if line is not None:
                lines.append(line)

    def _is_terminal_rpc_event(self, line: str) -> bool:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(event, dict):
            return False
        if event.get("type") == "agent_end":
            return True
        return event.get("type") == "response" and event.get("success") is False

    def _shutdown_process(self, process: subprocess.Popen[str]) -> None:
        if process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                logger.debug("runtimes.pi_rpc: suppressed stdin close error", exc_info=True)
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        """Send a prompt command and collect the response."""
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        command = self._build_prompt_command(full_prompt)

        args = self._build_args()
        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_queue: queue.Queue[str | None] = queue.Queue()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        process: subprocess.Popen[str] | None = None
        try:
            # Keep stdin open after the prompt ack so Pi can stream the agent result.
            input_line = json.dumps(command) + "\n"
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                return AgentOutput(text="", metadata={"error": "pi_rpc_pipe_unavailable"})

            threading.Thread(target=self._read_stream, args=(process.stdout, stdout_queue), daemon=True).start()
            threading.Thread(target=self._read_stream, args=(process.stderr, stderr_queue), daemon=True).start()

            process.stdin.write(input_line)
            process.stdin.flush()

            deadline = time.monotonic() + self._config.timeout
            saw_stdout_eof = False
            terminal_seen = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, self._config.timeout)

                try:
                    line = stdout_queue.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    self._drain_queue(stderr_queue, stderr_lines)
                    if process.poll() is not None and saw_stdout_eof:
                        break
                    continue

                if line is None:
                    saw_stdout_eof = True
                    if process.poll() is not None:
                        break
                    continue

                stdout_lines.append(line)
                if self._is_terminal_rpc_event(line):
                    terminal_seen = True
                    break

            self._drain_queue(stderr_queue, stderr_lines)
            if terminal_seen:
                self._shutdown_process(process)
            else:
                process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            logger.error("pi RPC timed out after %.0fs", self._config.timeout)
            if process is not None:
                process.kill()
                process.wait(timeout=1.0)
            return AgentOutput(text="", metadata={"error": "timeout"})
        except FileNotFoundError:
            logger.error("pi CLI not found at %r", self._config.pi_command)
            return AgentOutput(text="", metadata={"error": "pi_not_found"})

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)
        returncode = process.returncode if process is not None and process.returncode is not None else 0
        if returncode != 0 and not stdout.strip():
            logger.warning("pi RPC exited with code %d: %s", returncode, stderr[:200])
            return self._nonzero_exit_output(returncode, stderr)

        output = self._parse_rpc_output(
            stdout,
            exit_code=returncode,
            stderr=stderr,
        )
        if returncode != 0 and not output.metadata.get("error"):
            logger.warning("pi RPC exited with code %d: %s", returncode, stderr[:200])
            return self._nonzero_exit_output(returncode, stderr, stdout)
        return output

    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        revision_prompt = (
            f"Revise the following output based on the judge's feedback.\n\n"
            f"## Original Output\n{previous_output}\n\n"
            f"## Judge Feedback\n{feedback}\n\n"
            f"## Original Task\n{prompt}\n\n"
            "Produce an improved version:"
        )
        return self.generate(revision_prompt, system=system)

    def _parse_rpc_output(
        self,
        raw: str,
        *,
        exit_code: int = 0,
        stderr: str = "",
    ) -> AgentOutput:
        """Parse JSONL output from pi --mode rpc.

        Collects message_end events to extract the assistant's response.
        Falls back to the last non-empty line if no structured events found.
        """
        text_parts: list[str] = []
        saw_json_event = False

        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                saw_json_event = True
                event_type = event.get("type", "")

                # Collect assistant text from message_end or response events
                if event_type == "response":
                    if event.get("success") is False:
                        metadata: dict[str, Any] = {
                            "error": "rpc_response_error",
                            "rpc_command": str(event.get("command", "")),
                            "exit_code": exit_code,
                        }
                        error_message = event.get("error")
                        if error_message is not None:
                            metadata["rpc_message"] = str(error_message)
                        if stderr:
                            metadata["stderr"] = stderr[:500]
                        return AgentOutput(text="", metadata=metadata)

                    data = event.get("data", {})
                    if isinstance(data, dict) and "content" in data:
                        text_parts.append(str(data["content"]))
                elif event_type == "message_end":
                    msg = event.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        text_parts.append(content)
                elif event_type == "agent_end":
                    # Final messages array
                    messages = event.get("messages", [])
                    for msg in messages:
                        if isinstance(msg, dict) and msg.get("role") == "assistant":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content:
                                text_parts.append(content)
            except (json.JSONDecodeError, TypeError):
                # Not JSONL — treat as plain text fallback
                if not text_parts:
                    if exit_code != 0:
                        return self._nonzero_exit_output(exit_code, stderr, raw.strip())
                    return AgentOutput(text=raw.strip(), metadata={"exit_code": exit_code})

        if text_parts:
            return AgentOutput(text=text_parts[-1], metadata={"exit_code": exit_code})  # Last assistant message

        if exit_code != 0:
            return self._nonzero_exit_output(exit_code, stderr, raw.strip())
        if saw_json_event:
            return AgentOutput(
                text="",
                metadata={
                    "error": "missing_assistant_response",
                    "exit_code": exit_code,
                    "stdout": raw.strip()[:500],
                },
            )
        return AgentOutput(text=raw.strip(), metadata={"exit_code": exit_code})
