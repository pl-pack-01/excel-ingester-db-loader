"""Tests for ServiceNow REST API client (basic auth, bearer, and OAuth 2.0)."""

from unittest.mock import Mock, patch, call

import pytest

import servicenow as sn


class TestConnection:
    def test_connection_success(self):
        with patch("servicenow.requests.get") as mock_get:
            user_response = Mock()
            user_response.status_code = 200
            user_response.json.return_value = {
                "result": [
                    {
                        "sys_id": "abc123",
                        "email": "user@example.com",
                        "name": "Test User",
                        "department": "IT",
                    }
                ]
            }

            tables_response = Mock()
            tables_response.status_code = 200
            tables_response.json.return_value = {"result": [{"name": "incident"}]}

            mock_get.side_effect = [user_response, tables_response]

            result = sn.test_connection(
                "https://dev123.service-now.com",
                username="user",
                password="pass",
            )

            assert result["status"] == "success"
            assert result["user_info"]["name"] == "Test User"

    def test_connection_requires_credentials(self):
        result = sn.test_connection("https://dev123.service-now.com")
        assert result["status"] == "error"
        assert "basic auth" in result["message"].lower()

    def test_connection_with_bearer_token(self):
        with patch("servicenow.requests.get") as mock_get:
            user_response = Mock()
            user_response.status_code = 200
            user_response.json.return_value = {"result": [{"sys_id": "abc123", "name": "Token User"}]}

            tables_response = Mock()
            tables_response.status_code = 200
            tables_response.json.return_value = {"result": []}

            mock_get.side_effect = [user_response, tables_response]

            result = sn.test_connection(
                "https://dev123.service-now.com",
                auth_method="bearer",
                bearer_token="fake-token",
            )

            assert result["status"] == "success"
            first_call_headers = mock_get.call_args_list[0].kwargs["headers"]
            assert first_call_headers["Authorization"] == "Bearer fake-token"

    def test_connection_auth_failure(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 401
            mock_get.return_value = response

            result = sn.test_connection(
                "https://dev123.service-now.com",
                username="user",
                password="bad-pass",
            )

            assert result["status"] == "error"
            assert "Authentication failed" in result["message"]


class TestQueryTable:
    def test_query_table_success(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "result": [
                    {"sys_id": "1", "number": "INC0001"},
                    {"sys_id": "2", "number": "INC0002"},
                ]
            }
            mock_get.return_value = response

            result = sn.query_table(
                "https://dev123.service-now.com",
                "incident",
                username="user",
                password="pass",
                limit=5,
            )

            assert result["status"] == "success"
            assert result["count"] == 2
            assert result["table"] == "incident"
            assert mock_get.call_args.kwargs["params"]["sysparm_limit"] == 5

    def test_query_table_requires_credentials(self):
        result = sn.query_table("https://dev123.service-now.com", "incident")
        assert result["status"] == "error"
        assert "basic auth" in result["message"].lower()

    def test_query_table_with_bearer_token(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"result": [{"sys_id": "1"}]}
            mock_get.return_value = response

            result = sn.query_table(
                "https://dev123.service-now.com",
                "incident",
                auth_method="bearer",
                bearer_token="fake-token",
            )

            assert result["status"] == "success"
            assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer fake-token"

    def test_query_table_not_found(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 404
            mock_get.return_value = response

            result = sn.query_table(
                "https://dev123.service-now.com",
                "unknown_table",
                username="user",
                password="pass",
            )

            assert result["status"] == "error"
            assert "not found" in result["message"]

    def test_query_table_filters(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"result": []}
            mock_get.return_value = response

            sn.query_table(
                "https://dev123.service-now.com",
                "incident",
                username="user",
                password="pass",
                filters={"state": "1", "active": "true"},
            )

            assert "sysparm_query" in mock_get.call_args.kwargs["params"]


class TestNormalization:
    def test_url_normalized_in_test_connection(self):
        with patch("servicenow.requests.get") as mock_get:
            user_response = Mock()
            user_response.status_code = 200
            user_response.json.return_value = {"result": [{"sys_id": "1"}]}
            tables_response = Mock()
            tables_response.status_code = 200
            tables_response.json.return_value = {"result": []}
            mock_get.side_effect = [user_response, tables_response]

            sn.test_connection("dev123.service-now.com", username="user", password="pass")

            called_url = mock_get.call_args_list[0].args[0]
            assert called_url.startswith("https://")

    def test_url_normalized_in_query_table(self):
        with patch("servicenow.requests.get") as mock_get:
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"result": []}
            mock_get.return_value = response

            sn.query_table("dev123.service-now.com", "incident", username="user", password="pass")

            called_url = mock_get.call_args.args[0]
            assert called_url.startswith("https://")


class TestPullOperationalSnapshot:
    def test_problem_ci_enrichment_fields_added(self):
        incident_rows = {"status": "success", "records": []}
        request_rows = {"status": "success", "records": []}
        problem_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "prob-1",
                    "number": "PRB0001",
                    "cmdb_ci": {"value": "ci-123", "display_value": "Edge Router"},
                }
            ],
        }
        ci_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "ci-123",
                    "name": "Edge Router",
                    "location": {"value": "loc-1", "display_value": "US East DC"},
                    "u_lob": {"value": "lob-1", "display_value": "JCP.LOB"},
                }
            ],
        }
        location_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "loc-1",
                    "name": "US East DC",
                    "full_name": "Global/Americas/UNITED STATES",
                    "parent": "",
                }
            ],
        }
        group_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "lob-1",
                    "name": "JCP.LOB",
                    "u_customer_account": {"value": "acct-1", "display_value": "Penney Opco LLC"},
                }
            ],
        }
        account_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "acct-1",
                    "name": "Penney Opco LLC",
                    "country": "US",
                    "state": "TX",
                    "city": "Plano",
                }
            ],
        }

        with patch("servicenow.fetch_all_records") as mock_fetch:
            mock_fetch.side_effect = [
                incident_rows,
                request_rows,
                problem_rows,
                ci_rows,
                location_rows,
                group_rows,
                account_rows,
            ]

            result = sn.pull_operational_snapshot(
                "https://dev123.service-now.com",
                username="user",
                password="pass",
                include_problems=True,
                since_days=365,
                operational_query="sys_updated_on>=2026-08-01 00:00:00^sys_updated_on<2026-08-25 00:00:00",
            )

        assert result["status"] == "success"
        assert result["problem_count"] == 1
        problem = result["problems"][0]
        assert problem["cmdb_ci_sys_id"] == "ci-123"
        assert problem["cmdb_ci_name"] == "Edge Router"
        assert problem["cmdb_ci_region"] == "Americas"
        assert problem["cmdb_ci_territory"] == "UNITED STATES"
        assert problem["cmdb_ci_lob"] == "JCP.LOB"
        assert problem["cmdb_ci_customer_relationship"] == "Penney Opco LLC"
        assert problem["cmdb_ci_customer_relationship_region"] == "US"
        assert problem["cmdb_ci_customer_relationship_territory"] == "TX"
        assert mock_fetch.call_args_list[0].kwargs["query"] == (
            "sys_updated_on>=2026-08-01 00:00:00^sys_updated_on<2026-08-25 00:00:00"
        )
        assert mock_fetch.call_args_list[3].kwargs["query"] == "sys_idINci-123"
        assert mock_fetch.call_args_list[4].kwargs["query"] == "sys_idINloc-1"
        assert mock_fetch.call_args_list[5].kwargs["query"] == "sys_idINlob-1"
        assert mock_fetch.call_args_list[6].kwargs["query"] == "sys_idINacct-1"

    def test_resolve_region_territory_walks_parent_chain(self):
        locations_by_id = {
            "leaf": {"sys_id": "leaf", "full_name": "Azure - East US - VSP A", "parent": "country"},
            "country": {
                "sys_id": "country",
                "full_name": "Global/Americas/UNITED STATES",
                "parent": "",
            },
        }
        region, territory = sn.resolve_region_territory("leaf", locations_by_id)
        assert region == "Americas"
        assert territory == "UNITED STATES"

    def test_resolve_region_territory_unresolved_returns_none(self):
        locations_by_id = {"leaf": {"sys_id": "leaf", "full_name": "Datacenter A", "parent": ""}}
        region, territory = sn.resolve_region_territory("leaf", locations_by_id)
        assert region is None
        assert territory is None

    def test_fetch_customer_account_geography_resolves_chain(self):
        group_rows = {
            "status": "success",
            "records": [
                {
                    "sys_id": "lob-1",
                    "name": "JCP.LOB",
                    "u_customer_account": {"value": "acct-1", "display_value": "Penney Opco LLC"},
                }
            ],
        }
        account_rows = {
            "status": "success",
            "records": [
                {"sys_id": "acct-1", "name": "Penney Opco LLC", "country": "US", "state": "TX", "city": "Plano"}
            ],
        }
        with patch("servicenow.fetch_all_records") as mock_fetch:
            mock_fetch.side_effect = [group_rows, account_rows]
            result = sn.fetch_customer_account_geography(
                "https://dev123.service-now.com",
                lob_group_ids=["lob-1"],
                username="user",
                password="pass",
            )
        assert result["lob-1"]["lob_name"] == "JCP.LOB"
        assert result["lob-1"]["account_name"] == "Penney Opco LLC"
        assert result["lob-1"]["country"] == "US"
        assert result["lob-1"]["state"] == "TX"

    def test_fetch_customer_account_geography_empty_input(self):
        assert sn.fetch_customer_account_geography("https://dev123.service-now.com", lob_group_ids=[]) == {}


