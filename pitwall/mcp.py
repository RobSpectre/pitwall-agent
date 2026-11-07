"""MCP server: the same pit-wall tools over stdio, for agents that are not Hermes.

    pitwall-agent mcp

Any MCP client can then list and call ``pitwall_board``, ``pitwall_race`` and
the rest. The wrappers are generated from each tool's JSON schema, so a client
sees real parameters and types rather than a single ``arguments`` blob.

Session gating applies here too: at startup the server offers the tools that
belong to the session currently open in MultiViewer.
"""

from __future__ import annotations

from typing import Any

from . import feed
from .tools import specs

_ANNOTATIONS = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list[str]",
    "object": "dict",
}

INSTRUCTIONS = (
    "Live pit wall for MultiViewer. Read F1 live timing in compact form: "
    "session state, classification, flying laps, purple sectors, pole "
    "contention, race order, battles, strategy, and video players. "
    "Call pitwall_overview first, then the tool that answers the question."
)


def _params(schema: dict) -> tuple[list[str], list[str]]:
    parameters = schema.get("parameters") or {}
    properties = parameters.get("properties") or {}
    required = set(parameters.get("required") or ())
    signature: list[str] = []
    names: list[str] = []
    for name, spec in properties.items():
        annotation = _ANNOTATIONS.get(str(spec.get("type") or "string"), "str")
        names.append(name)
        signature.append(f"{name}: {annotation}" if name in required else f"{name}: {annotation} | None = None")
    return signature, names


def _wrapper(spec):
    """Build a function with the tool's real signature, delegating to the handler."""
    signature, names = _params(spec.schema)
    body = ", ".join(f'"{name}": {name}' for name in names)
    source = f"def {spec.name}({', '.join(signature)}):\n    return __handler__({{{body}}})\n"
    namespace: dict[str, Any] = {"__handler__": spec.handler}
    exec(compile(source, f"<pitwall:{spec.name}>", "exec"), namespace)  # noqa: S102 - generated from our own schema
    function = namespace[spec.name]
    function.__doc__ = spec.schema.get("description", "")
    return function


def build_server(name: str = "pitwall-agent"):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on the host env
        raise RuntimeError(
            "fastmcp is not installed; it ships with mvf1. "
            "Install the plugin dependencies (`hermes plugins install ... --yes-deps`) "
            "or `pip install fastmcp`."
        ) from exc

    server = FastMCP(name=name, instructions=INSTRUCTIONS)
    mode = feed.session_mode()
    for spec in specs():
        if spec.modes and mode != "unknown" and mode not in spec.modes:
            continue
        server.tool(name=spec.name, description=spec.schema.get("description", ""))(_wrapper(spec))
    return server


def run() -> None:
    build_server().run()
