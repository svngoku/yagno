"""Runtime: load YAML → compile → run/arun workflows."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml

from yagno.config import WorkflowSpec
from yagno.compiler import compile_workflow
from yagno.expressions import resolve_refs

logger = logging.getLogger("yagno.runtime")


class YagnoRuntime:
    """Main entrypoint for loading and running Yagno workflows.

    Usage:
        rt = YagnoRuntime("specs/my_workflow.yaml")
        result = await rt.arun({"topic": "AI agents"})
    """

    def __init__(self, workflow_path: str) -> None:
        path = Path(workflow_path)
        if not path.exists():
            raise FileNotFoundError(f"Workflow spec not found: {workflow_path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in '{workflow_path}': {exc}") from exc

        # Resolve top-level env references (e.g. db.url: ${env:DATABASE_URL})
        raw = resolve_refs(raw, {})

        try:
            self.spec = WorkflowSpec.model_validate(raw)
        except Exception as exc:
            raise ValueError(
                f"Schema validation error in '{workflow_path}':\n{exc}"
            ) from exc

        self.workflow = compile_workflow(self.spec)

    def run(
        self,
        input_data: dict[str, Any] | str,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Synchronous run."""
        return self.workflow.run(
            input=input_data,
            session_id=session_id or self.spec.session_id,
            **kwargs,
        )

    async def arun(
        self,
        input_data: dict[str, Any] | str,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Async run — recommended for long-running agents."""
        return await self.workflow.arun(
            input=input_data,
            session_id=session_id or self.spec.session_id,
            **kwargs,
        )

    def print_response(
        self,
        input_data: dict[str, Any] | str,
        stream: bool = True,
        **kwargs: Any,
    ) -> None:
        """Print the workflow response to stdout with streaming."""
        self.workflow.print_response(
            input=input_data,
            stream=stream,
            **kwargs,
        )

    def run_with_display(
        self,
        input_data: dict[str, Any] | str,
        stream: bool = True,
        debug: bool = False,
        **kwargs: Any,
    ) -> None:
        """Run with Rich event-driven display and observability."""
        from yagno import display as d
        from yagno.display import RunMetricsAccumulator
        from agno.run.workflow import (
            StepStartedEvent,
            StepCompletedEvent,
            StepErrorEvent,
            WorkflowCompletedEvent,
            WorkflowErrorEvent,
        )
        from agno.run.agent import (
            ToolCallStartedEvent,
            ToolCallCompletedEvent,
            ToolCallErrorEvent,
            RunContentEvent,
            RunContentCompletedEvent,
            RunCompletedEvent,
            ModelRequestStartedEvent,
            ModelRequestCompletedEvent,
            ReasoningStepEvent,
        )

        # Build council lookup for display
        council_specs = {c.id: c for c in self.spec.councils}
        council_steps = {
            s.id: council_specs[s.council]
            for s in self.spec.steps
            if s.kind == "council" and s.council and s.council in council_specs
        }

        input_preview = input_data if isinstance(input_data, str) else str(input_data)
        d.print_workflow_header(self.spec, input_preview, debug=debug)

        start = time.perf_counter()
        metrics = RunMetricsAccumulator()

        from agno.run.workflow import WorkflowRunOutput

        # Use stream_events=True to get the full event stream
        result = self.workflow.run(
            input=input_data,
            session_id=self.spec.session_id,
            stream=stream,
            stream_events=True,
            **kwargs,
        )

        # Non-streaming: result is a WorkflowRunOutput, not an iterator
        if isinstance(result, WorkflowRunOutput):
            elapsed = time.perf_counter() - start
            # Extract metrics from executor runs
            if result.step_executor_runs:
                for run_output in result.step_executor_runs:
                    m = getattr(run_output, "metrics", None)
                    if m:
                        metrics.start_step(getattr(run_output, "agent_name", None) or "step")
                        metrics.record_model_completed(
                            input_tokens=getattr(m, "input_tokens", 0),
                            output_tokens=getattr(m, "output_tokens", 0),
                            total_tokens=getattr(m, "total_tokens", 0),
                        )
                        if getattr(run_output, "tools", None):
                            for _ in run_output.tools:
                                metrics.record_tool_call()
                        metrics.record_run_completed(
                            cost=getattr(m, "cost", None),
                            duration=getattr(m, "duration", None),
                        )
                        metrics.end_step()

            content = result.content if isinstance(result.content, str) else str(result.content or "")
            if content:
                d.print_step_completed("result", content)
            d.print_completion(elapsed, metrics)
            return

        events = result

        # Accumulate content for the final step panel
        current_step_name: str | None = None
        accumulated_content: list[str] = []

        for event in events:
            # ── Workflow-level step events ─────────────────────────
            if isinstance(event, StepStartedEvent):
                # Flush previous step
                if current_step_name and accumulated_content:
                    d.print_step_completed(current_step_name, "".join(accumulated_content))
                    accumulated_content.clear()
                    metrics.end_step()

                current_step_name = event.step_name or "step"
                metrics.start_step(current_step_name)
                # Show council header if this step is a council
                matching_council = None
                for sid, cspec in council_steps.items():
                    step_spec = next((s for s in self.spec.steps if s.id == sid), None)
                    if step_spec and (step_spec.name or step_spec.id) == current_step_name:
                        matching_council = cspec
                        break
                if matching_council:
                    d.print_council_header(
                        matching_council.name or matching_council.id,
                        matching_council.members,
                        matching_council.debate_rounds,
                    )
                else:
                    d.print_step_started(current_step_name, event.step_index)

            elif isinstance(event, StepCompletedEvent):
                # Prefer accumulated streaming content (complete) over event.content (may be truncated)
                content = "".join(accumulated_content) if accumulated_content else None
                if not content and event.content and isinstance(event.content, str):
                    content = event.content
                step_name = event.step_name or current_step_name or "step"
                # Use council synthesis panel if this is a council step
                is_council_step = any(
                    (s.name or s.id) == step_name for s in self.spec.steps if s.kind == "council"
                )
                if is_council_step and content:
                    d.print_council_synthesis(content)
                else:
                    d.print_step_completed(step_name, content)
                accumulated_content.clear()
                metrics.end_step()
                current_step_name = None

            elif isinstance(event, StepErrorEvent):
                d.print_step_error(event.step_name or "step", event.error)
                metrics.end_step()

            # ── Agent-level tool call events ───────────────────────
            elif isinstance(event, ToolCallStartedEvent):
                if event.tool:
                    d.print_tool_call_started(
                        event.tool.tool_name or "?",
                        event.tool.tool_args,
                    )

            elif isinstance(event, ToolCallCompletedEvent):
                if event.tool:
                    is_error = event.tool.tool_call_error or False
                    metrics.record_tool_call(error=is_error)
                    duration = None
                    if event.tool.metrics and event.tool.metrics.duration:
                        duration = event.tool.metrics.duration
                    d.print_tool_call_completed(
                        event.tool.tool_name or "?",
                        event.tool.result,
                        duration,
                        error=is_error,
                    )

            elif isinstance(event, ToolCallErrorEvent):
                metrics.record_tool_call(error=True)
                tool_name = event.tool.tool_name if event.tool else "?"
                d.print_tool_call_error(tool_name, event.error)

            # ── Model request events ──────────────────────────────
            elif isinstance(event, ModelRequestStartedEvent):
                d.print_model_request(event.model)

            elif isinstance(event, ModelRequestCompletedEvent):
                metrics.record_model_completed(
                    input_tokens=event.input_tokens or 0,
                    output_tokens=event.output_tokens or 0,
                    total_tokens=event.total_tokens or 0,
                    reasoning_tokens=event.reasoning_tokens or 0,
                )
                d.print_model_completed(
                    event.input_tokens,
                    event.output_tokens,
                    event.total_tokens,
                    event.time_to_first_token,
                )

            # ── Reasoning events ──────────────────────────────────
            elif isinstance(event, ReasoningStepEvent):
                if event.reasoning_content:
                    d.print_reasoning_step(event.reasoning_content)

            # ── Streaming content ─────────────────────────────────
            elif isinstance(event, RunContentEvent):
                if event.content:
                    accumulated_content.append(str(event.content))

            # ── Content completed — use only as fallback ─────────
            elif isinstance(event, RunContentCompletedEvent):
                if not accumulated_content and event.content and isinstance(event.content, str):
                    accumulated_content.append(event.content)

            # ── Agent run completed (capture metrics) ─────────────
            elif isinstance(event, RunCompletedEvent):
                m = event.metrics
                if m:
                    metrics.record_run_completed(
                        cost=getattr(m, "cost", None),
                        duration=getattr(m, "duration", None),
                    )

            # ── Workflow error ────────────────────────────────────
            elif isinstance(event, WorkflowErrorEvent):
                d.print_workflow_error(event.error)

            elif isinstance(event, WorkflowCompletedEvent):
                pass  # handled by footer

            # ── Debug: catch-all for unhandled events ─────────────
            elif debug:
                d.print_debug_event(event)

        # Flush any remaining step
        if current_step_name:
            metrics.end_step()

        elapsed = time.perf_counter() - start
        d.print_completion(elapsed, metrics)


def load_workflow(path: str) -> YagnoRuntime:
    """Convenience function to load a workflow from a YAML file."""
    return YagnoRuntime(path)
