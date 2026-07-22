"""服务端认证身份边界。

MCP 工具参数不能携带可由客户端伪造的用户 UUID。真实部署中的实现应从已经校验的
MCP Token 或服务端 Session 构造该对象，再由应用服务继续执行项目权限检查。
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    """当前已认证调用者；该信息只能由服务端认证适配器创建。"""

    user_id: UUID
    team_id: UUID
    token_id: UUID
    project_id: UUID | None = None
    scopes: frozenset[str] = frozenset()
    authentication_method: Literal[
        "API_TOKEN", "MCP_TOKEN", "WEB_SESSION", "MCP_OAUTH"
    ] = "API_TOKEN"
    recent_authentication: bool = True
    subject: str | None = None
    client_id: str | None = None


class IdentityProvider(Protocol):
    """读取当前 MCP 请求所绑定的认证身份。"""

    def current_identity(self) -> RequestIdentity: ...
