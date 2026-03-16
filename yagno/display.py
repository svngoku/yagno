"""Rich display helpers for Yagno CLI output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule

if TYPE_CHECKING:
    from yagno.config import WorkflowSpec

console = Console(soft_wrap=True)

# ── theme colours ──────────────────────────────────────────────────────
ACCENT = "cyan"
OK = "green"
WARN = "yellow"
ERR = "red"
DIM = "dim"


# ── Metrics accumulator ──────────────────────────────────────────────

@dataclass
class StepMetrics:
    """Accumulated metrics for a single step."""
    name: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float | None = None
    duration: float | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    model_requests: int = 0


@dataclass
class RunMetricsAccumulator:
    """Accumulates metrics across all steps for the summary table."""
    steps: list[StepMetrics] = field(default_factory=list)
    _current: StepMetrics | None = None

    def start_step(self, name: str) -> None:
        self._current = StepMetrics(name=name)

    def end_step(self) -> None:
        if self._current:
            self.steps.append(self._current)
            self._current = None

    @property
    def current(self) -> StepMetrics | None:
        return self._current

    def record_model_completed(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        if self._current:
            self._current.input_tokens += input_tokens
            self._current.output_tokens += output_tokens
            self._current.total_tokens += total_tokens
            self._current.reasoning_tokens += reasoning_tokens
            self._current.model_requests += 1

    def record_tool_call(self, error: bool = False) -> None:
        if self._current:
            self._current.tool_calls += 1
            if error:
                self._current.tool_errors += 1

    def record_run_completed(self, cost: float | None = None, duration: float | None = None) -> None:
        if self._current:
            if cost is not None:
                self._current.cost = (self._current.cost or 0) + cost
            if duration is not None:
                self._current.duration = duration

    # ── Totals ──────────────────────────────────────────────────
    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.steps)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @property
    def total_cost(self) -> float | None:
        costs = [s.cost for s in self.steps if s.cost is not None]
        return sum(costs) if costs else None

    @property
    def total_tool_calls(self) -> int:
        return sum(s.tool_calls for s in self.steps)

    @property
    def total_tool_errors(self) -> int:
        return sum(s.tool_errors for s in self.steps)


# ── Workflow header / footer ───────────────────────────────────────────

def print_workflow_header(spec: WorkflowSpec, input_msg: str = "", debug: bool = False) -> None:
    """Print a Rich panel summarizing the workflow before execution."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column()

    grid.add_row("Workflow", spec.name)
    if spec.description:
        grid.add_row("", f"[{DIM}]{spec.description}[/{DIM}]")

    agent_ids = [a.id for a in spec.agents]
    tool_ids = [t.id for t in spec.tools]
    council_ids = [c.id for c in spec.councils]
    grid.add_row("Agents", ", ".join(agent_ids) or "-")
    grid.add_row("Tools", ", ".join(tool_ids) or "-")
    if council_ids:
        grid.add_row("Councils", ", ".join(council_ids))
    grid.add_row("Steps", str(len(spec.steps)))

    if debug:
        grid.add_row("Debug", f"[{WARN}]on[/{WARN}]")

    if input_msg:
        preview = input_msg if len(input_msg) <= 120 else input_msg[:117] + "..."
        grid.add_row("Input", f"[{ACCENT}]{preview}[/{ACCENT}]")

    console.print()
    console.print(Panel(grid, title="[bold]yagno[/bold]", border_style=ACCENT, expand=False))
    console.print()


def print_completion(elapsed: float, metrics: RunMetricsAccumulator | None = None) -> None:
    """Print a footer with elapsed time, metrics summary table, and totals."""
    console.print()
    console.print(Rule(style=DIM))

    if metrics and metrics.steps:
        _print_metrics_table(metrics, elapsed)
    else:
        parts = [f"[bold {OK}]Done[/bold {OK}]", f"in [{ACCENT}]{elapsed:.1f}s[/{ACCENT}]"]
        console.print(" ".join(parts))

    console.print()


