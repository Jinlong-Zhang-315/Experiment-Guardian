"""面向本地 Coding Agent 的产品 MCP Server。

MCP 层只处理参数校验和协议转换，禁止在工具函数中直接拼 SQL、访问 S3 或调用 Bedrock。
六个工具全部委托给应用门面，因此 REST 和 MCP 可以共享权限、幂等与审计逻辑。
"""

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from experiment_guardian.application.container import (
    get_guardian_use_cases,
    get_identity_provider,
)
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    ExperimentCheckPlanCommand,
    ExperimentQueryCommand,
    LocalAttestation,
)
from experiment_guardian.domain.enums import ConfigFormat, ExperimentStatus

settings = get_settings()
mcp = FastMCP(
    name="experiment-guardian",
    instructions=(
        "读取经过用户确认的团队实验上下文、执行训练前配置一致性检查，并提交可追溯的"
        "实验草稿。系统提高一致性、可追溯性和风险可见性，不保证实验行为或结果正确。"
        "LOCAL_ATTESTED 字段仅代表本地 Agent 声明。"
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    log_level=settings.log_level,
)


@mcp.tool()
def project_get_context(project_id: str) -> dict[str, Any]:
    """读取当前正式上下文、Active 意图、已确认约束及其不可省略的版本信息。"""

    identity = get_identity_provider().current_identity()
    result = get_guardian_use_cases().project_get_context(
        project_id=UUID(project_id), identity=identity
    )
    return result.model_dump(mode="json")


@mcp.tool()
def experiment_check_plan(
    project_id: str,
    experiment_intent_id: str,
    idempotency_key: str,
    config_format: str,
    config_content: str,
    command: str,
    git_commit: str,
    local_attestation: dict[str, Any],
) -> dict[str, Any]:
    """检查配置与正式意图的一致性；该结果不等于真实训练行为已验证正确。"""

    identity = get_identity_provider().current_identity()
    payload = ExperimentCheckPlanCommand(
        project_id=UUID(project_id),
        experiment_intent_id=UUID(experiment_intent_id),
        idempotency_key=UUID(idempotency_key),
        configuration=ConfigurationDocument(
            format=ConfigFormat(config_format.lower()), content=config_content
        ),
        command=command,
        git_commit=git_commit,
        local_attestation=LocalAttestation.model_validate(local_attestation),
    )
    result = get_guardian_use_cases().experiment_check_plan(payload, identity)
    return result.model_dump(mode="json")


@mcp.tool()
def run_manifest_create(plan_check_id: str, idempotency_key: str) -> dict[str, Any]:
    """根据 PASS 或已经由 Owner 批准的 plan check 创建不可变 Manifest。"""

    identity = get_identity_provider().current_identity()
    result = get_guardian_use_cases().run_manifest_create(
        plan_check_id=UUID(plan_check_id),
        actor_id=identity.user_id,
        idempotency_key=UUID(idempotency_key),
    )
    return dict(result)


@mcp.tool()
def submission_prepare(
    project_id: str,
    run_manifest_id: str,
    idempotency_key: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """创建实验草稿并为白名单文件返回 S3 预签名上传地址。"""

    identity = get_identity_provider().current_identity()
    result = get_guardian_use_cases().submission_prepare(
        project_id=UUID(project_id),
        run_manifest_id=UUID(run_manifest_id),
        actor_id=identity.user_id,
        idempotency_key=UUID(idempotency_key),
        files=files,
    )
    return dict(result)


@mcp.tool()
def submission_finalize(
    submission_id: str,
    idempotency_key: str,
    uploaded_files: list[dict[str, Any]],
) -> dict[str, Any]:
    """确认文件上传完成并启动可恢复的提交分析工作流。"""

    identity = get_identity_provider().current_identity()
    result = get_guardian_use_cases().submission_finalize(
        submission_id=UUID(submission_id),
        actor_id=identity.user_id,
        idempotency_key=UUID(idempotency_key),
        uploaded_files=uploaded_files,
    )
    return dict(result)


@mcp.tool()
def experiments_query(
    project_id: str,
    query: str,
    protocol: str,
    model_name: str | None = None,
    seed: int | None = None,
    include_historical: bool = False,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """先按项目/状态/协议等过滤正式记录，再将向量结果作为候选证据返回。"""

    safe_top_k = max(1, min(top_k, 50))
    statuses = {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED}
    if include_historical:
        statuses.update({ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED})
    identity = get_identity_provider().current_identity()
    command = ExperimentQueryCommand(
        project_id=UUID(project_id),
        actor_id=identity.user_id,
        query=query,
        protocol=protocol,
        model_name=model_name,
        seed=seed,
        statuses=statuses,
        include_historical=include_historical,
        top_k=safe_top_k,
    )
    result = get_guardian_use_cases().experiments_query(command)
    return [item.model_dump(mode="json") for item in result]


def run() -> None:
    """启动 MCP Server；本地默认 stdio，云端可使用 streamable-http。"""

    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    run()
