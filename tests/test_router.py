"""Tests for filename routing."""

from router import route_filename, load_config


def test_load_config():
    mappings = load_config()
    assert len(mappings) >= 3
    assert mappings[0]["pattern"] == "sales"


def test_route_sales():
    assert route_filename("sales_march_2026.xlsx") == "sales"


def test_route_invoice():
    assert route_filename("invoice_jan.xlsx") == "invoices"


def test_route_inv_abbreviation():
    assert route_filename("inv_2026.xlsx") == "invoices"


def test_route_inventory():
    assert route_filename("inventory_q1.xlsx") == "inventory"


def test_route_unknown_derives_from_filename():
    result = route_filename("weird_report_2026.xlsx")
    assert result == "weird_report_2026"


def test_route_with_custom_mappings():
    mappings = [{"pattern": "budget", "table": "budgets"}]
    assert route_filename("budget_2026.xlsx", mappings) == "budgets"


def test_route_case_insensitive():
    assert route_filename("SALES_Q1.xlsx") == "sales"
