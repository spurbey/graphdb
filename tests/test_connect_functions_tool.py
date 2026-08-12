from __future__ import annotations

from tools import graph_mcp_server
from tools import graph_tools


def test_connect_functions_is_exposed_in_manifest_and_dispatch():
    names = {tool["name"] for tool in graph_mcp_server.MANIFEST["tools"]}
    assert "connect_functions" in names
    assert "connect_functions" in graph_mcp_server.TOOL_MAP


def test_connect_functions_validates_public_inputs():
    assert "error" in graph_tools.connect_functions(["one"])
    assert "error" in graph_tools.connect_functions(["one", "two"], direction="sideways")

