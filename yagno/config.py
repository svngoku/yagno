"""Pydantic models that define the YAML schema for Yagno specs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Named constants ────────────────────────────────────────────────────
DEFAULT_TOOL_CALL_LIMIT = 15
DEFAULT_CONTEXT_SUMMARY_CHARS = 600


class ToolSpec(BaseModel):
    """A tool available to agents."""

    id: str
    kind: Literal["callable", "sandbox", "tavily"] = "callable"
    entrypoint: str | None = None  # dotted path for callable
    provider: Literal["daytona"] | None = None  # for sandbox kind
    # Tavily-specific options
    api_key: str | None = None  # defaults to TAVILY_API_KEY env var
    search_depth: Literal["basic", "advanced"] = "advanced"
    max_tokens: int = 6000
    include_answer: bool = True
    include_images: bool = False


class MCPSpec(BaseModel):
    """An MCP (Model Context Protocol) server providing external tools."""

    id: str
    url: str | None = None
    command: str | None = None
    transport: Literal["streamable-http", "sse", "stdio"] = "stdio"
    env: dict[str, str] = Field(default_factory=dict)


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
