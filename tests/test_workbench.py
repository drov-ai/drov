"""The public login shell is fixed static data; every Runtime operation stays authenticated."""

import re

import pytest

from drov.server.workbench import POLICY, assets
from tests.test_http import Policy, client, service

pytest.importorskip("fastapi")


@pytest.mark.asyncio
async def test_public_shell_fixed_assets_security_headers_and_head() -> None:
    app = service()
    async with client(app) as connection:
        for asset, (data, media) in assets().items():
            response = await connection.get(asset, headers={"Authorization": ""})
            assert response.status_code == 200 and response.content == data
            assert response.headers["content-security-policy"] == POLICY
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cross-origin-resource-policy"] == "same-origin"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["content-type"].startswith(media)
            head = await connection.head(asset, headers={"Authorization": ""})
            assert head.status_code == 200 and head.content == b""
        response = await connection.get("/workbench/", headers={"Authorization": ""})
        assert 'lang="zh-CN"' in response.text
        assert "Bearer" not in response.text and "alice" not in response.text
        for reference in re.findall(r'(?:src|href)="([^"]+)"', response.text):
            assert reference in assets()
        assert (await connection.get("/", follow_redirects=False)).headers["location"] == (
            "/workbench/"
        )
        # No credential bootstrap, provider config, source tree or arbitrary static path.
        for route in ("/v1/agents", "/openapi.json", "/v1/sessions", "/workbench/app.py"):
            assert (await connection.get(route, headers={"Authorization": ""})).status_code == 401
        for method in ("post", "delete"):
            assert (
                await getattr(connection, method)("/workbench/", headers={"Authorization": ""})
            ).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,query,status",
    [
        ({"Host": "evil.test"}, "", 400),
        ({"Origin": "https://evil.test"}, "", 403),
        ({}, "?token=synthetic", 400),
    ],
)
async def test_public_assets_keep_host_origin_and_url_credential_checks(
    headers: dict[str, str], query: str, status: int
) -> None:
    async with client(service()) as connection:
        response = await connection.get("/workbench/" + query, headers=headers)
        assert response.status_code == status


@pytest.mark.asyncio
async def test_catalog_is_authorized_metadata_only_and_workbench_can_be_disabled() -> None:
    policy = Policy()
    async with client(service(authorizer=policy, workbench=False)) as connection:
        catalog = await connection.get("/v1/agents")
        assert catalog.status_code == 200
        assert catalog.json() == {"items": [{"name": "assistant", "revision": "1"}]}
        assert (await connection.get("/workbench/")).status_code == 404
        policy.denied.add("read")
        assert (await connection.get("/v1/agents")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", ["a/b", ".", "..", "a?b#c%", "空 格/名称"])
async def test_query_session_lookup_round_trips_opaque_names(session_id: str) -> None:
    from tests.test_http import body

    async with client(service()) as connection:
        accepted = await connection.post("/v1/runs", json=body(session_id))
        assert accepted.status_code == 202
        lookup = await connection.get("/v1/session", params={"session_id": session_id})
        assert lookup.status_code == 200
        assert lookup.json()["session_id"] == session_id
        assert lookup.json()["runs"][0]["run_id"] == accepted.json()["run_id"]
        hidden = await connection.get(
            "/v1/session",
            params={"session_id": session_id},
            headers={"Authorization": "Bearer bob"},
        )
        assert hidden.status_code == 404