def _print_metrics_table(metrics: RunMetricsAccumulator, elapsed: float) -> None:
    """Print the per-step metrics summary table."""
    table = Table(
        title="Run Summary",
        title_style="bold",
        show_footer=True,
        footer_style="bold",
        border_style=DIM,
        padding=(0, 1),
    )

    table.add_column("Step", footer="Total")
    table.add_column("Tokens", justify="right", footer=f"{metrics.total_tokens:,}" if metrics.total_tokens else "-")
    table.add_column("In", justify="right", footer=f"{metrics.total_input_tokens:,}" if metrics.total_input_tokens else "-")
    table.add_column("Out", justify="right", footer=f"{metrics.total_output_tokens:,}" if metrics.total_output_tokens else "-")
    table.add_column("Tools", justify="right", footer=str(metrics.total_tool_calls) if metrics.total_tool_calls else "-")
    table.add_column("Cost", justify="right", footer=f"${metrics.total_cost:.4f}" if metrics.total_cost is not None else "-")
    table.add_column("Time", justify="right", footer=f"{elapsed:.1f}s")

    for s in metrics.steps:
        tool_str = str(s.tool_calls) if s.tool_calls else "-"
        if s.tool_errors:
            tool_str += f" [red]({s.tool_errors} err)[/red]"

        table.add_row(
            s.name,
            f"{s.total_tokens:,}" if s.total_tokens else "-",
            f"{s.input_tokens:,}" if s.input_tokens else "-",
            f"{s.output_tokens:,}" if s.output_tokens else "-",
            tool_str,
            f"${s.cost:.4f}" if s.cost is not None else "-",
            f"{s.duration:.1f}s" if s.duration is not None else "-",
        )

    console.print(table)


# ── Step / event display ──────────────────────────────────────────────

def print_step_started(step_name: str, step_index: int | None = None) -> None:
    idx = f" {step_index}" if step_index is not None else ""
    console.print(
        f"\n[bold {ACCENT}]Step{idx}:[/bold {ACCENT}] [bold]{step_name}[/bold]"
    )


def print_step_completed(step_name: str, content: str | None = None) -> None:
    if content:
        console.print(
            Panel(
                Markdown(content),
                title=f"[bold {OK}]{step_name}[/bold {OK}]",
                border_style=OK,
                padding=(1, 2),
            )
        )
    else:
        console.print(f"  [{OK}]completed[/{OK}]")


def print_step_error(step_name: str, error: str | None = None) -> None:
    msg = error or "unknown error"
    console.print(f"  [{ERR}]error:[/{ERR}] {msg}")


# ── Tool calls ────────────────────────────────────────────────────────

def print_tool_call_started(tool_name: str, tool_args: dict[str, Any] | None = None) -> None:
    args_str = ""
    if tool_args:
        compact = {}
        for k, v in tool_args.items():
            s = str(v)
            compact[k] = s if len(s) <= 80 else s[:77] + "..."
        args_str = f" [{DIM}]{json.dumps(compact, ensure_ascii=False)}[/{DIM}]"

    console.print(f"  [{WARN}]tool[/{WARN}] [bold]{tool_name}[/bold]{args_str}")


def print_tool_call_completed(
    tool_name: str,
    result: str | None = None,
    duration: float | None = None,
    error: bool = False,
) -> None:
    status = f"[{ERR}]error[/{ERR}]" if error else f"[{OK}]done[/{OK}]"
    dur = f" [{DIM}]{duration:.1f}s[/{DIM}]" if duration is not None else ""

    console.print(f"  [{WARN}]tool[/{WARN}] {tool_name} {status}{dur}")

    if result:
        preview = result if len(result) <= 300 else result[:297] + "..."
        console.print(f"       [{DIM}]{preview}[/{DIM}]")


def print_tool_call_error(tool_name: str, error: str | None = None) -> None:
    msg = error or "unknown error"
    console.print(f"  [{ERR}]tool {tool_name} failed:[/{ERR}] {msg}")


# ── Model request ─────────────────────────────────────────────────────

def print_model_request(model: str | None = None) -> None:
    if model:
        console.print(f"  [{DIM}]model: {model}[/{DIM}]")


def print_model_completed(
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    ttft: float | None = None,
) -> None:
    parts = []
    if total_tokens:
        parts.append(f"{total_tokens:,} tok")
    elif input_tokens or output_tokens:
        parts.append(f"{input_tokens or 0}+{output_tokens or 0} tok")
    if ttft is not None:
        parts.append(f"ttft={ttft:.2f}s")
    if parts:
        console.print(f"  [{DIM}]{' | '.join(parts)}[/{DIM}]")


