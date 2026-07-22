"""验证公开 R14 部署的 Web、OIDC 和 MCP OAuth 发现边界。"""

import argparse
import json
import sys
from typing import Any

import httpx


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{url} 未返回 JSON object")
    return payload


def verify(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    results: dict[str, Any] = {}
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        health = _get_json(client, f"{base_url}/api/v1/health")
        if health.get("status") != "ok":
            raise ValueError("FastAPI health 状态异常")
        results["api_health"] = "PASS"

        web = client.get(f"{base_url}/")
        web.raise_for_status()
        if "Experiment Guardian" not in web.text:
            raise ValueError("CloudFront Web 首页不包含产品标识")
        results["web_distribution"] = "PASS"

        metadata_url = f"{base_url}/.well-known/oauth-protected-resource/mcp"
        protected_resource = _get_json(client, metadata_url)
        expected_resource = f"{base_url}/mcp"
        if protected_resource.get("resource") != expected_resource:
            raise ValueError("MCP Protected Resource Metadata 的 resource 不匹配")
        authorization_servers = protected_resource.get("authorization_servers")
        scopes = protected_resource.get("scopes_supported")
        if not isinstance(authorization_servers, list) or len(authorization_servers) != 1:
            raise ValueError("MCP 必须公开唯一 Cognito Authorization Server")
        expected_scopes = {
            f"{expected_resource}/{name}"
            for name in {
                "project.read",
                "experiment.check",
                "manifest.create",
                "submission.create",
                "submission.finalize",
                "submission.read",
                "experiment.query",
            }
        }
        if not isinstance(scopes, list) or set(scopes) != expected_scopes:
            raise ValueError("MCP 必须公开七个收敛 OAuth scope")
        results["mcp_protected_resource_metadata"] = "PASS"

        issuer = str(authorization_servers[0]).rstrip("/")
        discovery = _get_json(client, f"{issuer}/.well-known/openid-configuration")
        for field in ["authorization_endpoint", "token_endpoint", "jwks_uri"]:
            if not discovery.get(field):
                raise ValueError(f"Cognito OIDC discovery 缺少 {field}")
        if discovery.get("registration_endpoint"):
            raise ValueError("R14 不应公开动态客户端注册端点")
        results["cognito_oidc_discovery"] = "PASS"
        results["dynamic_client_registration"] = "DISABLED"

        for role, cookie in [("OWNER", args.owner_cookie), ("RESEARCHER", args.researcher_cookie)]:
            if not cookie:
                continue
            response = client.get(
                f"{base_url}/api/v1/auth/me",
                cookies={args.cookie_name: cookie},
            )
            response.raise_for_status()
            session = response.json()
            if session.get("role") != role:
                raise ValueError(f"{role} Session 返回了错误角色")
            token_fields = {"access_token", "id_token", "refresh_token"}
            if "csrf_token" not in session or token_fields & set(session):
                raise ValueError(f"{role} Session 回执缺少 CSRF 或泄露 Cognito Token")
            results[f"{role.lower()}_web_session"] = "PASS"

    return {
        "base_url": base_url,
        "result": "PASS",
        "checks": results,
        "disclaimer": "该验收证明部署和认证边界可用，不代表实验行为或结果正确。",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--owner-cookie")
    parser.add_argument("--researcher-cookie")
    parser.add_argument("--cookie-name", default="eg_session")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main() -> int:
    try:
        result = verify(build_parser().parse_args())
    except (httpx.HTTPError, ValueError) as exc:
        print(json.dumps({"result": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
