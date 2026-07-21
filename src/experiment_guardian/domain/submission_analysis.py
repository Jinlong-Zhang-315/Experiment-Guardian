"""Submission 配置与结果文件的确定性解析规则。"""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    SubmittedResultDocument,
)
from experiment_guardian.domain.enums import ConfigFormat
from experiment_guardian.domain.plan_check import ConfigurationError, parse_configuration


class SubmissionDocumentError(ValueError):
    """上传内容不是 R11 支持的严格结构时抛出。"""


def _decode_utf8(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SubmissionDocumentError(f"{label} 必须使用 UTF-8 编码") from exc


def parse_submitted_configuration(*, filename: str, payload: bytes) -> dict[str, Any]:
    """按扩展名解析 CONFIG，复用训练前检查的去重键和 YAML 标量规则。"""

    suffix = Path(filename).suffix.lower()
    format_by_suffix = {
        ".json": ConfigFormat.JSON,
        ".yaml": ConfigFormat.YAML,
        ".yml": ConfigFormat.YAML,
    }
    config_format = format_by_suffix.get(suffix)
    if config_format is None:
        raise SubmissionDocumentError("CONFIG 文件只支持 YAML 或 JSON")
    try:
        return parse_configuration(
            ConfigurationDocument(
                format=config_format,
                content=_decode_utf8(payload, "CONFIG"),
            )
        )
    except (ConfigurationError, ValidationError) as exc:
        raise SubmissionDocumentError(str(exc)) from exc


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SubmissionDocumentError(f"result.json 发现重复字段 {key!r}")
        value[key] = item
    return value


def parse_submitted_result(payload: bytes) -> SubmittedResultDocument:
    """解析固定的 result.json；不允许 NaN、重复键、额外字段或隐式类型。"""

    def reject_constant(value: str) -> None:
        raise SubmissionDocumentError(f"result.json 不允许非有限数值 {value}")

    try:
        parsed = json.loads(
            _decode_utf8(payload, "RESULT"),
            object_pairs_hook=_unique_json_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise SubmissionDocumentError(f"result.json 无法解析: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SubmissionDocumentError("result.json 根节点必须是对象")
    try:
        # Enum 与 ISO 时间戳在 JSON 中天然以字符串表示；字段级校验器已经阻止指标布尔值、
        # 非有限数值和错误 schema_version，不使用全局 strict 破坏合法的枚举解析。
        return SubmittedResultDocument.model_validate(parsed)
    except ValidationError as exc:
        raise SubmissionDocumentError(f"result.json 结构不合法: {exc}") from exc