# ── Reasoning ─────────────────────────────────────────────────────────

def print_reasoning_step(content: str) -> None:
    console.print(f"  [{DIM}]thinking: {content}[/{DIM}]")


# ── Content streaming ─────────────────────────────────────────────────

def print_content_delta(content: str) -> None:
    """Print a streaming content chunk (no newline)."""
    console.print(content, end="")


# ── Debug event catch-all ─────────────────────────────────────────────

def print_debug_event(event: Any) -> None:
    """Print any unhandled event in debug mode."""
    event_type = type(event).__name__
    console.print(f"  [{DIM}][debug] {event_type}[/{DIM}]")


# ── Workflow error ────────────────────────────────────────────────────

def print_workflow_error(error: str | None = None) -> None:
    msg = error or "unknown error"
    console.print(f"\n[bold {ERR}]Workflow error:[/bold {ERR}] {msg}")


# ── Validate ──────────────────────────────────────────────────────────

def print_validation(spec: WorkflowSpec) -> None:
    """Print a Rich table for the validate command."""
    console.print(
        Panel(
            f"[bold {OK}]{spec.name}[/bold {OK}] ({spec.id})",
            title="Valid spec",
            border_style=OK,
        )
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Component")
    table.add_column("IDs")

    table.add_row("Agents", ", ".join(a.id for a in spec.agents) or "-")
    table.add_row("Teams", ", ".join(t.id for t in spec.teams) or "-")
    if spec.councils:
        table.add_row("Councils", ", ".join(c.id for c in spec.councils))
    table.add_row("Steps", ", ".join(s.id for s in spec.steps) or "-")
    table.add_row("Tools", ", ".join(t.id for t in spec.tools) or "-")
    if spec.mcp_servers:
        table.add_row("MCP", ", ".join(m.id for m in spec.mcp_servers) or "-")
    if spec.knowledge_bases:
        table.add_row("Knowledge", ", ".join(k.id for k in spec.knowledge_bases) or "-")

    console.print(table)


# ── Council display ────────────────────────────────────────────────────

def print_council_header(name: str, members: list[str], rounds: int) -> None:
    """Print a panel showing council debate info."""
    member_list = ", ".join(members) or "-"
    console.print(
        Panel(
            f"Members: {member_list}\nDebate rounds: {rounds}",
            title=f"[bold {ACCENT}]Council: {name}[/bold {ACCENT}]",
            border_style=ACCENT,
            expand=False,
        )
    )


def print_council_synthesis(content: str) -> None:
    """Print the council consensus output."""
    console.print(
        Panel(
            Markdown(content),
            title=f"[bold {OK}]Council Consensus[/bold {OK}]",
            border_style=OK,
            padding=(1, 2),
        )
    )


# ── Mission display ────────────────────────────────────────────────────

MISSION_ACCENT = "magenta"


def print_mission_header(spec: "MissionSpec") -> None:  # type: ignore[name-defined]
    """Print a Rich panel summarising the mission before execution."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column()

    grid.add_row("Mission", spec.name)
    if spec.description:
        grid.add_row("", f"[{DIM}]{spec.description}[/{DIM}]")
    grid.add_row("Goal", f"[italic]{spec.goal}[/italic]")
    grid.add_row("Milestones", str(len(spec.milestones)))
    grid.add_row("Features", str(len(spec.features)))
    if spec.workers:
        grid.add_row("Workers", ", ".join(spec.workers))
    if spec.validators:
        grid.add_row("Validators", ", ".join(spec.validators))
    if spec.tools:
        grid.add_row("Tools", ", ".join(t.id for t in spec.tools))

    console.print()
    console.print(
        Panel(
            grid,
            title=f"[bold {MISSION_ACCENT}]\u2605 Mission Control[/bold {MISSION_ACCENT}]",
            border_style=MISSION_ACCENT,
            expand=False,
        )
    )
    console.print()


def print_milestone_started(
    name: str, index: int, total: int
) -> None:
    """Print a milestone separator."""
    console.print()
    console.print(
        Rule(
            f"[bold {MISSION_ACCENT}]Milestone {index}/{total}: {name}[/bold {MISSION_ACCENT}]",
            style=MISSION_ACCENT,
        )
    )
    console.print()


def print_feature_started(name: str, retry: int = 0) -> None:
    """Print a feature worker starting."""
    retry_tag = f" [dim](retry {retry})[/dim]" if retry else ""
    console.print(
        f"  [{MISSION_ACCENT}]\u25b6 feature[/{MISSION_ACCENT}] "
        f"[bold]{name}[/bold]{retry_tag}"
    )


def print_feature_completed(
    name: str, content: str | None = None, duration: float | None = None
) -> None:
    """Print a completed feature with its output."""
    dur = f" [{DIM}]{duration:.1f}s[/{DIM}]" if duration is not None else ""
    console.print(
        f"  [{OK}]\u2714 feature[/{OK}] [bold]{name}[/bold]{dur}"
    )
    if content:
        console.print(
            Panel(
                Markdown(content),
                title=f"[bold {OK}]{name}[/bold {OK}]",
                border_style=OK,
                padding=(1, 2),
            )
        )


def print_feature_failed(name: str, error: str | None = None) -> None:
    """Print a failed feature."""
    msg = f": {error}" if error else ""
    console.print(f"  [{ERR}]\u2718 feature[/{ERR}] [bold]{name}[/bold]{msg}")


def print_validation_result(
    milestone_name: str,
    passed: bool,
    feedback: str = "",
    attempt: int = 1,
) -> None:
    """Print a validation verdict for a milestone."""
    attempt_tag = f" (attempt {attempt})" if attempt > 1 else ""
    if passed:
        console.print(
            Panel(
                f"[{OK}]PASS[/{OK}] \u2014 {feedback or 'All criteria met.'}" ,
                title=f"[bold {OK}]Validation: {milestone_name}{attempt_tag}[/bold {OK}]",
                border_style=OK,
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                f"[{ERR}]FAIL[/{ERR}] \u2014 {feedback or 'Criteria not met.'}",
                title=f"[bold {ERR}]Validation: {milestone_name}{attempt_tag}[/bold {ERR}]",
                border_style=ERR,
                expand=False,
            )
        )


def print_milestone_completed(
    name: str,
    result: "MilestoneResult",  # type: ignore[name-defined]
) -> None:
    """Print a brief milestone completion summary."""
    completed = sum(1 for f in result.features if f.status == "completed")
    total = len(result.features)
    status_colour = OK if result.validation_passed else ERR
    status_label = "completed" if result.validation_passed else "validation failed"
    console.print(
        f"  [{status_colour}]Milestone '{name}': "
        f"{completed}/{total} features — {status_label} "
        f"({result.duration:.1f}s)[/{status_colour}]"
    )


def print_mission_summary(
    result: "MissionResult",  # type: ignore[name-defined]
) -> None:
    """Print the final mission summary table."""
    console.print()
    console.print(Rule(style=DIM))

    status_colour = OK if result.status == "completed" else (WARN if result.status == "partial" else ERR)

    table = Table(
        title="Mission Summary",
        title_style="bold",
        border_style=DIM,
        padding=(0, 1),
    )
    table.add_column("Milestone")
    table.add_column("Features", justify="right")
    table.add_column("Validation")
    table.add_column("Time", justify="right")

    for m in result.milestones:
        completed = sum(1 for f in m.features if f.status == "completed")
        total = len(m.features)
        v_label = (
            f"[{OK}]pass[/{OK}]" if m.validation_passed else f"[{ERR}]fail[/{ERR}]"
        )
        if m.validation_retries:
            v_label += f" [{DIM}]({m.validation_retries} retries)[/{DIM}]"
        table.add_row(
            m.milestone_name,
            f"{completed}/{total}",
            v_label,
            f"{m.duration:.1f}s",
        )

    console.print(table)
    console.print(
        f"[bold {status_colour}]Mission {result.status}[/bold {status_colour}]  "
        f"\u2014  "
        f"{result.completed_features}/{result.total_features} features  "
        f"[{DIM}]{result.total_duration:.1f}s total[/{DIM}]"
    )
    console.print()
