"""MCP Server 工具注册测试。"""

import pytest

from experiment_guardian.mcp_server.server import mcp


@pytest.mark.asyncio
async def test_mcp_exposes_only_the_six_p0_tools() -> None:
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "project_get_context",
        "experiment_check_plan",
        "run_manifest_create",
        "submission_prepare",
        "submission_finalize",
        "experiments_query",
    }

    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "actor_id" not in properties
        assert "requester_id" not in properties
