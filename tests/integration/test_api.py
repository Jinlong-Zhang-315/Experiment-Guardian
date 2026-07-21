"""不依赖外部服务的 API 启动冒烟测试。"""

import httpx
import pytest

from experiment_guardian.main import create_app


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    # 直接使用 ASGITransport，测试不会打开端口，也不依赖同步 TestClient 的线程门户。
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_capabilities_exposes_exactly_six_mcp_tools() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert len(response.json()["mcp_tools"]) == 6
    assert response.json()["plan_results"] == ["PASS", "NEEDS_APPROVAL", "BLOCKED"]
    assert "不代表" in response.json()["verification_disclaimer"]
