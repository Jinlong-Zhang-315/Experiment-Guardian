"""应用层可安全暴露的业务错误。"""


class ApplicationError(RuntimeError):
    code = "APPLICATION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(ApplicationError):
    code = "AUTHENTICATION_FAILED"


class AuthorizationError(ApplicationError):
    code = "PERMISSION_DENIED"


class RecentAuthenticationRequiredError(ApplicationError):
    """敏感操作需要当前认证后端提供近期认证，不重新实现应用密码。"""

    code = "RECENT_AUTHENTICATION_REQUIRED"


class ResourceNotFoundError(ApplicationError):
    code = "RESOURCE_NOT_FOUND"


class ConflictError(ApplicationError):
    code = "CONFLICT"


class InputValidationError(ApplicationError):
    code = "INVALID_INPUT"


class DataIntegrityError(ApplicationError):
    """持久化结果违反确定性数据库约束，不能通过重试恢复。"""

    code = "DATA_INTEGRITY_ERROR"


class ServiceUnavailableError(ApplicationError):
    code = "SERVICE_UNAVAILABLE"


class ModelProviderError(ServiceUnavailableError):
    """可安全持久化的外部模型错误，不携带响应正文或凭据。"""

    code = "MODEL_PROVIDER_ERROR"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        category: str,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.category = category
        self.retryable = retryable
        self.http_status = http_status


class FeatureUnavailableError(ApplicationError):
    code = "FEATURE_NOT_IMPLEMENTED"
