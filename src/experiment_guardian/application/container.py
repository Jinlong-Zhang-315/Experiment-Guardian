"""应用依赖装配入口。

当前提交只建立框架，尚未把仓储、S3 和 LangGraph 适配器接入。所有接口均通过本模块
获取应用门面；第二步只需在这里替换实现，不需要修改 FastAPI 路由或 MCP 工具定义。
"""

from functools import lru_cache
from typing import Never

from experiment_guardian.application.identity import IdentityProvider
from experiment_guardian.application.ports import GuardianUseCases


class ApplicationNotWiredError(RuntimeError):
    """业务适配器尚未装配时返回的明确错误。"""


class UnwiredGuardianUseCases:
    """框架阶段的失败即停实现。

    返回伪造业务数据会污染演示和测试，因此未实现的用例明确失败。健康检查和 MCP 工具
    枚举仍可正常工作；接入真实仓储后删除本类即可。
    """

    @staticmethod
    def _raise() -> Never:
        raise ApplicationNotWiredError("业务适配器尚未装配，请先完成仓储与工作流实现")

    def project_get_context(self, **_: object) -> object:
        self._raise()

    def experiment_check_plan(self, *_: object, **__: object) -> object:
        self._raise()

    def run_manifest_create(self, **_: object) -> object:
        self._raise()

    def submission_prepare(self, **_: object) -> object:
        self._raise()

    def submission_finalize(self, **_: object) -> object:
        self._raise()

    def experiments_query(self, **_: object) -> object:
        self._raise()


class UnwiredIdentityProvider:
    """认证尚未接入时拒绝业务调用，避免回退到客户端提交的用户 ID。"""

    def current_identity(self) -> Never:
        raise ApplicationNotWiredError("MCP Token/Session 身份适配器尚未装配，不能确定当前调用者")


@lru_cache(maxsize=1)
def get_guardian_use_cases() -> GuardianUseCases:
    # 类型忽略只存在于临时失败即停实现；真实应用服务会完整实现 Protocol。
    return UnwiredGuardianUseCases()  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_identity_provider() -> IdentityProvider:
    """返回服务端身份提供器；不得用 MCP 工具参数构造身份。"""

    return UnwiredIdentityProvider()
