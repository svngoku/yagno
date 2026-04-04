# Yagno

Minimal YAML. Production-grade long-running agents & multi-agent teams.

Yagno is a thin, declarative compiler that turns simple YAML files into powerful, persistent [Agno](https://github.com/agno-agi/agno) agents, teams, and workflows.

You get everything Agno offers — structured outputs, RAG, MCP tools, Daytona sandboxes, Postgres persistence, AgentOS background execution, resumable sessions — with almost zero Python code.

## Features

- **Zero-boilerplate YAML** for agents, teams, workflows, tools & sandboxes
- **Native multi-agent teams** (coordinate, route, broadcast, tasks + nesting)
- **Councils** — multi-round debate & consensus with automatic synthesis
- **Mission Control** — long-running, multi-milestone execution with validation
- **Secure code execution** via persistent Daytona sandboxes
- **Full persistence** (Postgres/SQLite) — agents survive restarts
- **Agentic RAG / Knowledge bases** (PgVector, LanceDB)
- **MCP tools** (Model Context Protocol) for external tool servers
- **Tavily search** built-in as a first-class tool kind
- **Finance tools** — stock quotes, crypto prices, SEC filings, financial news (no API keys)
- **Structured outputs**, automatic retries, tool call limits
- **Built-in observability** — per-step token/cost/tool metrics, model request tracking, debug mode
- **Rich CLI output** — colored panels, tool call logs, streaming content, metrics summary table
- **Async-first** with streaming and session state
- **Background deployment** via AgentOS for 24/7 agents
- **Reference resolution** (`${input.topic}`, `${session_state.X}`, `${env.DB_URL}`)
- **Robust error handling** — descriptive errors for bad YAML, schema mismatches, and missing files
- **Security guardrails by default** — import/env allowlists, path checks, MCP safety filters

## Quick Start

### One-line Install

```bash
curl -fsSL https://raw.githubusercontent.com/svngoku/yagno/main/install.sh | bash
```

This installs `uv` (if needed), ensures Python >= 3.11, and installs `yagno` as an isolated CLI tool. No pip/uv required on the target machine.

Pin a specific version:

```bash
YAGNO_VERSION=1.2.0 curl -fsSL https://raw.githubusercontent.com/svngoku/yagno/main/install.sh | bash
```

### Or install with pip/uv

```bash
pip install yagno

# With optional extras
pip install yagno[postgres]    # PostgreSQL persistence
pip install yagno[tavily]      # Tavily web search
pip install yagno[finance]     # Finance tools (yfinance)
pip install yagno[sandbox]     # Daytona sandbox support
pip install yagno[serve]       # FastAPI / AgentOS serving
pip install yagno[all]         # Everything
```

### Scaffold a new project

```bash
yagno init my-project
```

This creates:

```
my-project/
├── pyproject.toml
├── .env.example
├── .gitignore
├── specs/
│   └── my_project.yaml
└── prompts/
    └── assistant.md
```

Options:

```bash
yagno init my-project --model openai:gpt-4o    # different model
yagno init my-project --tool none               # no search tool
```

### Create a spec

`specs/simple_researcher.yaml`:

```yaml
id: simple_researcher
name: Simple Research Agent
persistent: false

agents:
  - id: researcher
    model: openrouter:openai/gpt-4.1-mini
    tools: [tavily_search]
    prompt_file: prompts/researcher.md
    markdown: true

tools:
  - id: tavily_search
    kind: tavily
    search_depth: advanced

steps:
  - id: research
    kind: agent
    agent: researcher
```

### Run it

```bash
# CLI
yagno run specs/simple_researcher.yaml --input '{"topic": "AI agents"}'

# Validate without running
yagno validate specs/simple_researcher.yaml

# Debug mode — shows all events, model requests, unhandled events
yagno run specs/simple_researcher.yaml -i '"AI agents"' --debug
```

Or from Python:

```python
import asyncio
from yagno import load_workflow

async def main():
    wf = load_workflow("specs/simple_researcher.yaml")
    result = await wf.arun({"topic": "AI agents in 2026"})
    print(result)

asyncio.run(main())
```

## Observability

Yagno provides built-in observability powered by Agno's event streaming. Every run shows:

**Live event stream** — as your workflow executes, you see each phase in real time:

```
Step 1: research
  model: openai/gpt-4.1-mini
  tool tavily_search {"query": "AI agents 2026"}
  tool tavily_search done 2.1s
       [Search results preview...]
  1,234 tok | ttft=0.42s
  tool tavily_search {"query": "multi-agent frameworks"}
  tool tavily_search done 1.8s
  2,891 tok | ttft=0.38s
```

**Metrics summary table** — after completion, a per-step breakdown:

```
           Run Summary
┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━┓
┃ Step     ┃ Tokens ┃    In ┃  Out ┃ Tools ┃   Cost ┃ Time ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━┩
│ research │  4,125 │ 3,200 │  925 │     2 │ $0.003 │ 8.2s │
├──────────┼────────┼───────┼──────┼───────┼────────┼──────┤
│ Total    │  4,125 │ 3,200 │  925 │     2 │ $0.003 │ 8.2s │
└──────────┴────────┴───────┴──────┴───────┴────────┴──────┘
```

**Debug mode** (`--debug`) additionally logs unhandled events and marks debug as active in the header.

Events tracked:
- `StepStarted` / `StepCompleted` / `StepError` — workflow step lifecycle
- `ModelRequestStarted` / `ModelRequestCompleted` — LLM calls with token counts and TTFT
- `ToolCallStarted` / `ToolCallCompleted` / `ToolCallError` — tool invocations with args, results, duration
- `ReasoningStep` — chain-of-thought for reasoning models
- `RunContentEvent` — streaming content chunks
- `RunCompleted` — final metrics (cost, duration, token totals)
- `WorkflowError` — workflow-level failures

### YAML config for observability

```yaml
# Per-agent
agents:
  - id: researcher
    debug_mode: true    # verbose agent-level logging

# Workflow-level
store_events: true      # persist events to DB (default: true)
stream_events: true     # stream events during execution (default: true)
debug_mode: true        # enable debug across all agents
```

## Tavily Search

Built-in web search via [Tavily](https://tavily.com/). Requires the `tavily` extra (`pip install yagno[tavily]`).

```yaml
tools:
  - id: tavily_search
    kind: tavily
    search_depth: advanced    # or "basic"
    max_tokens: 6000
    include_answer: true
    include_images: false
    api_key_env: MY_TAVILY_KEY   # optional; defaults to TAVILY_API_KEY
```

## Finance Tools

Real-time market data tools — no API keys required. Requires the `finance` extra (`pip install yagno[finance]`).

```yaml
tools:
  - id: stock_quote
    kind: callable
    entrypoint: yagno.tools.finance.get_stock_quote

  - id: financial_news
    kind: callable
    entrypoint: yagno.tools.finance.get_financial_news

  - id: sec_filing
    kind: callable
    entrypoint: yagno.tools.finance.get_sec_filing

  - id: crypto_price
    kind: callable
    entrypoint: yagno.tools.finance.get_crypto_price
```

Available tools:
- **`get_stock_quote`** — live price, daily change, 52W range, market cap, P/E, dividend yield (Yahoo Finance)
- **`get_financial_news`** — latest articles by ticker or topic (Yahoo Finance)
- **`get_sec_filing`** — SEC EDGAR filing metadata with direct document links (SEC REST API)
- **`get_crypto_price`** — USD price, 24h change, market cap, volume (CoinGecko)

## Multi-Agent Teams

```yaml
id: research_team
name: Research & Validation Team

agents:
  - id: researcher
    model: openrouter:openai/gpt-4.1-mini
    tools: [tavily_search]

  - id: coder
    model: openrouter:openai/gpt-4.1-mini
    tools: [sandbox]
    instructions:
      - "Validate ideas with working code in the Daytona sandbox."

teams:
  - id: core_team
    mode: coordinate
    model: openrouter:openai/gpt-4.1-mini
    members: [researcher, coder]
    instructions:
      - "Research first, then validate code in the sandbox."

tools:
  - id: tavily_search
    kind: tavily
  - id: sandbox
    kind: sandbox
    provider: daytona

steps:
  - id: main
    kind: team
    team: core_team
```

Team modes:
- `coordinate` — leader decomposes, delegates, and synthesizes
- `route` — routes to a single specialist
- `broadcast` — sends to all members in parallel
- `tasks` — autonomous task-based execution with shared task list

### Nested teams

Teams can reference other teams as members. Declaration order doesn't matter — Yagno resolves references in two passes:

```yaml
teams:
  - id: super_team
    mode: coordinate
    members: [research_team, review_team]  # references teams defined below

  - id: research_team
    mode: coordinate
    members: [researcher, analyst]

  - id: review_team
    mode: broadcast
    members: [reviewer, editor]
```

## Councils (Debate & Consensus)

Multiple agents with different perspectives debate a question through multiple rounds, then a synthesizer produces a balanced consensus.

```yaml
agents:
  - id: utilitarian
    model: openrouter:anthropic/claude-sonnet-4-5
    role: Utilitarian ethicist
    instructions: ["Argue from a utilitarian perspective."]

  - id: deontologist
    model: openrouter:anthropic/claude-sonnet-4-5
    role: Deontological ethicist
    instructions: ["Argue from a deontological perspective."]

  - id: virtue_ethicist
    model: openrouter:anthropic/claude-sonnet-4-5
    role: Virtue ethicist
    instructions: ["Argue from a virtue ethics perspective."]

councils:
  - id: ethics_council
    name: AI Ethics Council
    members: [utilitarian, deontologist, virtue_ethicist]
    debate_rounds: 2
    synthesizer_model: openrouter:anthropic/claude-sonnet-4-5
    synthesizer_instructions:
      - "Synthesize all perspectives into a balanced consensus."

steps:
  - id: deliberation
    kind: council
    council: ethics_council
```

Council config:
- `debate_rounds` — number of broadcast iterations (default: 2)
- `synthesizer_model` — model for the team leader that produces consensus
- `synthesizer_instructions` — instructions for the synthesizer (defaults provided)
- `synthesizer_prompt_file` — load synthesizer instructions from a file

## Mission Control

For long-running, multi-feature goals that span multiple milestones with validation checkpoints. Each feature executes in a fresh agent context for isolation. The orchestrator carries a rolling summary between milestones.

```bash
yagno mission run specs/finance_mission.yaml
yagno mission validate specs/finance_mission.yaml
```

### Mission YAML structure

```yaml
id: finance_mission
name: Finance Market Intelligence Mission
goal: >
  Produce a structured market intelligence brief covering
  equities, crypto, and regulatory filings.

# ── Orchestrator (carries context between milestones) ─────
orchestrator:
  model: openrouter:openai/gpt-4.1-mini
  carry_context: true
  context_summary_chars: 1200   # max chars per feature in summary (default: 600)
  instructions:
    - "Carry forward key numerical facts and conclusions."

# ── Workers (execute individual features) ─────────────────
workers:
  market_analyst:
    model: openrouter:openai/gpt-4.1-mini
    tools: [stock_quote, financial_news]
    instructions:
      - "Retrieve live data and produce structured markdown reports."

  brief_writer:
    model: openrouter:openai/gpt-4.1-mini
    instructions:
      - "Synthesise research into a concise investment brief."

# ── Validators (check milestone quality) ──────────────────
validators:
  data_completeness:
    model: openrouter:openai/gpt-4.1-mini
    criteria:
      - Each equity section contains a current price and 24h change.
      - No section is empty or contains placeholder text.

# ── Features (individual units of work) ───────────────────
features:
  - id: equity_research
    name: Equity Research
    description: >
      Retrieve live market data for AAPL, NVDA, and MSFT.
    worker: market_analyst
    success_criteria:
      - Current price and daily change for all tickers.
    max_retries: 1

  - id: investment_brief
    name: Investment Brief
    description: >
      Write a structured investment brief from the research.
    worker: brief_writer
    max_retries: 1

# ── Milestones (ordered checkpoints with validation) ──────
milestones:
  - id: data_gathering
    name: Data Gathering
    features: [equity_research]
    validator: data_completeness
    max_validation_retries: 2

  - id: synthesis
    name: Brief Synthesis
    features: [investment_brief]
```

### Mission execution model

```
Mission
  └─ Milestone 1: Data Gathering
  │    ├─ Feature: equity_research  → fresh agent, tools, retries
  │    ├─ Feature: crypto_research  → fresh agent, tools, retries
  │    └─ Validator: data_completeness
  │         ├─ PASS → carry context to next milestone
  │         └─ FAIL → re-run ALL features with feedback (up to max_validation_retries)
  └─ Milestone 2: Brief Synthesis
       ├─ Feature: investment_brief  → receives orchestrator summary as context
       └─ Validator: brief_quality
```

Key design choices:
- **Isolated feature execution** — each feature runs in a fresh Agno Agent with no shared state
- **Validation retries re-run all features** — a feature can succeed but still produce output that fails quality validation; the validator feedback is injected into the retry context
- **Configurable context carry-over** — `context_summary_chars` controls how much of each feature's output is included in the orchestrator summary passed to the next milestone

### Mission config reference

| Key | Description | Default |
|-----|-------------|---------|
| `goal` | Overall mission objective | required |
| `orchestrator.model` | Model for inter-milestone summarization | `openrouter:openai/gpt-4.1-mini` |
| `orchestrator.carry_context` | Summarize and pass context between milestones | `true` |
| `orchestrator.context_summary_chars` | Max chars per feature in context summary | `600` |
| `workers.<id>.model` | Model for the worker | `openrouter:openai/gpt-4.1-mini` |
| `workers.<id>.tools` | Tool IDs available to the worker | `[]` |
| `workers.<id>.tool_call_limit` | Max tool calls per feature execution | `15` |
| `validators.<id>.criteria` | Success criteria (injected into validator prompt) | `[]` |
| `features[].worker` | Worker ID (falls back to first defined worker) | `null` |
| `features[].success_criteria` | Criteria shown to the worker | `[]` |
| `features[].max_retries` | Feature-level retries on exception | `1` |
| `milestones[].validator` | Validator ID for this checkpoint | `null` |
| `milestones[].max_validation_retries` | Re-run cycles on validation failure | `2` |

## Persistence & Background

```yaml
persistent: true
background: true
agentos_enabled: true

db:
  provider: postgres
  url: "${env:DATABASE_URL}"
```

```bash
# Run as a background AgentOS service (requires: pip install yagno[serve])
yagno run specs/my_workflow.yaml --background
```

Sessions survive restarts and can be resumed by `session_id`.

## MCP Tools

```yaml
mcp_servers:
  - id: git_tools
    command: "uvx mcp-server-git"
    transport: stdio

agents:
  - id: dev_agent
    model: openrouter:openai/gpt-4.1-mini
    mcp_servers: [git_tools]
```

## Knowledge / RAG

```yaml
knowledge_bases:
  - id: docs_kb
    vector_db: pgvector
    db_url: "${env:DATABASE_URL}"
    table_name: kb_docs
    embedder_model: text-embedding-3-small

agents:
  - id: researcher
    model: openrouter:openai/gpt-4.1-mini
    knowledge: [docs_kb]
```

> **Note:** Agno agents accept a single knowledge base. If multiple are listed, only the first is used (a warning is logged).

## Security

### Security defaults

Yagno now enforces practical guardrails by default:

- **Callable import allowlist**: `callable` tool entrypoints are limited to `yagno` and `agno` packages unless extended.
- **Env expression allowlist**: `${env:...}` references are validated against an allowlist.
- **Prompt path restrictions**: `prompt_file` must resolve inside the spec directory (or configured allowed base); path traversal is blocked.
- **MCP safety filters**: unsafe shell metacharacters in MCP server `command` values are blocked; dangerous env vars are rejected.
- **Tavily key handling**: inline `api_key` is deprecated/blocked; use `api_key_env` (or default `TAVILY_API_KEY`).

These defaults are designed to keep YAML specs portable while reducing high-risk misconfiguration.

### Tool entrypoint import allowlist

The `callable` tool kind imports Python functions by dotted `entrypoint`. By default, only these top-level packages are allowed:

- `yagno`
- `agno`

To allow additional packages, set `YAGNO_ALLOWED_PACKAGES` as a comma-separated list:

```bash
export YAGNO_ALLOWED_PACKAGES="yagno,agno,my_project_tools"
```

If an entrypoint resolves to a package outside the allowlist, Yagno fails fast during validation/compile with an import error.

### Env expression allowlist (`${env:*}`)

Environment references in expressions (for example `${env:DATABASE_URL}`) are checked against an allowlist.

To extend the allowed env var names, set:

```bash
export YAGNO_ALLOWED_ENV_VARS="DATABASE_URL,OPENAI_API_KEY,MY_CUSTOM_KEY"
```

Use this to explicitly permit project-specific secrets/config keys in specs.

### Prompt file path restrictions

`prompt_file` paths are normalized and must stay within the spec directory (or other configured allowed base). Path traversal attempts such as `../` escapes are blocked.

Recommended pattern:

```yaml
agents:
  - id: researcher
    prompt_file: prompts/researcher.md
```

### MCP command/env restrictions

For `mcp_servers` config, Yagno applies safety checks:

- blocks dangerous shell metacharacters in `command`
- blocks dangerous environment variable names/overrides in MCP env config

Use simple executable-style commands and explicit, non-sensitive env wiring.

## Error Handling

Yagno provides clear, contextual error messages at every stage:

| Stage | Error | Message example |
|-------|-------|-----------------|
| File loading | File not found | `Workflow spec not found: specs/missing.yaml` |
| YAML parsing | Syntax error | `Invalid YAML in 'specs/bad.yaml': ...` |
| Schema validation | Missing/invalid fields | `Schema validation error in 'specs/bad.yaml': ...` |
| Tool import | Bad entrypoint | `Module 'myapp.tools' has no attribute 'search' (from 'myapp.tools.search')` |
| Tool import | No dot in path | `Cannot import 'search': expected a dotted path like 'pkg.module.func'` |
| Prompt file | File missing or blocked path | `Prompt file 'prompts/missing.md' not found` / `Prompt path escapes allowed base` |
| Team resolution | Unknown member | `WARNING: Team member 'ghost' not found (not an agent or team) for team 'my_team'` |
| Knowledge | Multiple listed | `WARNING: Agent 'x' lists 3 knowledge bases; only the first ('kb1') will be used.` |

## Project Structure

```
yagno/
  specs/                  # YAML workflow & mission blueprints
  prompts/                # .md files for agent instructions
  yagno/
    __init__.py
    config.py             # Pydantic models (YAML schema)
    compiler.py           # Spec → live Agno objects
    runtime.py            # Load, compile, run workflows
    mission.py            # Mission Control runtime
    display.py            # Rich CLI output & metrics
    cli.py                # CLI entrypoint
    expressions.py        # ${...} reference resolver
    registry.py           # Dynamic imports & prompt loading
    tools/
      web.py              # Example web search tool (stub)
      finance.py          # Finance tools (Yahoo Finance, CoinGecko, SEC EDGAR)
  install.sh              # Remote installer
  pyproject.toml
  .env.example
```

## CLI Reference

```bash
yagno init <name>              # scaffold a new project
  --model / -m                 # default model (default: openrouter:openai/gpt-4.1-mini)
  --tool                       # tavily | none (default: tavily)

yagno run <spec>               # run a workflow
  --input / -i                 # JSON input data
  --session-id                 # resume a session
  --background                 # run as AgentOS service
  --async                      # use async execution
  --no-stream                  # disable streaming
  --no-guardrails              # disable runtime guardrails (not recommended)
  --debug                      # verbose event logging

yagno validate <spec>          # validate + schema + deep compile/setup checks

yagno mission run <spec>       # run a mission
  --no-guardrails              # disable runtime guardrails (not recommended)
  --debug                      # verbose logging

yagno mission validate <spec>  # validate a mission YAML spec
```

## Configuration Reference

### Workflow config

| Key | Description | Default |
|-----|-------------|---------|
| `persistent` | Enable DB-backed sessions | `true` |
| `background` | Run as long-lived AgentOS service | `false` |
| `agentos_enabled` | Expose via AgentOS control plane | `false` |
| `debug_mode` | Enable debug logging across agents | `false` |
| `store_events` | Persist events to DB | `true` |
| `stream_events` | Stream events during execution | `true` |
| `db.provider` | `postgres` or `sqlite` | `postgres` |
| `db.table_prefix` | Prefix for DB table names | `yagno` |

### Agent config

| Key | Description | Default |
|-----|-------------|---------|
| `model` | Model string (`provider:model_id`) | required |
| `role` | Agent role description | `null` |
| `instructions` | Inline instruction strings | `[]` |
| `prompt_file` | Path to a `.md` instructions file | `null` |
| `tools` | Tool IDs to attach | `[]` |
| `mcp_servers` | MCP server IDs to attach | `[]` |
| `knowledge` | Knowledge base IDs (first one used) | `[]` |
| `debug_mode` | Per-agent debug mode | `false` |
| `retries` | Max retries on failure | `3` |
| `tool_call_limit` | Max tool calls per run | `15` |
| `reasoning` | Enable chain-of-thought | `false` |
| `markdown` | Format output as markdown | `true` |

### Team config

| Key | Description | Default |
|-----|-------------|---------|
| `mode` | `coordinate` / `route` / `broadcast` / `tasks` | `coordinate` |
| `members` | Agent or team IDs (order-independent) | `[]` |
| `model` | Leader/coordinator model | `null` |
| `max_iterations` | Max delegation rounds | `10` |

### Tool config

| Key | Description | Default |
|-----|-------------|---------|
| `kind` | `callable`, `sandbox`, or `tavily` | `callable` |
| `entrypoint` | Dotted Python path (for `callable`) | `null` |
| `provider` | `daytona` (for `sandbox`) | `null` |
| `search_depth` | `basic` or `advanced` (for `tavily`) | `advanced` |

### Knowledge config

| Key | Description | Default |
|-----|-------------|---------|
| `vector_db` | `pgvector` or `lancedb` | `pgvector` |
| `embedder_model` | OpenAI embedding model | `text-embedding-3-small` |

Model string format: `provider:model_id` where provider is `openrouter`, `openai`, `anthropic`, or `google`. If no provider is specified, `openrouter` is assumed.

## Environment Variables

```env
OPENROUTER_API_KEY=...     # For OpenRouter models
OPENAI_API_KEY=...         # For OpenAI models or embeddings
ANTHROPIC_API_KEY=...      # For Anthropic models
TAVILY_API_KEY=...         # For Tavily search tool
DATABASE_URL=...           # For Postgres persistence/RAG
DAYTONA_API_KEY=...        # For sandbox code execution
```

## License

MIT
