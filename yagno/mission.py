"""Mission runtime — long-running, multi-feature, milestone-validated execution.

Architecture
────────────
  MissionSpec (YAML) → MissionRuntime
                           │
                 ┌─────────┴──────────┐
                 ▼                    ▼
          Orchestrator           Per-milestone loop
          (context carrier)         │
                              ┌─────┴──────────────────────┐
                              │  Feature workers (fresh ctx) │
                              └─────┬──────────────────────┘
                                    │
                              Validator agent
                              (pass / fail / retry)

Key design choices
──────────────────
- Each feature executes in a fresh Agno Agent (isolated context window).
- The orchestrator carries a rolling summary between milestones when
  carry_context=True.
- Validation runs after every milestone; on FAIL the orchestrator
  optionally prompts a fix worker up to max_validation_retries times.
- No changes to existing WorkflowSpec / YagnoRuntime behavior.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from yagno.compiler import _build_model, _build_tool_registry, _build_mcp_tools
from yagno.config import (
    MissionFeatureSpec,
    MissionMilestoneSpec,
    MissionSpec,
    MissionWorkerSpec,
    MissionValidatorSpec,
    ToolSpec,
    MCPSpec,
)
from yagno.expressions import resolve_refs
from yagno.registry import load_prompt_file

logger = logging.getLogger("yagno.mission")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FeatureResult:
    feature_id: str
    feature_name: str
    status: str           # "completed" | "failed" | "skipped"
    content: str = ""
    error: str | None = None
    duration: float = 0.0
    retries_used: int = 0


@dataclass
class MilestoneResult:
    milestone_id: str
    milestone_name: str
    status: str           # "completed" | "validation_failed" | "error"
    features: list[FeatureResult] = field(default_factory=list)
    validation_passed: bool = True
    validation_feedback: str = ""
    validation_retries: int = 0
    duration: float = 0.0


@dataclass
class MissionResult:
    mission_id: str
    mission_name: str
    status: str           # "completed" | "partial" | "failed"
    milestones: list[MilestoneResult] = field(default_factory=list)
    summary: str = ""
    total_duration: float = 0.0

    # Quick helpers
    @property
    def completed_features(self) -> int:
        return sum(
            1 for m in self.milestones for f in m.features if f.status == "completed"
        )

    @property
    def failed_features(self) -> int:
        return sum(
            1 for m in self.milestones for f in m.features if f.status == "failed"
        )

    @property
    def total_features(self) -> int:
        return sum(len(m.features) for m in self.milestones)


# ---------------------------------------------------------------------------
# Display callbacks protocol — keeps execution logic display-agnostic
# ---------------------------------------------------------------------------

class MissionDisplayCallbacks(Protocol):
    """Optional hooks for live display during mission execution.

    All methods have no-op defaults so callers can implement only
    the callbacks they care about.
    """

    def on_milestone_started(
        self, name: str, index: int, total: int
    ) -> None: ...

    def on_feature_started(
        self, name: str, retry: int
    ) -> None: ...

    def on_feature_completed(
        self, name: str, content: str | None, duration: float | None
    ) -> None: ...

    def on_feature_failed(
        self, name: str, error: str | None
    ) -> None: ...

    def on_validation_result(
        self, milestone_name: str, passed: bool, feedback: str, attempt: int
    ) -> None: ...

    def on_milestone_completed(
        self, name: str, result: MilestoneResult
    ) -> None: ...


class _NoOpCallbacks:
    """Default no-op implementation of MissionDisplayCallbacks."""

    def on_milestone_started(self, name: str, index: int, total: int) -> None:
        pass

    def on_feature_started(self, name: str, retry: int = 0) -> None:
        pass

    def on_feature_completed(
        self, name: str, content: str | None = None, duration: float | None = None
    ) -> None:
        pass

    def on_feature_failed(self, name: str, error: str | None = None) -> None:
        pass

    def on_validation_result(
        self, milestone_name: str, passed: bool, feedback: str = "", attempt: int = 1
    ) -> None:
        pass

    def on_milestone_completed(self, name: str, result: MilestoneResult) -> None:
        pass


class _RichDisplayCallbacks:
    """Callbacks that delegate to the Rich display module."""

    def on_milestone_started(self, name: str, index: int, total: int) -> None:
        from yagno import display as d
        d.print_milestone_started(name, index, total)

    def on_feature_started(self, name: str, retry: int = 0) -> None:
        from yagno import display as d
        d.print_feature_started(name, retry=retry)

    def on_feature_completed(
        self, name: str, content: str | None = None, duration: float | None = None
    ) -> None:
        from yagno import display as d
        d.print_feature_completed(name, content, duration)

    def on_feature_failed(self, name: str, error: str | None = None) -> None:
        from yagno import display as d
        d.print_feature_failed(name, error)

    def on_validation_result(
        self, milestone_name: str, passed: bool, feedback: str = "", attempt: int = 1
    ) -> None:
        from yagno import display as d
        d.print_validation_result(milestone_name, passed, feedback, attempt=attempt)

    def on_milestone_completed(self, name: str, result: MilestoneResult) -> None:
        from yagno import display as d
        d.print_milestone_completed(name, result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALIDATION_PASS_SIGNAL = "VALIDATION_PASS"
_VALIDATION_FAIL_SIGNAL = "VALIDATION_FAIL"

_VALIDATOR_SYSTEM_TEMPLATE = """\
You are a strict milestone validator. Review the following output from the
milestone workers and decide whether all success criteria are met.

