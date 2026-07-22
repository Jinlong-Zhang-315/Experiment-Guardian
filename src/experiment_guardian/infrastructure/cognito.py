"""Amazon Cognito User Pool 的 OIDC Authorization Code + PKCE 适配器。"""

from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from experiment_guardian.application.errors import AuthenticationError, ServiceUnavailableError
from experiment_guardian.application.ports import OidcIdentity


class CognitoOidcProvider:
    """只返回验证后的身份声明，Cognito Token 不向路由或浏览器透传。"""

    def __init__(
        self,
        *,
        issuer_url: str,
        managed_login_domain: str,
        client_id: str,
        client_secret: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._issuer = issuer_url.rstrip("/")
        self._domain = managed_login_domain.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout_seconds
        self._jwks = PyJWKClient(f"{self._issuer}/.well-known/jwks.json")

    def _require_configured(self) -> None:
        if not self._issuer or not self._domain or not self._client_id:
            raise ServiceUnavailableError("Cognito Web OIDC 尚未配置")

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        prompt: str | None = None,
    ) -> str:
        self._require_configured()
        parameters = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if prompt:
            parameters["prompt"] = prompt
        return f"{self._domain}/oauth2/authorize?{urlencode(parameters)}"

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcIdentity:
        self._require_configured()
        data = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        auth = (self._client_id, self._client_secret) if self._client_secret else None
        try:
            response = httpx.post(
                f"{self._domain}/oauth2/token",
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            token_response = response.json()
            id_token = token_response.get("id_token")
            if not isinstance(id_token, str):
                raise AuthenticationError("Cognito 未返回 ID Token")
            signing_key = self._jwks.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "nonce"]},
            )
        except AuthenticationError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError) as exc:
            raise AuthenticationError("Cognito 授权码交换或 ID Token 校验失败") from exc

        if claims.get("nonce") != expected_nonce:
            raise AuthenticationError("OIDC nonce 不匹配")
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not subject or not isinstance(email, str) or not email:
            raise AuthenticationError("Cognito 身份缺少 sub 或 email")
        auth_time = claims.get("auth_time", claims.get("iat"))
        if not isinstance(auth_time, int | float):
            raise AuthenticationError("Cognito 身份缺少有效 auth_time")
        return OidcIdentity(
            subject=subject,
            email=email.strip().lower(),
            email_verified=claims.get("email_verified") is True,
            authenticated_at=datetime.fromtimestamp(auth_time, tz=UTC),
        )

    def logout_url(self, *, redirect_uri: str) -> str:
        self._require_configured()
        query = urlencode({"client_id": self._client_id, "logout_uri": redirect_uri})
        return f"{self._domain}/logout?{query}"
