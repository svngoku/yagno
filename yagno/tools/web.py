"""Example tool: web search stub.

Replace the body with a real search API (DuckDuckGo, Tavily, Brave, etc.)
"""

from agno.tools import tool


@tool
def web_search(query: str) -> str:
    """Search the web for information on a given query."""
    # TODO: Replace with a real search API call
    return f"[web_search] Results for '{query}': No real API configured. Replace this stub."