class TestAzureAccessToken:
    def test_get_azure_access_token_success(self):
        with patch("servicenow.requests.post") as mock_post:
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "access_token": "token-123",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            mock_post.return_value = response

            result = sn.get_azure_access_token(
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret="client-secret",
                scope="api://servicenow/.default",
            )

        assert result["status"] == "success"
        assert result["access_token"] == "token-123"
        assert result["scope"] == "api://servicenow/.default"

    def test_get_azure_access_token_requires_fields(self):
        result = sn.get_azure_access_token(
            tenant_id="",
            client_id="client-id",
            client_secret="",
        )
        assert result["status"] == "error"
        assert "missing azure credentials" in result["message"].lower()


# ---------------------------------------------------------------------------
# OAuth 2.0 – token acquisition
# ---------------------------------------------------------------------------

INSTANCE = "https://dev123.service-now.com"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"

_PASSWORD_TOKEN_RESPONSE = {
    "access_token": "access-abc123",
    "token_type": "Bearer",
    "expires_in": 1799,
    "scope": "useraccount",
    "refresh_token": "refresh-xyz789",
}

_CLIENT_CREDS_TOKEN_RESPONSE = {
    "access_token": "access-def456",
    "token_type": "Bearer",
    "expires_in": 1799,
    "scope": "useraccount",
}


