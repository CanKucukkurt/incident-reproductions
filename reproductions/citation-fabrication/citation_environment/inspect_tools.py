"""Optional Inspect integration; callers provide their own task and prompt."""

import json

from inspect_ai.tool import ToolError, bash, tool
from inspect_ai.util import sandbox

from .config import compose_file


def sandbox_config():
    """Pass this value as Task(sandbox=...). Each sample gets its own environment."""
    return ("docker", compose_file())


async def _browser(request):
    result = await sandbox("browser").exec(
        ["python", "-m", "citation_environment.browser"],
        input=json.dumps(request), timeout=120,
    )
    if not result.success:
        raise ToolError(result.stderr)
    return result.stdout


@tool
def browser_open():
    async def execute(url: str) -> str:
        """Read a web page and return its text, links, and source reference.

        Args:
            url: HTTP or HTTPS URL to visit.
        """
        return await _browser({"operation": "open", "url": url})
    return execute


@tool
def browser_search():
    async def execute(query: str) -> str:
        """Search the web and return a results page with links.

        Args:
            query: Search query.
        """
        return await _browser({"operation": "search", "query": query})
    return execute


def environment_tools():
    """Tool definitions only: no prompt, solver, scoring, or model invocation."""
    return [bash(), browser_open(), browser_search()]
