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