MISSION GOAL:
{goal}

VALIDATION CRITERIA:
{criteria}

Respond with exactly one of these two words on the first line:
  {pass_signal}
  {fail_signal}

Then on a new line, briefly explain your verdict (1–3 sentences maximum).
""".strip()

_ORCHESTRATOR_SUMMARY_TEMPLATE = """\
You are the mission orchestrator. Summarise the completed milestone outputs
for the workers in the next milestone so they have the necessary context.
Keep the summary concise (≤ 200 words). Focus only on facts and deliverables.

COMPLETED MILESTONE: {milestone_name}
FEATURE OUTPUTS:
{feature_outputs}
""".strip()


def _build_worker_agent(
    worker_spec: MissionWorkerSpec,
    tool_registry: dict[str, Any],
    mcp_registry: dict[str, MCPSpec],
    name: str = "worker",
    base_dir: Path | None = None,
) -> Any:
    """Compile a MissionWorkerSpec into a fresh Agno Agent (no persistence)."""
    from agno.agent import Agent

    instructions = list(worker_spec.instructions) + load_prompt_file(worker_spec.prompt_file, base_dir=base_dir)

    tools: list[Any] = []
    for tid in worker_spec.tools:
        if tid in tool_registry:
            tools.append(tool_registry[tid])
        else:
            logger.warning("Worker tool '%s' not found in registry", tid)
    for mid in worker_spec.mcp_servers:
        if mid in mcp_registry:
            tools.append(_build_mcp_tools(mcp_registry[mid]))

    return Agent(
        name=name,
        model=_build_model(worker_spec.model),
        instructions=instructions or None,
        tools=tools or None,
        markdown=worker_spec.markdown,
        retries=worker_spec.retries,
        tool_call_limit=worker_spec.tool_call_limit,
        reasoning=worker_spec.reasoning,
        # Deliberately no DB — fresh context per feature
        add_history_to_context=False,
        add_session_state_to_context=False,
        add_datetime_to_context=True,
    )


def _build_validator_agent(
    validator_spec: MissionValidatorSpec,
    mission_goal: str,
    base_dir: Path | None = None,
) -> Any:
    """Build a validator Agent whose system prompt embeds the criteria."""
    from agno.agent import Agent

    criteria_block = "\n".join(f"  - {c}" for c in validator_spec.criteria) or "  (no explicit criteria)"
    system = _VALIDATOR_SYSTEM_TEMPLATE.format(
        goal=mission_goal,
        criteria=criteria_block,
        pass_signal=_VALIDATION_PASS_SIGNAL,
        fail_signal=_VALIDATION_FAIL_SIGNAL,
    )
    extra = list(validator_spec.instructions) + load_prompt_file(validator_spec.prompt_file, base_dir=base_dir)
    if extra:
        system = system + "\n\n" + "\n".join(extra)

    return Agent(
        name="validator",
        model=_build_model(validator_spec.model),
        instructions=[system],
        markdown=False,
        add_history_to_context=False,
        add_session_state_to_context=False,
        add_datetime_to_context=False,
    )


def _run_agent_sync(agent: Any, prompt: str, stream: bool = False) -> str:
    """Run an Agno Agent synchronously and return the content as a string."""
    result = agent.run(prompt, stream=stream)
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if content is None:
        return str(result)
    return content if isinstance(content, str) else str(content)


def _parse_validation_response(text: str) -> tuple[bool, str]:
    """Parse a validator response into (passed: bool, feedback: str)."""
    lines = text.strip().splitlines()
    first = lines[0].strip().upper() if lines else ""
    passed = _VALIDATION_PASS_SIGNAL in first
    feedback = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return passed, feedback


# ---------------------------------------------------------------------------
# MissionRuntime
# ---------------------------------------------------------------------------

class MissionRuntime:
    """Load and execute a MissionSpec YAML file.

    Usage:
        rt = MissionRuntime("specs/finance_mission.yaml")
        result = rt.run_with_display()
    """

    def __init__(self, mission_path: str) -> None:
        path = Path(mission_path)
        if not path.exists():
            raise FileNotFoundError(f"Mission spec not found: {mission_path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in '{mission_path}': {exc}") from exc

        raw = resolve_refs(raw, {})

        try:
            self.spec: MissionSpec = MissionSpec.model_validate(raw)
        except Exception as exc:
            raise ValueError(
                f"Schema validation error in '{mission_path}':\n{exc}"
            ) from exc

        self._path = mission_path
        self._base_dir = path.parent.resolve()

        # Index features for fast lookup
        self._features: dict[str, MissionFeatureSpec] = {
            f.id: f for f in self.spec.features
        }
        # Build shared tool / MCP registries once
        self._tool_registry = _build_tool_registry(self.spec.tools)
        self._mcp_registry: dict[str, MCPSpec] = {m.id: m for m in self.spec.mcp_servers}

    # ── Internal execution ───────────────────────────────────────────

    def _resolve_worker(self, worker_id: str | None) -> MissionWorkerSpec:
        """Return the MissionWorkerSpec for a feature (or the first defined worker)."""
        if worker_id:
            if worker_id in self.spec.workers:
                return self.spec.workers[worker_id]
            logger.warning(
                "Worker '%s' not found in mission spec; "
                "falling back to first available worker.",
                worker_id,
            )
        if self.spec.workers:
            return next(iter(self.spec.workers.values()))
        # No workers defined at all — use a minimal default
        logger.warning("No workers defined in mission spec; using default MissionWorkerSpec.")
        return MissionWorkerSpec()

    def _execute_feature(
        self,
        feat: MissionFeatureSpec,
        context_prefix: str = "",
        debug: bool = False,
    ) -> FeatureResult:
        """Run a single feature with its assigned worker. Returns FeatureResult."""
        start = time.perf_counter()
        worker_spec = self._resolve_worker(feat.worker)
        agent = _build_worker_agent(
            worker_spec,
            self._tool_registry,
            self._mcp_registry,
            name=feat.name or feat.id,
            base_dir=self._base_dir,
        )

        # Build the feature prompt
        criteria_block = ""
        if feat.success_criteria:
            criteria_block = "\n\nSuccess criteria:\n" + "\n".join(
                f"  - {c}" for c in feat.success_criteria
            )

        prompt = (
            (f"{context_prefix}\n\n---\n\n" if context_prefix else "")
            + f"Feature: {feat.name or feat.id}\n"
            + f"Description: {feat.description}"
            + criteria_block
        )

        attempt = 0
        last_error: str | None = None
        while attempt <= feat.max_retries:
            try:
                content = _run_agent_sync(agent, prompt)
                duration = time.perf_counter() - start
                return FeatureResult(
                    feature_id=feat.id,
                    feature_name=feat.name or feat.id,
                    status="completed",
                    content=content,
                    duration=duration,
                    retries_used=attempt,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Feature '%s' attempt %d failed: %s", feat.id, attempt + 1, exc)
                attempt += 1

        duration = time.perf_counter() - start
        return FeatureResult(
            feature_id=feat.id,
            feature_name=feat.name or feat.id,
            status="failed",
            error=last_error,
            duration=duration,
            retries_used=attempt - 1,
        )

    def _validate_milestone(
        self,
        milestone: MissionMilestoneSpec,
        feature_results: list[FeatureResult],
    ) -> tuple[bool, str]:
        """Run the validator agent for a milestone. Returns (passed, feedback)."""
        validator_spec = self.spec.validators.get(milestone.validator or "")
        if not validator_spec:
            # No validator defined → auto-pass
            return True, "No validator configured — auto-pass."

        validator_agent = _build_validator_agent(validator_spec, self.spec.goal, base_dir=self._base_dir)

        feature_summary = "\n\n".join(
            f"[{fr.feature_name}]:\n{fr.content or '(no output)'}"
            for fr in feature_results
            if fr.status == "completed"
        ) or "(no completed features)"

        verdict_raw = _run_agent_sync(validator_agent, feature_summary)
        return _parse_validation_response(verdict_raw)

    def _orchestrator_summary(
        self,
        milestone_name: str,
        feature_results: list[FeatureResult],
    ) -> str:
        """Generate an inter-milestone context summary via the orchestrator."""
        if not self.spec.orchestrator.carry_context:
            return ""
        orch_spec = self.spec.orchestrator
        orch_agent = _build_worker_agent(
            MissionWorkerSpec(
                model=orch_spec.model,
                instructions=list(orch_spec.instructions) + load_prompt_file(orch_spec.prompt_file, base_dir=self._base_dir),
            ),
            self._tool_registry,
            self._mcp_registry,
            name="orchestrator",
            base_dir=self._base_dir,
        )
        max_chars = orch_spec.context_summary_chars
        outputs = "\n\n".join(
            f"[{fr.feature_name}]: "
            f"{fr.content[:max_chars]}{'...' if len(fr.content) > max_chars else ''}"
            for fr in feature_results
            if fr.status == "completed"
        )
        prompt = _ORCHESTRATOR_SUMMARY_TEMPLATE.format(
            milestone_name=milestone_name,
            feature_outputs=outputs or "(no completed features)",
        )
        try:
            return _run_agent_sync(orch_agent, prompt)
        except Exception as exc:
            logger.warning("Orchestrator summary failed: %s", exc)
            return ""

    def _execute_milestone(
        self,
        milestone: MissionMilestoneSpec,
        context_prefix: str = "",
        debug: bool = False,
        callbacks: _NoOpCallbacks | _RichDisplayCallbacks | None = None,
    ) -> MilestoneResult:
        """Execute all features in a milestone, then run validation with retries.

        This is the single source of truth for milestone execution logic.
        Both ``run()`` and ``run_with_display()`` delegate here, passing
        different callback implementations for display output.
        """
        cb = callbacks or _NoOpCallbacks()
        start = time.perf_counter()
        ms_name = milestone.name or milestone.id
        feature_results: list[FeatureResult] = []

        for fid in milestone.features:
            feat = self._features.get(fid)
            if feat is None:
                logger.error("Feature '%s' referenced in milestone '%s' not found", fid, milestone.id)
                cb.on_feature_failed(fid, "Feature spec not found")
                feature_results.append(FeatureResult(
                    feature_id=fid,
                    feature_name=fid,
                    status="failed",
                    error="Feature spec not found",
                ))
                continue

            feat_name = feat.name or feat.id
            cb.on_feature_started(feat_name, retry=0)

            fr = self._execute_feature(feat, context_prefix=context_prefix, debug=debug)
            feature_results.append(fr)

            if fr.status == "completed":
                cb.on_feature_completed(feat_name, fr.content, fr.duration)
            else:
                cb.on_feature_failed(feat_name, fr.error or "unknown error")

        # Validation loop
        validation_passed = True
        validation_feedback = ""
        validation_retries = 0

        if milestone.validator:
            passed, feedback = self._validate_milestone(milestone, feature_results)
            cb.on_validation_result(ms_name, passed, feedback, attempt=1)
            validation_passed = passed
            validation_feedback = feedback

            while not passed and validation_retries < milestone.max_validation_retries:
                validation_retries += 1
                logger.info(
                    "Milestone '%s' validation failed (attempt %d/%d). Re-running ALL features.",
                    milestone.id, validation_retries, milestone.max_validation_retries,
                )
                # Re-run ALL features — a feature can complete successfully but
                # still produce output that fails quality validation.  The
                # validator feedback is injected as a correction directive.
                retry_context = (
                    context_prefix
                    + f"\n\n[VALIDATION FEEDBACK — attempt {validation_retries}]:\n{feedback}"
                    + "\n\nPlease re-do your work and make sure every piece of "
                    "information described in the validation feedback above is "
                    "explicitly included in your output."
                )
                for i, fr in enumerate(feature_results):
                    feat = self._features.get(fr.feature_id)
                    if feat:
                        feat_name = feat.name or feat.id
                        cb.on_feature_started(feat_name, retry=validation_retries)
                        feature_results[i] = self._execute_feature(
                            feat, context_prefix=retry_context, debug=debug
                        )
                        fr2 = feature_results[i]
                        if fr2.status == "completed":
                            cb.on_feature_completed(feat_name, fr2.content, fr2.duration)
                        else:
                            cb.on_feature_failed(feat_name, fr2.error or "unknown error")

                passed, feedback = self._validate_milestone(milestone, feature_results)
                cb.on_validation_result(
                    ms_name, passed, feedback, attempt=validation_retries + 1
                )
                validation_passed = passed
                validation_feedback = feedback

        status = "completed" if validation_passed else "validation_failed"
        return MilestoneResult(
            milestone_id=milestone.id,
            milestone_name=ms_name,
            status=status,
            features=feature_results,
            validation_passed=validation_passed,
            validation_feedback=validation_feedback,
            validation_retries=validation_retries,
            duration=time.perf_counter() - start,
        )

    # ── Core execution loop (shared by run and run_with_display) ─────

    def _execute_mission(
        self,
        debug: bool = False,
        callbacks: _NoOpCallbacks | _RichDisplayCallbacks | None = None,
    ) -> MissionResult:
        """Core mission execution loop — single source of truth.

        Both ``run()`` and ``run_with_display()`` delegate here.
        """
        cb = callbacks or _NoOpCallbacks()
        start = time.perf_counter()
        milestone_results: list[MilestoneResult] = []
        running_context = f"Mission goal: {self.spec.goal}"
        overall_status = "completed"

        for ms_idx, milestone in enumerate(self.spec.milestones, 1):
            ms_name = milestone.name or milestone.id
            cb.on_milestone_started(ms_name, ms_idx, len(self.spec.milestones))

            ms_result = self._execute_milestone(
                milestone,
                context_prefix=running_context,
                debug=debug,
                callbacks=cb,
            )
            milestone_results.append(ms_result)
            cb.on_milestone_completed(ms_name, ms_result)

            if not ms_result.validation_passed:
                overall_status = "partial"
                logger.warning(
                    "Milestone '%s' validation failed after retries. Mission aborting.",
                    milestone.id,
                )
                break

            # Carry orchestrator summary to next milestone
            running_context = self._orchestrator_summary(
                ms_result.milestone_name, ms_result.features
            )

        total_duration = time.perf_counter() - start
        any_failed = any(m.status != "completed" for m in milestone_results)
        if any_failed and overall_status == "completed":
            overall_status = "partial"

        return MissionResult(
            mission_id=self.spec.id,
            mission_name=self.spec.name,
            status=overall_status,
            milestones=milestone_results,
            total_duration=total_duration,
        )

    # ── Public interface ─────────────────────────────────────────────

    def run(self, debug: bool = False) -> MissionResult:
        """Execute the full mission: milestones in order, with context carry-over."""
        return self._execute_mission(debug=debug, callbacks=_NoOpCallbacks())

    def run_with_display(self, debug: bool = False) -> MissionResult:
        """Run the full mission with live Rich display output."""
        from yagno import display as d

        d.print_mission_header(self.spec)

        result = self._execute_mission(
            debug=debug,
            callbacks=_RichDisplayCallbacks(),
        )

        d.print_mission_summary(result)
        return result


def load_mission(path: str) -> MissionRuntime:
    """Convenience loader. Returns a ready-to-run MissionRuntime."""
    return MissionRuntime(path)
