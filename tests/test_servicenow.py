"""Tests for ServiceNow basic-auth client."""

from unittest.mock import Mock, patch

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