def _token_mock(status_code: int = 200, json_body: dict | None = None, text: str = "") -> Mock:
    m = Mock()
    m.status_code = status_code
    m.text = text
    if json_body is not None:
        m.json.return_value = json_body
    return m


class TestOAuthGetToken:
    """Unit tests for oauth_get_token() covering all branches."""

    # --- happy paths ---------------------------------------------------------

    def test_password_grant_success(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="secret",
            )

        assert result["status"] == "success"
        assert result["access_token"] == "access-abc123"
        assert result["token_type"] == "Bearer"
        assert result["expires_in"] == 1799
        assert result["refresh_token"] == "refresh-xyz789"
        assert "successfully" in result["message"].lower()

    def test_client_credentials_grant_success(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_CLIENT_CREDS_TOKEN_RESPONSE)

            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="client_credentials",
            )

        assert result["status"] == "success"
        assert result["access_token"] == "access-def456"
        assert result.get("refresh_token") is None

    # --- request shape -------------------------------------------------------

    def test_password_grant_posts_to_oauth_endpoint(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="secret",
            )

        posted_url = mock_post.call_args.args[0]
        assert posted_url == f"{INSTANCE}/oauth_token.do"

    def test_password_grant_payload_contains_credentials(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="secret",
            )

        payload = mock_post.call_args.kwargs["data"]
        assert payload["grant_type"] == "password"
        assert payload["client_id"] == CLIENT_ID
        assert payload["client_secret"] == CLIENT_SECRET
        assert payload["username"] == "admin"
        assert payload["password"] == "secret"

    def test_client_credentials_payload_has_no_user_fields(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_CLIENT_CREDS_TOKEN_RESPONSE)

            sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="client_credentials",
            )

        payload = mock_post.call_args.kwargs["data"]
        assert "username" not in payload
        assert "password" not in payload

    def test_content_type_header_is_form_urlencoded(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="u", password="p",
            )

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"

    # --- input validation errors --------------------------------------------

    def test_password_grant_missing_username_returns_error(self):
        result = sn.oauth_get_token(
            INSTANCE, CLIENT_ID, CLIENT_SECRET,
            grant_type="password", password="secret",
        )
        assert result["status"] == "error"
        assert "username" in result["message"].lower() or "password" in result["message"].lower()

    def test_password_grant_missing_password_returns_error(self):
        result = sn.oauth_get_token(
            INSTANCE, CLIENT_ID, CLIENT_SECRET,
            grant_type="password", username="admin",
        )
        assert result["status"] == "error"

    def test_unsupported_grant_type_returns_error(self):
        result = sn.oauth_get_token(
            INSTANCE, CLIENT_ID, CLIENT_SECRET,
            grant_type="authorization_code",
        )
        assert result["status"] == "error"
        assert "authorization_code" in result["message"]

    # --- HTTP error responses -----------------------------------------------

    def test_401_returns_auth_error(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(status_code=401)

            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="wrong",
            )

        assert result["status"] == "error"
        assert result["status_code"] == 401
        assert "authentication failed" in result["message"].lower()

    def test_non_200_status_returns_error(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(status_code=500, text="Internal Server Error")

            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="pass",
            )

        assert result["status"] == "error"
        assert "500" in result["message"]

    def test_oauth_error_in_response_body(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(
                json_body={"error": "invalid_client", "error_description": "Client not found"}
            )

            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="client_credentials",
            )

        assert result["status"] == "error"
        assert "invalid_client" in result["message"]

    # --- network-level errors -----------------------------------------------

    def test_timeout_returns_error(self):
        import requests as req_lib

        with patch("servicenow.requests.post", side_effect=req_lib.exceptions.Timeout):
            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="pass",
            )

        assert result["status"] == "error"
        assert "timeout" in result["message"].lower()

    def test_connection_error_returns_error(self):
        import requests as req_lib

        with patch(
            "servicenow.requests.post",
            side_effect=req_lib.exceptions.ConnectionError("name resolution failed"),
        ):
            result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="pass",
            )

        assert result["status"] == "error"
        assert "connection error" in result["message"].lower()

    # --- URL normalisation ---------------------------------------------------

    def test_url_without_scheme_is_normalised(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            sn.oauth_get_token(
                "dev123.service-now.com", CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="u", password="p",
            )

        posted_url = mock_post.call_args.args[0]
        assert posted_url.startswith("https://")

    def test_trailing_slash_stripped_from_url(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

            sn.oauth_get_token(
                f"{INSTANCE}/", CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="u", password="p",
            )

        posted_url = mock_post.call_args.args[0]
        assert "//" not in posted_url.replace("https://", "")


# ---------------------------------------------------------------------------
# OAuth 2.0 – token refresh
# ---------------------------------------------------------------------------

_REFRESH_TOKEN_RESPONSE = {
    "access_token": "access-new-111",
    "token_type": "Bearer",
    "expires_in": 1799,
    "scope": "useraccount",
    "refresh_token": "refresh-new-222",
}


class TestOAuthRefreshToken:
    """Unit tests for oauth_refresh_token()."""

    def test_refresh_success(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_REFRESH_TOKEN_RESPONSE)

            result = sn.oauth_refresh_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET, refresh_token="refresh-xyz789"
            )

        assert result["status"] == "success"
        assert result["access_token"] == "access-new-111"
        assert result["refresh_token"] == "refresh-new-222"
        assert "refreshed" in result["message"].lower()

    def test_refresh_posts_correct_grant_type(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_REFRESH_TOKEN_RESPONSE)

            sn.oauth_refresh_token(INSTANCE, CLIENT_ID, CLIENT_SECRET, "old-refresh")

        payload = mock_post.call_args.kwargs["data"]
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "old-refresh"
        assert payload["client_id"] == CLIENT_ID
        assert payload["client_secret"] == CLIENT_SECRET

    def test_refresh_invalid_token_returns_error(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(status_code=401)

            result = sn.oauth_refresh_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET, refresh_token="expired-token"
            )

        assert result["status"] == "error"
        assert result["status_code"] == 401
        assert "refresh" in result["message"].lower()

    def test_refresh_oauth_error_in_body(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(
                json_body={"error": "invalid_grant", "error_description": "Token expired"}
            )

            result = sn.oauth_refresh_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET, refresh_token="bad-token"
            )

        assert result["status"] == "error"
        assert "invalid_grant" in result["message"]

    def test_refresh_timeout_returns_error(self):
        import requests as req_lib

        with patch("servicenow.requests.post", side_effect=req_lib.exceptions.Timeout):
            result = sn.oauth_refresh_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET, refresh_token="tok"
            )

        assert result["status"] == "error"
        assert "timeout" in result["message"].lower()

    def test_refresh_url_normalised(self):
        with patch("servicenow.requests.post") as mock_post:
            mock_post.return_value = _token_mock(json_body=_REFRESH_TOKEN_RESPONSE)

            sn.oauth_refresh_token(
                "dev123.service-now.com", CLIENT_ID, CLIENT_SECRET, "tok"
            )

        posted_url = mock_post.call_args.args[0]
        assert posted_url.startswith("https://")


