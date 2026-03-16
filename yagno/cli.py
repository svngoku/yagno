"""CLI entrypoint for Yagno."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def _run(args: argparse.Namespace) -> None:
    """Run a workflow from a YAML spec."""
    from yagno.runtime import load_workflow

    spec_path = args.spec
    if not Path(spec_path).exists():
        print(f"Error: spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    rt = load_workflow(spec_path)

    # Parse input — Agno workflows expect a string message
    input_data: str = args.input or ""
    if isinstance(input_data, str):
        try:
            parsed = json.loads(input_data)
            if isinstance(parsed, dict):
                # Convert {"topic": "X"} → "X" or "topic: X\nother: Y"
                if len(parsed) == 1:
                    input_data = str(next(iter(parsed.values())))
                else:
                    input_data = "\n".join(f"{k}: {v}" for k, v in parsed.items())
        except json.JSONDecodeError:
            pass  # keep as string

    if args.background and rt.spec.agentos_enabled:
        _run_background(rt)
    elif args.asyncio:
        asyncio.run(rt.arun(input_data, session_id=args.session_id))
    else:
        rt.run_with_display(input_data, stream=not args.no_stream, debug=args.debug)


def _run_background(rt) -> None:
    """Start the workflow as a background AgentOS service."""
    from agno.os import AgentOS

    app = AgentOS(
        name=rt.spec.name,
        workflows=[rt.workflow],
        db=rt.workflow.db,
    )
    app.serve()


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="yagno",
        description="Yagno — Minimal YAML config for production Agno agents",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Run a workflow from a YAML spec")
    run_parser.add_argument("spec", help="Path to the YAML spec file")
    run_parser.add_argument(
        "--input", "-i",
        help='JSON input data, e.g. \'{"topic": "AI agents"}\'',
    )
    run_parser.add_argument(
        "--session-id",
        help="Resume a specific session by ID",
    )
    run_parser.add_argument(
        "--background",
        action="store_true",
        help="Run as a background AgentOS service",
    )
    run_parser.add_argument(
        "--async",
        dest="asyncio",
        action="store_true",
        help="Use async execution",
    )
    run_parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output",
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (verbose model/tool/event logging)",
    )

    # --- init ---
    init_parser = subparsers.add_parser("init", help="Scaffold a new Yagno project")
    init_parser.add_argument("name", nargs="?", default=None, help="Project directory name")
    init_parser.add_argument(
        "--model", "-m",
        default="openrouter:openai/gpt-4.1-mini",
        help="Default model for the agent (default: openrouter:openai/gpt-4.1-mini)",
    )
    init_parser.add_argument(
        "--tool",
        choices=["tavily", "none"],
        default="tavily",
        help="Search tool to include (default: tavily)",
    )

    # --- mission ---
    mission_parser = subparsers.add_parser(
        "mission", help="Mission Control: long-running multi-feature execution"
    )
    mission_sub = mission_parser.add_subparsers(dest="mission_command")

    mission_run_parser = mission_sub.add_parser(
        "run", help="Run a mission from a YAML spec"
    )
    mission_run_parser.add_argument("spec", help="Path to the mission YAML spec file")
    mission_run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (verbose logging)",
    )

    mission_validate_parser = mission_sub.add_parser(
        "validate", help="Validate a mission YAML spec without running it"
    )
    mission_validate_parser.add_argument("spec", help="Path to the mission YAML spec file")

    # --- validate ---
    validate_parser = subparsers.add_parser("validate", help="Validate a YAML spec")
    validate_parser.add_argument("spec", help="Path to the YAML spec file")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
        # Suppress noisy HTTP and framework logs
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("agno").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

    if args.command == "run":
        _run(args)
    elif args.command == "mission":
        _mission(args)
    elif args.command == "validate":
        _validate(args)
    elif args.command == "init":
        _init(args)
    else:
        parser.print_help()


def _mission(args: argparse.Namespace) -> None:
    """Dispatch mission sub-commands."""
    mission_command = getattr(args, "mission_command", None)

    if mission_command == "run":
        _mission_run(args)
    elif mission_command == "validate":
        _mission_validate(args)
    else:
        # No sub-command given — show mission help
        print(
            "Usage: yagno mission <command>\n"
            "\n"
            "Commands:\n"
            "  run       Run a mission from a YAML spec\n"
            "  validate  Validate a mission YAML spec\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _mission_run(args: argparse.Namespace) -> None:
    """Run a mission with live Rich display."""
    from yagno.mission import load_mission

    spec_path = args.spec
    if not Path(spec_path).exists():
        print(f"Error: mission spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    rt = load_mission(spec_path)
    result = rt.run_with_display(debug=getattr(args, "debug", False))

    if result.status != "completed":
        sys.exit(1)


def _mission_validate(args: argparse.Namespace) -> None:
    """Validate a mission YAML spec without running it."""
    import yaml
    from yagno.config import MissionSpec
    from yagno.display import console, OK, ERR
    from rich.panel import Panel
    from rich.table import Table

    spec_path = args.spec
    if not Path(spec_path).exists():
        print(f"Error: mission spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    raw = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
    try:
        spec = MissionSpec.model_validate(raw)
    except Exception as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)

    console.print(
        Panel(
            f"[bold {OK}]{spec.name}[/bold {OK}] ({spec.id})",
            title="Valid mission spec",
            border_style=OK,
        )
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Component")
    table.add_column("IDs")
    table.add_row("Features", ", ".join(f.id for f in spec.features) or "-")
    table.add_row("Milestones", ", ".join(m.id for m in spec.milestones) or "-")
    table.add_row("Workers", ", ".join(spec.workers) or "-")
    table.add_row("Validators", ", ".join(spec.validators) or "-")
    table.add_row("Tools", ", ".join(t.id for t in spec.tools) or "-")
    console.print(table)


def _validate(args: argparse.Namespace) -> None:
    """Validate a YAML spec without running it."""
    import yaml
    from yagno.config import WorkflowSpec

    spec_path = args.spec
    if not Path(spec_path).exists():
        print(f"Error: spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    raw = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
    try:
        spec = WorkflowSpec.model_validate(raw)
        from yagno.display import print_validation
        print_validation(spec)
    except Exception as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)


def _init(args: argparse.Namespace) -> None:
    """Scaffold a new Yagno project."""
    from yagno.display import console

    # Determine project name / directory
    name = args.name
    if not name:
        # Interactive prompt
        console.print("[bold]Create a new Yagno project[/bold]\n")
        name = input("Project name: ").strip()
        if not name:
            print("Error: project name is required", file=sys.stderr)
            sys.exit(1)

    project_dir = Path(name)
    if project_dir.exists() and any(project_dir.iterdir()):
        print(f"Error: directory '{name}' already exists and is not empty", file=sys.stderr)
        sys.exit(1)

    model = args.model

    # Build tool config
    tool_yaml = ""
    tool_ref = ""
    if args.tool == "tavily":
        tool_ref = "    tools: [tavily_search]"
        tool_yaml = (
            "tools:\n"
            "  - id: tavily_search\n"
            "    kind: tavily\n"
            "    search_depth: advanced\n"
        )

    # ── Scaffold files ──────────────────────────────────────────────
    slug = name.replace(" ", "_").replace("-", "_").lower()

    spec_content = (
        f"id: {slug}\n"
        f"name: {name}\n"
        f"description: A Yagno workflow.\n"
        f"persistent: false\n"
        f"\n"
        f"agents:\n"
        f"  - id: assistant\n"
        f"    name: Assistant\n"
        f"    model: {model}\n"
        f"{tool_ref}\n"
        f"    prompt_file: prompts/assistant.md\n"
        f"    markdown: true\n"
        f"\n"
        f"{tool_yaml}"
        f"steps:\n"
        f"  - id: main\n"
        f"    kind: agent\n"
        f"    agent: assistant\n"
    )

    prompt_content = (
        "You are a helpful assistant. Answer the user's question clearly and concisely.\n"
        "\n"
        "Use your tools when you need external information.\n"
    )

    env_content = (
        "# Model provider keys\n"
        "# OPENROUTER_API_KEY=\n"
        "# OPENAI_API_KEY=\n"
        "# ANTHROPIC_API_KEY=\n"
        "\n"
        "# Search tool\n"
        "# TAVILY_API_KEY=\n"
        "\n"
        "# Database (optional, for persistent workflows)\n"
        "# DATABASE_URL=postgresql://user:pass@host:5432/dbname\n"
    )

    gitignore_content = (
        "# Python\n"
        "__pycache__/\n"
        "*.py[oc]\n"
        "build/\n"
        "dist/\n"
        "*.egg-info\n"
        "\n"
        "# Virtual environments\n"
        ".venv\n"
        "\n"
        "# Environment\n"
        ".env\n"
        "\n"
        "# OS\n"
        ".DS_Store\n"
        "\n"
        "# Database\n"
        "*.db\n"
        "tmp/\n"
    )

    pyproject_content = (
        f'[project]\n'
        f'name = "{slug}"\n'
        f'version = "0.1.0"\n'
        f'description = "A Yagno agent project"\n'
        f'requires-python = ">=3.11"\n'
        f'dependencies = [\n'
        f'    "yagno>=1.1.0",\n'
        f']\n'
        f'\n'
        f'[build-system]\n'
        f'requires = ["hatchling"]\n'
        f'build-backend = "hatchling.build"\n'
    )

    # Create directories
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "specs").mkdir(exist_ok=True)
    (project_dir / "prompts").mkdir(exist_ok=True)

    # Write files
    files = {
        f"specs/{slug}.yaml": spec_content,
        "prompts/assistant.md": prompt_content,
        ".env.example": env_content,
        ".gitignore": gitignore_content,
        "pyproject.toml": pyproject_content,
    }

    for rel_path, content in files.items():
        fp = project_dir / rel_path
        fp.write_text(content, encoding="utf-8")

    # ── Summary ─────────────────────────────────────────────────────
    from rich.tree import Tree
    from rich.panel import Panel

    tree = Tree(f"[bold cyan]{name}/[/bold cyan]")
    tree.add("[dim]pyproject.toml[/dim]")
    tree.add("[dim].env.example[/dim]")
    tree.add("[dim].gitignore[/dim]")
    specs_node = tree.add("specs/")
    specs_node.add(f"{slug}.yaml")
    prompts_node = tree.add("prompts/")
    prompts_node.add("assistant.md")

    console.print(Panel(tree, title="[bold green]Project created[/bold green]", border_style="green", expand=False))
    console.print()
    console.print("Next steps:")
    console.print(f"  [cyan]cd {name}[/cyan]")
    console.print(f"  [cyan]cp .env.example .env[/cyan]     # add your API keys")
    console.print(f"  [cyan]pip install yagno[/cyan]         # or: uv pip install yagno")
    console.print(f"  [cyan]yagno run specs/{slug}.yaml -i '\"Hello\"'[/cyan]")
    console.print()


if __name__ == "__main__":
    main()
