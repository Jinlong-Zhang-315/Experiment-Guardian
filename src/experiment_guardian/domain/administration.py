"""首版项目初始化的管理端契约。"""

from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.contracts import ContractModel, ProjectContextBundle
from experiment_guardian.domain.enums import ProtectionLevel


class InitialProjectInput(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    repository_url: str | None = Field(default=None, max_length=1000)


class InitialContextInput(ContractModel):
    goal: str = Field(min_length=1)
    non_goals: list[str] = Field(default_factory=list)
    mainline_model: str = Field(min_length=1, max_length=500)
    baseline: dict[str, Any] = Field(default_factory=dict)
    dataset: str = Field(min_length=1, max_length=200)
    protocol: str = Field(min_length=1, max_length=200)
    primary_metric: dict[str, Any]
    default_seeds: list[int] = Field(min_length=1)
    active_branch: str = Field(min_length=1, max_length=500)
    active_config: dict[str, Any]
    deprecated_items: list[Any] = Field(default_factory=list)
    key_decisions: list[Any] = Field(default_factory=list)
    change_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_seeds(self) -> "InitialContextInput":
        if len(self.default_seeds) != len(set(self.default_seeds)):
            raise ValueError("default_seeds 不能重复")
        return self


class InitialIntentInput(ContractModel):
    name: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    allowed_variables: list[str] = Field(default_factory=list)
    controlled_variables: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    original_message: str = Field(min_length=1)


class InitialConstraintInput(ContractModel):
    parameter_path: str = Field(min_length=1, max_length=1000)
    protection_level: ProtectionLevel
    expected_value: Any
    allowed_values: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    reason: str = Field(min_length=1)
    original_message: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_allowed_range(self) -> "InitialConstraintInput":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum 不能大于 maximum")
        if self.allowed_values is not None and self.expected_value not in self.allowed_values:
            raise ValueError("expected_value 必须包含在 allowed_values 中")
        if self.minimum is not None or self.maximum is not None:
            if not isinstance(self.expected_value, int | float) or isinstance(
                self.expected_value, bool
            ):
                raise ValueError("使用 minimum/maximum 时 expected_value 必须是数值")
            if self.minimum is not None and self.expected_value < self.minimum:
                raise ValueError("expected_value 小于 minimum")
            if self.maximum is not None and self.expected_value > self.maximum:
                raise ValueError("expected_value 大于 maximum")
        return self


class ProjectInitializeRequest(ContractModel):
    project: InitialProjectInput
    context: InitialContextInput
    intent: InitialIntentInput
    constraints: list[InitialConstraintInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_constraint_paths(self) -> "ProjectInitializeRequest":
        paths = [item.parameter_path for item in self.constraints]
        if len(paths) != len(set(paths)):
            raise ValueError("初始化约束的 parameter_path 不能重复")
        experiment_variables = {
            item.parameter_path
            for item in self.constraints
            if item.protection_level is ProtectionLevel.EXPERIMENT_VARIABLE
        }
        if experiment_variables != set(self.intent.allowed_variables):
            raise ValueError("intent.allowed_variables 必须与 EXPERIMENT_VARIABLE 约束路径完全一致")
        return self


class ProjectInitializeResponse(ContractModel):
    project_id: UUID
    context_bundle: ProjectContextBundle