# ---------------------------------------------------------------------------
# OAuth 2.0 – end-to-end flow with test_connection
# ---------------------------------------------------------------------------


class TestOAuthEndToEnd:
    """
    Verifies the full OAuth → API-call pipeline:
    1. oauth_get_token() returns an access_token.
    2. That token is passed to test_connection() as a bearer token.
    """

    def test_full_oauth_flow(self):
        token_response = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)

        user_api_response = Mock()
        user_api_response.status_code = 200
        user_api_response.json.return_value = {
            "result": [{"sys_id": "u1", "name": "OAuth User", "email": "oauth@example.com"}]
        }
        tables_api_response = Mock()
        tables_api_response.status_code = 200
        tables_api_response.json.return_value = {"result": [{"name": "incident"}]}

        with patch("servicenow.requests.post", return_value=token_response) as mock_post, \
             patch("servicenow.requests.get", side_effect=[user_api_response, tables_api_response]) as mock_get:

            token_result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="secret",
            )

            assert token_result["status"] == "success"

            conn_result = sn.test_connection(
                INSTANCE,
                auth_method="bearer",
                bearer_token=token_result["access_token"],
            )

        assert conn_result["status"] == "success"
        assert conn_result["user_info"]["name"] == "OAuth User"
        auth_header = mock_get.call_args_list[0].kwargs["headers"]["Authorization"]
        assert auth_header == f"Bearer {token_result['access_token']}"

    def test_oauth_token_then_query_table(self):
        token_response = _token_mock(json_body=_PASSWORD_TOKEN_RESPONSE)
        api_response = Mock()
        api_response.status_code = 200
        api_response.json.return_value = {"result": [{"sys_id": "i1", "number": "INC0010"}]}

        with patch("servicenow.requests.post", return_value=token_response), \
             patch("servicenow.requests.get", return_value=api_response) as mock_get:

            token_result = sn.oauth_get_token(
                INSTANCE, CLIENT_ID, CLIENT_SECRET,
                grant_type="password", username="admin", password="secret",
            )
            query_result = sn.query_table(
                INSTANCE, "incident",
                auth_method="bearer",
                bearer_token=token_result["access_token"],
                limit=1,
            )

        assert query_result["status"] == "success"
        assert query_result["count"] == 1
        auth_header = mock_get.call_args.kwargs["headers"]["Authorization"]
        assert auth_header == "Bearer access-abc123"

