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


class ResourceNotFoundError(ApplicationError):
    code = "RESOURCE_NOT_FOUND"


class ConflictError(ApplicationError):
    code = "CONFLICT"


class InputValidationError(ApplicationError):
    code = "INVALID_INPUT"


class ServiceUnavailableError(ApplicationError):
    code = "SERVICE_UNAVAILABLE"


class FeatureUnavailableError(ApplicationError):
    code = "FEATURE_NOT_IMPLEMENTED"
