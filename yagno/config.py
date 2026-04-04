"""Pydantic models that define the YAML schema for Yagno specs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── Named constants ────────────────────────────────────────────────────
DEFAULT_TOOL_CALL_LIMIT = 15
DEFAULT_CONTEXT_SUMMARY_CHARS = 600

# ── MCP command safety ─────────────────────────────────────────────────
_SHELL_METACHARACTERS = re.compile(r'[;|&$`><(){}\n\\]')
_DANGEROUS_ENV_VARS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "PATH", "HOME", "SHELL", "USER",
    "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "NODE_OPTIONS",
})


class ToolSpec(BaseModel):
    """A tool available to agents."""

    id: str
    kind: Literal["callable", "sandbox", "tavily"] = "callable"
    entrypoint: str | None = None  # dotted path for callable
    provider: Literal["daytona"] | None = None  # for sandbox kind
    # Tavily-specific options
    api_key_env: str | None = None  # env var name for API key (defaults to TAVILY_API_KEY)
    search_depth: Literal["basic", "advanced"] = "advanced"
    max_tokens: int = 6000
    include_answer: bool = True
    include_images: bool = False

    @model_validator(mode="before")
    @classmethod
    def _reject_inline_api_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "api_key" in data:
            raise ValueError(
                "Inline 'api_key' is no longer allowed in tool specs — it risks "
                "leaking credentials into version control. Use 'api_key_env' to "
                "specify an environment variable name instead, e.g. "
                "api_key_env: CUSTOM_TAVILY_KEY (defaults to TAVILY_API_KEY)."
            )
        return data

    @model_validator(mode="after")
    def _check_entrypoint_allowed(self) -> "ToolSpec":
        if self.kind == "callable" and self.entrypoint:
            from yagno.registry import _ALLOWED_PACKAGES

            top_level = self.entrypoint.split(".")[0]
            if top_level not in _ALLOWED_PACKAGES:
                raise ValueError(
                    f"Tool entrypoint '{self.entrypoint}': package '{top_level}' not in "
                    f"import allowlist. Allowed: {_ALLOWED_PACKAGES}. "
                    f"Set YAGNO_ALLOWED_PACKAGES to extend."
                )
        return self


class MCPSpec(BaseModel):
    """An MCP (Model Context Protocol) server providing external tools."""

    id: str
    url: str | None = None
    command: str | None = None
    transport: Literal["streamable-http", "sse", "stdio"] = "stdio"
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_command_safety(self) -> "MCPSpec":
        if self.command:
            if _SHELL_METACHARACTERS.search(self.command):
                raise ValueError(
                    f"MCP command contains shell metacharacters: '{self.command}'. "
                    f"Commands must not contain: ; | & $ ` > < ( ) {{ }} or backslashes. "
                    f"Use direct binary paths instead of shell expressions."
                )
        if self.env:
            bad_vars = set(self.env.keys()) & _DANGEROUS_ENV_VARS
            if bad_vars:
                raise ValueError(
                    f"MCP env contains dangerous variables: {sorted(bad_vars)}. "
                    f"These cannot be overridden: {sorted(_DANGEROUS_ENV_VARS)}"
                )
        return self


class DbSpec(BaseModel):
    """Persistent storage config — required for long-running agents."""

    provider: Literal["postgres", "sqlite"] = "postgres"
    url: str | None = None  # connection string or file path
    table_prefix: str = "yagno"


class KnowledgeSpec(BaseModel):
    """Agentic RAG / Knowledge base definition."""

    id: str
    vector_db: Literal["pgvector", "lancedb"] = "pgvector"
    db_url: str | None = None
    table_name: str | None = None
    embedder_model: str = "text-embedding-3-small"


class AgentSpec(BaseModel):
    """An individual agent definition."""

    id: str
    name: str | None = None
    model: str
    role: str | None = None
    description: str | None = None
    instructions: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)  # references top-level mcp_servers
    knowledge: list[str] = Field(default_factory=list)  # references knowledge_bases
    output_schema: dict[str, Any] | None = None
    markdown: bool = True
    retries: int = 3
    tool_call_limit: int | None = DEFAULT_TOOL_CALL_LIMIT
    add_history_to_context: bool = True
    add_session_state_to_context: bool = True
    add_datetime_to_context: bool = True
    reasoning: bool = False
    stream: bool | None = None
    debug_mode: bool = False


class TeamSpec(BaseModel):
    """A multi-agent team definition."""

    id: str
    name: str | None = None
    model: str | None = None  # leader model
    role: str | None = None
    mode: Literal["coordinate", "route", "broadcast", "tasks"] = "coordinate"
    members: list[str] = Field(default_factory=list)  # agent or nested team ids
    instructions: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    markdown: bool = True
    max_iterations: int = 10


class CouncilSpec(BaseModel):
    """A debate-and-consensus council of agents."""

    id: str
    name: str | None = None
    members: list[str] = Field(default_factory=list)  # agent ids
    debate_rounds: int = 2
    synthesizer_model: str | None = None
    synthesizer_instructions: list[str] = Field(default_factory=list)
    synthesizer_prompt_file: str | None = None
    markdown: bool = True
    num_history_runs: int | None = None


class StepSpec(BaseModel):
    """A single step in a workflow."""

    id: str
    name: str | None = None
    kind: Literal["agent", "team", "council", "function"] = "agent"
    agent: str | None = None  # reference to agent id
    team: str | None = None  # reference to team id
    council: str | None = None  # reference to council id
    executor: str | None = None  # dotted path for function kind
    description: str | None = None
    max_retries: int = 3

    @model_validator(mode="after")
    def _validate_kind_target(self) -> "StepSpec":
        target_map = {
            "agent": "agent",
            "team": "team",
            "council": "council",
            "function": "executor",
        }
        set_targets = [name for name in target_map.values() if getattr(self, name) is not None]
        if len(set_targets) != 1:
            raise ValueError(
                f"Step '{self.id}' must set exactly one of agent/team/council/executor; got {set_targets or 'none'}"
            )
        expected = target_map[self.kind]
        actual = set_targets[0]
        if actual != expected:
            raise ValueError(
                f"Step '{self.id}' kind='{self.kind}' requires '{expected}', but '{actual}' was provided"
            )
        return self

    @model_validator(mode="after")
    def _check_executor_allowed(self) -> "StepSpec":
        if self.kind == "function" and self.executor:
            from yagno.registry import _ALLOWED_PACKAGES

            top_level = self.executor.split(".")[0]
            if top_level not in _ALLOWED_PACKAGES:
                raise ValueError(
                    f"Step executor '{self.executor}': package '{top_level}' not in "
                    f"import allowlist. Allowed: {_ALLOWED_PACKAGES}. "
                    f"Set YAGNO_ALLOWED_PACKAGES to extend."
                )
        return self


# ---------------------------------------------------------------------------
# Mission schema — parallel execution path for long-running, multi-feature goals
# ---------------------------------------------------------------------------


class MissionWorkerSpec(BaseModel):
    """Defines the LLM worker used to execute a feature."""

    model: str = "openrouter:openai/gpt-4.1-mini"
    instructions: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    tools: list[str] = Field(default_factory=list)       # ref → top-level tools
    mcp_servers: list[str] = Field(default_factory=list)  # ref → top-level mcp_servers
    markdown: bool = True
    retries: int = 3
    tool_call_limit: int | None = DEFAULT_TOOL_CALL_LIMIT
    reasoning: bool = False


class MissionValidatorSpec(BaseModel):
    """Defines a validator agent that reviews milestone output and decides pass/fail."""

    model: str = "openrouter:openai/gpt-4.1-mini"
    instructions: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    # Criteria the validator checks — surfaced in its system prompt automatically
    criteria: list[str] = Field(default_factory=list)


class MissionFeatureSpec(BaseModel):
    """A single piece of work within a mission milestone."""

    id: str
    name: str | None = None
    description: str
    worker: str | None = None            # ref → mission-level workers dict; None → use default
    success_criteria: list[str] = Field(default_factory=list)
    # TODO: depends_on and parallel are planned but not yet implemented.
    # See https://github.com/…/issues/XX for tracking.
    max_retries: int = 1


class MissionMilestoneSpec(BaseModel):
    """A checkpoint grouping features, with optional validation before the mission continues."""

    id: str
    name: str | None = None
    description: str | None = None
    features: list[str]                  # ordered list of feature ids in this milestone
    validator: str | None = None         # ref → mission-level validators dict
    # How many times to attempt fixing failing validation before aborting the milestone
    max_validation_retries: int = 2


class MissionOrchestratorSpec(BaseModel):
    """Top-level orchestrator config for the mission — controls planning and handoffs."""

    model: str = "openrouter:openai/gpt-4.1-mini"
    instructions: list[str] = Field(default_factory=list)
    prompt_file: str | None = None
    # Whether to summarize inter-milestone context for the next milestone's workers
    carry_context: bool = True
    # Max chars per feature output included in the orchestrator summary prompt
    context_summary_chars: int = DEFAULT_CONTEXT_SUMMARY_CHARS


class MissionSpec(BaseModel):
    """Root schema for a .yaml file that describes a Missions-style long-running execution."""

    id: str
    name: str
    description: str | None = None
    goal: str                            # the overall goal the mission is working towards

    # Shared infrastructure
    orchestrator: MissionOrchestratorSpec = Field(default_factory=MissionOrchestratorSpec)
    workers: dict[str, MissionWorkerSpec] = Field(default_factory=dict)
    validators: dict[str, MissionValidatorSpec] = Field(default_factory=dict)

    # Tooling (same ToolSpec / MCPSpec as workflows)
    tools: list[ToolSpec] = Field(default_factory=list)
    mcp_servers: list[MCPSpec] = Field(default_factory=list)

    # Plan: ordered milestones containing ordered features
    features: list[MissionFeatureSpec] = Field(default_factory=list)
    milestones: list[MissionMilestoneSpec] = Field(default_factory=list)

    # Persistence / observability
    persistent: bool = False
    db: DbSpec | None = None
    debug_mode: bool = False

    @model_validator(mode="after")
    def _validate_integrity(self) -> "MissionSpec":
        errors: list[str] = []

        if not self.features:
            errors.append("Mission must define at least one feature")
        if not self.milestones:
            errors.append("Mission must define at least one milestone")

        feature_ids = {f.id for f in self.features}
        worker_ids = set(self.workers.keys())
        validator_ids = set(self.validators.keys())

        for f in self.features:
            if f.worker and f.worker not in worker_ids:
                errors.append(f"Feature '{f.id}' references unknown worker '{f.worker}'")

        for m in self.milestones:
            for fid in m.features:
                if fid not in feature_ids:
                    errors.append(f"Milestone '{m.id}' references unknown feature '{fid}'")
            if m.validator and m.validator not in validator_ids:
                errors.append(f"Milestone '{m.id}' references unknown validator '{m.validator}'")

        if errors:
            raise ValueError("Mission validation failed:\n- " + "\n- ".join(errors))
        return self


class WorkflowSpec(BaseModel):
    """Top-level workflow spec — the root of a YAML file."""

    id: str
    name: str
    description: str | None = None
    persistent: bool = True
    background: bool = False
    agentos_enabled: bool = False
    db: DbSpec | None = None
    session_id: str | None = None
    session_state: dict[str, Any] = Field(default_factory=dict)

    # Components
    agents: list[AgentSpec] = Field(default_factory=list)
    teams: list[TeamSpec] = Field(default_factory=list)
    councils: list[CouncilSpec] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    mcp_servers: list[MCPSpec] = Field(default_factory=list)
    knowledge_bases: list[KnowledgeSpec] = Field(default_factory=list)

    # Execution
    steps: list[StepSpec] = Field(default_factory=list)

    # Workflow-level flags
    store_events: bool = True
    stream_events: bool = True
    debug_mode: bool = False

    @model_validator(mode="after")
    def _validate_integrity(self) -> "WorkflowSpec":
        errors: list[str] = []

        if not self.steps:
            errors.append("Workflow must define at least one step")

        namespaces = {
            "tools": [t.id for t in self.tools],
            "mcp_servers": [m.id for m in self.mcp_servers],
            "knowledge_bases": [k.id for k in self.knowledge_bases],
            "agents": [a.id for a in self.agents],
            "teams": [t.id for t in self.teams],
            "councils": [c.id for c in self.councils],
            "steps": [s.id for s in self.steps],
        }

        for ns, ids in namespaces.items():
            seen = set()
            dups = sorted({i for i in ids if i in seen or (seen.add(i) or False)})
            if dups:
                errors.append(f"Duplicate IDs in {ns}: {dups}")

        owner: dict[str, str] = {}
        for ns, ids in namespaces.items():
            for ident in ids:
                if ident in owner and owner[ident] != ns:
                    errors.append(f"ID collision: '{ident}' used in both '{owner[ident]}' and '{ns}'")
                owner.setdefault(ident, ns)

        tool_ids = set(namespaces["tools"])
        mcp_ids = set(namespaces["mcp_servers"])
        kb_ids = set(namespaces["knowledge_bases"])
        agent_ids = set(namespaces["agents"])
        team_ids = set(namespaces["teams"])
        council_ids = set(namespaces["councils"])

        for a in self.agents:
            for tid in a.tools:
                if tid not in tool_ids:
                    errors.append(f"Agent '{a.id}' references unknown tool '{tid}'")
            for mid in a.mcp_servers:
                if mid not in mcp_ids:
                    errors.append(f"Agent '{a.id}' references unknown MCP server '{mid}'")
            for kid in a.knowledge:
                if kid not in kb_ids:
                    errors.append(f"Agent '{a.id}' references unknown knowledge base '{kid}'")

        for t in self.teams:
            for member in t.members:
                if member not in agent_ids and member not in team_ids:
                    errors.append(f"Team '{t.id}' has unknown member '{member}'")

        for c in self.councils:
            for member in c.members:
                if member not in agent_ids:
                    errors.append(f"Council '{c.id}' has unknown member '{member}'")

        for s in self.steps:
            if s.kind == "agent" and s.agent not in agent_ids:
                errors.append(f"Step '{s.id}' references unknown agent '{s.agent}'")
            if s.kind == "team" and s.team not in team_ids:
                errors.append(f"Step '{s.id}' references unknown team '{s.team}'")
            if s.kind == "council" and s.council not in council_ids:
                errors.append(f"Step '{s.id}' references unknown council '{s.council}'")

        if errors:
            raise ValueError("Workflow validation failed:\n- " + "\n- ".join(errors))
        return self
