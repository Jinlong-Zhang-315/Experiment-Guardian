"""R14 托管认证配置的启动期保护。"""

import pytest
from pydantic import ValidationError

from experiment_guardian.core.config import Settings


def _production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": "production",
        "web_public_base_url": "https://guardian.example.com",
        "web_frontend_url": "https://guardian.example.com",
        "cognito_issuer_url": "https://cognito-idp.example.com/pool",
        "cognito_domain": "https://guardian.auth.example.com",
        "cognito_web_client_id": "web-client",
        "cognito_web_client_secret": "confidential-client-secret",
        "web_oidc_state_key": "o" * 32,
        "web_csrf_secret": "c" * 32,
        "s3_bucket": "experiment-guardian",
        "sqs_submission_queue_url": "https://sqs.example.com/submissions",
        "bedrock_summary_model_id": "summary-model",
        "bedrock_embedding_model_id": "embedding-model",
    }
    values.update(overrides)
    return values


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate(_production_settings(**overrides))


def test_production_requires_confidential_cognito_client_secret() -> None:
    with pytest.raises(ValidationError, match="COGNITO_WEB_CLIENT_SECRET"):
        _settings(cognito_web_client_secret=None)


def test_production_remote_mcp_requires_matching_https_resource() -> None:
    with pytest.raises(ValidationError, match="resource identifier"):
        _settings(
            mcp_transport="streamable-http",
            mcp_public_url="https://guardian.example.com/mcp",
            mcp_oauth_resource_identifier="https://other.example.com/mcp",
        )


def test_production_managed_identity_configuration_is_accepted() -> None:
    settings = _settings(
        mcp_transport="streamable-http",
        mcp_public_url="https://guardian.example.com/mcp",
        mcp_oauth_resource_identifier="https://guardian.example.com/mcp",
    )
    assert settings.app_env == "production"


def _local_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": "development",
        "deployment_mode": "local",
        "web_auth_mode": "local_owner",
        "local_owner_email": "owner@example.com",
        "object_storage_backend": "s3_compatible",
        "s3_endpoint_url": "http://minio:9000",
        "s3_presign_endpoint_url": "http://127.0.0.1:9000",
        "s3_bucket": "experiment-guardian",
        "s3_access_key": "local-key",
        "s3_secret_key": "local-secret",
        "queue_backend": "database",
        "llm_provider": "bailian",
        "bailian_api_key": "test-key",
        "bailian_base_url": "https://bailian.example/v1",
        "bailian_summary_model": "summary-model",
        "bailian_embedding_model": "embedding-model",
    }
    values.update(overrides)
    return values


def test_local_mode_does_not_require_any_aws_or_cognito_configuration() -> None:
    settings = Settings.model_validate(_local_settings())
    assert settings.deployment_mode == "local"
    assert settings.cognito_issuer_url == ""
    assert settings.sqs_submission_queue_url == ""
    assert settings.bedrock_summary_model_id == ""


def test_local_mode_rejects_production_and_inconsistent_backends() -> None:
    with pytest.raises(ValidationError, match="只能用于"):
        Settings.model_validate(_local_settings(app_env="production"))
    with pytest.raises(ValidationError, match="QUEUE_BACKEND"):
        Settings.model_validate(_local_settings(queue_backend="sqs"))


def test_local_mode_requires_bailian_and_real_s3_compatible_inputs() -> None:
    with pytest.raises(ValidationError, match="BAILIAN_API_KEY"):
        Settings.model_validate(_local_settings(bailian_api_key=""))
    with pytest.raises(ValidationError, match="S3_PRESIGN_ENDPOINT_URL"):
        Settings.model_validate(_local_settings(s3_presign_endpoint_url=""))
    with pytest.raises(ValidationError, match=r"cockroachdb\+psycopg"):
        Settings.model_validate(
            _local_settings(database_url="postgresql+psycopg://root@cockroachdb/local")
        )


def test_cloud_mode_rejects_local_backends_and_requires_cloud_worker_inputs() -> None:
    with pytest.raises(ValidationError, match="QUEUE_BACKEND"):
        _settings(queue_backend="database")
    with pytest.raises(ValidationError, match="SQS_SUBMISSION_QUEUE_URL"):
        _settings(sqs_submission_queue_url="")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object_storage_backend", "filesystem"),
        ("queue_backend", "redis"),
        ("llm_provider", "unknown"),
    ],
)
def test_unknown_infrastructure_backend_names_fail_during_settings_validation(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate(_local_settings(**{field: value}))


def test_bailian_dimension_must_match_fixed_vector_schema() -> None:
    with pytest.raises(ValidationError, match="BAILIAN_EMBEDDING_DIMENSION"):
        Settings.model_validate(_local_settings(bailian_embedding_dimension=1536))
