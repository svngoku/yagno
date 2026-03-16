"""Example: run a Yagno workflow programmatically."""

import asyncio

from dotenv import load_dotenv

from yagno.runtime import load_workflow


async def main():
    load_dotenv()
    wf = load_workflow("specs/research_team_with_sandbox.yaml")

    if wf.spec.background and wf.spec.agentos_enabled:
        from agno.os import AgentOS

        app = AgentOS(
            name=wf.spec.name,
            workflows=[wf.workflow],
        )
        app.serve()
    else:
        result = await wf.arun({"topic": "Build a YAML-first agent system"})
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
