"""Compiler: turns a WorkflowSpec into live Agno objects.

config.py (YAML) → compiler.py → Agent, Team, Workflow, Step, Knowledge, etc.

All import paths and constructor signatures are verified against Agno >=2.5.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agno.agent import Agent
from agno.team.team import Team
from agno.team.mode import TeamMode
from agno.workflow.workflow import Workflow
from agno.workflow.step import Step

from yagno.config import (
    AgentSpec,
    CouncilSpec,
    DbSpec,
    KnowledgeSpec,
    MCPSpec,
    TeamSpec,
    ToolSpec,
    WorkflowSpec,
)
from yagno.registry import import_from_string, load_prompt_file

logger = logging.getLogger("yagno.compiler")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _build_db(spec: WorkflowSpec) -> Any | None:
    """Build a database backend for persistence."""
    if not spec.persistent or spec.db is None:
        return None

    db_spec: DbSpec = spec.db
    url = db_spec.url or os.getenv("DATABASE_URL")

    if db_spec.provider == "postgres":
        from agno.db.postgres import PostgresDb

        # Normalise common postgres URL schemes to the psycopg (v3) dialect
        if url and (url.startswith("postgresql://") or url.startswith("postgres://")):
            url = "postgresql+psycopg://" + url.split("://", 1)[1]

        db = PostgresDb(
            db_url=url,
            session_table=f"{db_spec.table_prefix}_sessions",
        )
        # Eagerly create tables so they exist before the workflow runs.
        # _create_all_tables is Agno-internal; guard against future removal.
        if hasattr(db, "_create_all_tables"):
            db._create_all_tables()
        else:
            logger.warning(
                "PostgresDb no longer exposes _create_all_tables(); "
                "tables may need to be created manually."
            )
        return db

    from agno.db.sqlite import SqliteDb

    return SqliteDb(
        db_file=url or "yagno.db",
        session_table=f"{db_spec.table_prefix}_sessions",
    )


# ---------------------------------------------------------------------------
# Knowledge / RAG
# ---------------------------------------------------------------------------

def _build_knowledge(spec: KnowledgeSpec) -> Any:
    """Build a Knowledge object with its vector DB."""
    from agno.knowledge import Knowledge

    vector_db = _build_vector_db(spec)
    return Knowledge(
        name=spec.id,
        vector_db=vector_db,
    )


def _build_vector_db(spec: KnowledgeSpec) -> Any:
    """Build the vector database backend for a knowledge base."""
    if spec.vector_db == "pgvector":
        from agno.vectordb.pgvector import PgVector
        from agno.embedder.openai import OpenAIEmbedder

        return PgVector(
            db_url=spec.db_url or os.getenv("DATABASE_URL", ""),
            table_name=spec.table_name or f"kb_{spec.id}",
            embedder=OpenAIEmbedder(id=spec.embedder_model),
        )

    if spec.vector_db == "lancedb":
        from agno.vectordb.lancedb import LanceDb
        from agno.embedder.openai import OpenAIEmbedder

        return LanceDb(
            uri=spec.db_url or "tmp/lancedb",
            table_name=spec.table_name or f"kb_{spec.id}",
            embedder=OpenAIEmbedder(id=spec.embedder_model),
        )

    raise ValueError(f"Unsupported vector_db: {spec.vector_db}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _build_tool_registry(specs: list[ToolSpec]) -> dict[str, Any]:
    """Build tool objects from ToolSpecs."""
    registry: dict[str, Any] = {}
    for t in specs:
        if t.kind == "callable":
            if t.entrypoint is None:
                raise ValueError(f"Tool '{t.id}' is callable but has no entrypoint")
            registry[t.id] = import_from_string(t.entrypoint)
        elif t.kind == "sandbox":
            try:
                registry[t.id] = _build_daytona_tools()
            except (ImportError, ValueError) as exc:
                logger.warning("Sandbox tool '%s' skipped: %s", t.id, exc)
        elif t.kind == "tavily":
            try:
                registry[t.id] = _build_tavily_tools(t)
            except (ImportError, ValueError) as exc:
                logger.warning("Tavily tool '%s' skipped: %s", t.id, exc)
        else:
            # Pydantic Literal should prevent this at parse time, but guard
            # against future schema additions that forget to add a handler.
            raise ValueError(
                f"Tool '{t.id}' has unsupported kind='{t.kind}'. "
                f"Supported kinds: callable, sandbox, tavily."
            )
    return registry


def _build_tavily_tools(spec: ToolSpec) -> Any:
    """Build a TavilyTools instance from spec."""
    from agno.tools.tavily import TavilyTools

    return TavilyTools(
        api_key=spec.api_key,
        search_depth=spec.search_depth,
        max_tokens=spec.max_tokens,
        include_answer=spec.include_answer,
        include_images=spec.include_images,
    )


def _build_daytona_tools() -> Any:
    """Build a DaytonaTools sandbox instance."""
    from agno.tools.daytona import DaytonaTools

    return DaytonaTools()


def _build_mcp_tools(spec: MCPSpec) -> Any:
    """Build an MCPTools instance from spec."""
    from agno.tools.mcp import MCPTools

    kwargs: dict[str, Any] = {"transport": spec.transport}
    if spec.url:
        kwargs["url"] = spec.url
    if spec.command:
        kwargs["command"] = spec.command
    if spec.env:
        kwargs["env"] = spec.env
    return MCPTools(**kwargs)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _build_model(model_str: str) -> Any:
    """Resolve a model string to an Agno model object.

    Supports formats:
      - "openrouter:openai/gpt-4.1-mini"  → OpenRouter
      - "openai:gpt-4o"                   → OpenAIChat
      - "anthropic:claude-sonnet-4-5-20250929"   → Claude
      - "google:gemini-2.0-flash"          → Gemini
    """
    if ":" in model_str:
        provider, model_id = model_str.split(":", 1)
    else:
        provider, model_id = "openrouter", model_str

    if provider == "openrouter":
        from agno.models.openrouter import OpenRouter
        return OpenRouter(id=model_id)

    if provider == "openai":
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id=model_id)

    if provider == "anthropic":
        from agno.models.anthropic import Claude
        return Claude(id=model_id)

    if provider == "google":
        from agno.models.google import Gemini
        return Gemini(id=model_id)

    # Fallback: treat whole string as OpenRouter model id
    logger.warning(
        "Unknown model provider '%s' in '%s' — falling back to OpenRouter.",
        provider,
        model_str,
    )
    from agno.models.openrouter import OpenRouter as _OR
    return _OR(id=model_str)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def _build_agent(
    spec: AgentSpec,
    tool_registry: dict[str, Any],
    mcp_registry: dict[str, MCPSpec],
    knowledge_dict: dict[str, Any],
    db: Any | None = None,
) -> Agent:
    """Compile an AgentSpec into a live Agent."""
    instructions = list(spec.instructions) + load_prompt_file(spec.prompt_file)

    # Resolve tools
    tools: list[Any] = []
    for tool_id in spec.tools:
        if tool_id in tool_registry:
            tools.append(tool_registry[tool_id])
        else:
            logger.warning("Tool '%s' not found in registry for agent '%s'", tool_id, spec.id)

    # Resolve MCP servers referenced by agent
    for mcp_id in spec.mcp_servers:
        if mcp_id in mcp_registry:
            tools.append(_build_mcp_tools(mcp_registry[mcp_id]))
        else:
            logger.warning("MCP server '%s' not found for agent '%s'", mcp_id, spec.id)

    # Resolve knowledge — Agno Agent accepts a single Knowledge object.
    # Warn if multiple are specified, since only the first is used.
    knowledge = None
    if spec.knowledge:
        if len(spec.knowledge) > 1:
            logger.warning(
                "Agent '%s' lists %d knowledge bases %s; only the first ('%s') "
                "will be used. Agno Agent accepts a single Knowledge object.",
                spec.id,
                len(spec.knowledge),
                spec.knowledge,
                spec.knowledge[0],
            )
        for kid in spec.knowledge:
            if kid in knowledge_dict:
                knowledge = knowledge_dict[kid]
                break  # Agent takes a single knowledge object
            else:
                logger.warning(
                    "Knowledge '%s' not found for agent '%s'", kid, spec.id
                )

    model = _build_model(spec.model)

    return Agent(
        name=spec.name or spec.id,
        model=model,
        role=spec.role,
        description=spec.description,
        instructions=instructions or None,
        tools=tools or None,
        knowledge=knowledge,
        db=db,
        output_schema=spec.output_schema,
        markdown=spec.markdown,
        retries=spec.retries,
        tool_call_limit=spec.tool_call_limit,
        add_history_to_context=spec.add_history_to_context,
        add_session_state_to_context=spec.add_session_state_to_context,
        add_datetime_to_context=spec.add_datetime_to_context,
        reasoning=spec.reasoning,
        stream=spec.stream,
        debug_mode=spec.debug_mode,
        store_events=True,
    )


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

def _build_teams(
    specs: list[TeamSpec],
    agents: dict[str, Agent],
) -> dict[str, Team]:
    """Build teams, resolving member references to agents or other teams.

    Uses a two-pass approach so that a team can reference another team
    regardless of declaration order in the YAML spec.
    """
    built: dict[str, Team] = {}

    # ── Pass 1: build all teams with only agent members ──────────
    deferred_members: dict[str, list[str]] = {}  # team_id → unresolved member ids

    for t in specs:
        members: list[Agent | Team] = []
        unresolved: list[str] = []
        for mid in t.members:
            if mid in agents:
                members.append(agents[mid])
            else:
                # Could be another team — defer resolution to pass 2
                unresolved.append(mid)

        instructions = list(t.instructions) + load_prompt_file(t.prompt_file)

        team_kwargs: dict[str, Any] = {
            "name": t.name or t.id,
            "role": t.role,
            "mode": TeamMode[t.mode],
            "members": members,
            "markdown": t.markdown,
            "max_iterations": t.max_iterations,
        }
        if instructions:
            team_kwargs["instructions"] = instructions
        if t.model:
            team_kwargs["model"] = _build_model(t.model)

        built[t.id] = Team(**team_kwargs)
        if unresolved:
            deferred_members[t.id] = unresolved

    # ── Pass 2: resolve team-to-team member references ───────────
    for team_id, unresolved in deferred_members.items():
        team = built[team_id]
        for mid in unresolved:
            if mid in built:
                team.members.append(built[mid])
            else:
                logger.warning(
                    "Team member '%s' not found (not an agent or team) "
                    "for team '%s'",
                    mid,
                    team_id,
                )

    return built


# ---------------------------------------------------------------------------
# Councils (debate & consensus)
# ---------------------------------------------------------------------------

def _build_councils(
    specs: list[CouncilSpec],
    agents: dict[str, Agent],
) -> dict[str, Team]:
    """Build council Teams using broadcast mode for multi-round debate."""
    built: dict[str, Team] = {}

    for c in specs:
        members: list[Agent] = []
        for mid in c.members:
            if mid in agents:
                members.append(agents[mid])
            else:
                logger.warning("Council member '%s' not found for council '%s'", mid, c.id)

        instructions = list(c.synthesizer_instructions) + load_prompt_file(c.synthesizer_prompt_file)
        if not instructions:
            instructions = [
                "You are the council synthesizer. After reviewing all member perspectives "
                "and their debate across rounds, produce a balanced consensus that "
                "acknowledges key agreements, tensions, and trade-offs.",
            ]

        team_kwargs: dict[str, Any] = {
            "name": c.name or c.id,
            "mode": TeamMode.broadcast,
            "members": members,
            "share_member_interactions": True,
            "add_team_history_to_members": True,
            "max_iterations": c.debate_rounds,
            "markdown": c.markdown,
            "instructions": instructions,
        }
        if c.synthesizer_model:
            team_kwargs["model"] = _build_model(c.synthesizer_model)
        if c.num_history_runs is not None:
            team_kwargs["num_history_runs"] = c.num_history_runs

        built[c.id] = Team(**team_kwargs)

    return built


# ---------------------------------------------------------------------------
# Workflow Steps
# ---------------------------------------------------------------------------

def _build_steps(
    specs: list,
    agents: dict[str, Agent],
    teams: dict[str, Team],
) -> list[Step]:
    """Compile StepSpecs into Agno Step objects."""
    steps: list[Step] = []

    for s in specs:
        if s.kind == "agent" and s.agent:
            if s.agent not in agents:
                raise ValueError(f"Step '{s.id}' references unknown agent '{s.agent}'")
            steps.append(Step(
                name=s.name or s.id,
                agent=agents[s.agent],
                description=s.description,
                max_retries=s.max_retries,
            ))

        elif s.kind == "team" and s.team:
            if s.team not in teams:
                raise ValueError(f"Step '{s.id}' references unknown team '{s.team}'")
            steps.append(Step(
                name=s.name or s.id,
                team=teams[s.team],
                description=s.description,
                max_retries=s.max_retries,
            ))

        elif s.kind == "council" and s.council:
            if s.council not in teams:
                raise ValueError(f"Step '{s.id}' references unknown council '{s.council}'")
            steps.append(Step(
                name=s.name or s.id,
                team=teams[s.council],
                description=s.description,
                max_retries=s.max_retries,
            ))

        elif s.kind == "function" and s.executor:
            executor_fn = import_from_string(s.executor)
            steps.append(Step(
                name=s.name or s.id,
                executor=executor_fn,
                description=s.description,
                max_retries=s.max_retries,
            ))

        else:
            raise ValueError(
                f"Step '{s.id}' has kind='{s.kind}' but is missing the required field "
                f"('agent', 'team', 'council', or 'executor')"
            )

    return steps


# ---------------------------------------------------------------------------
# Top-level: build the full Workflow
# ---------------------------------------------------------------------------

def compile_workflow(spec: WorkflowSpec) -> Workflow:
    """Compile a full WorkflowSpec into a live Agno Workflow."""
    db = _build_db(spec)

    # Tools
    tool_registry = _build_tool_registry(spec.tools)

    # MCP servers (top-level, referenceable by agents)
    mcp_registry = {m.id: m for m in spec.mcp_servers}

    # Knowledge bases
    knowledge_dict: dict[str, Any] = {}
    for k in spec.knowledge_bases:
        try:
            knowledge_dict[k.id] = _build_knowledge(k)
        except Exception as exc:
            logger.warning("Failed to build knowledge '%s': %s", k.id, exc)

    # Agents
    agents = {
        a.id: _build_agent(a, tool_registry, mcp_registry, knowledge_dict, db)
        for a in spec.agents
    }

    # Teams
    teams = _build_teams(spec.teams, agents)

    # Councils (built as Teams, merged into the teams dict)
    councils = _build_councils(spec.councils, agents)
    all_teams = {**teams, **councils}

    # Steps
    steps = _build_steps(spec.steps, agents, all_teams)

    workflow = Workflow(
        id=spec.id,
        name=spec.name,
        description=spec.description,
        steps=steps,
        db=db,
        session_id=spec.session_id,
        session_state=spec.session_state or None,
        store_events=spec.store_events,
        stream_events=spec.stream_events,
        debug_mode=spec.debug_mode,
        stream_executor_events=True,
    )

    return workflow
