"""Tests for the outlook module."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from outlook import scan_for_attachments, OutlookAttachment, ScanDiagnostics


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeAttachments:
    """Minimal iterable that mimics Outlook's Attachments collection."""

    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def __iter__(self):
        return iter(self._items)


def _make_att(filename: str, content: bytes = b"fake"):
    """Return a mock COM attachment that writes *content* on SaveAsFile."""
    att = MagicMock()
    att.FileName = filename

    def save(path):
        Path(path).write_bytes(content)

    att.SaveAsFile.side_effect = save
    return att


def _make_msg(subject: str, sender: str, received: datetime, attachments):
    msg = MagicMock()
    msg.Subject = subject
    msg.SenderName = sender
    msg.ReceivedTime = received
    msg.Attachments = _FakeAttachments(attachments)
    return msg


class _FakeItems:
    """Iterable with a .Count attribute, mimicking folder.Items."""

    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def __iter__(self):
        return iter(self._items)


def _build_win32_mock(messages):
    """Return a mock win32com module whose Outlook exposes *messages* in Inbox."""
    folder = MagicMock()
    folder.Items = _FakeItems(messages)
    folder.Folders.Count = 0

    namespace = MagicMock()
    namespace.GetDefaultFolder.return_value = folder

    outlook_app = MagicMock()
    outlook_app.GetNamespace.return_value = namespace

    client_mod = MagicMock()
    client_mod.Dispatch.return_value = outlook_app

    win32_mod = MagicMock()
    win32_mod.client = client_mod

    return win32_mod


def _mock_com(monkeypatch, win32_mock):
    """Register win32com and pythoncom mocks so COM imports succeed in tests."""
    pythoncom_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom_mock)
    monkeypatch.setitem(sys.modules, "win32com", win32_mock)
    monkeypatch.setitem(sys.modules, "win32com.client", win32_mock.client)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_raises_when_pywin32_missing(monkeypatch):
    """RuntimeError is raised when pywin32 cannot be imported."""
    monkeypatch.setitem(sys.modules, "pythoncom", None)
    monkeypatch.setitem(sys.modules, "win32com", None)
    monkeypatch.setitem(sys.modules, "win32com.client", None)
    with pytest.raises(RuntimeError, match="pywin32"):
        scan_for_attachments()


def test_returns_excel_attachments(monkeypatch):
    """Excel attachments within the date window are returned."""
    att = _make_att("report.xlsx")
    msg = _make_msg(
        subject="Monthly Report",
        sender="alice@example.com",
        received=datetime.now() - timedelta(days=1),
        attachments=[att],
    )
    win32_mock = _build_win32_mock([msg])
    _mock_com(monkeypatch, win32_mock)

    attachments, diag = scan_for_attachments()

    assert diag.in_date_range == 1
    assert diag.excel_attachments_found == 1
    assert len(attachments) == 1
    result = attachments[0]
    assert result.subject == "Monthly Report"
    assert result.sender == "alice@example.com"
    assert result.filename == "report.xlsx"
    assert result.temp_path.exists()


def test_skips_non_excel_attachments(monkeypatch):
    """Non-Excel attachments (e.g. .pdf) are ignored."""
    msg = _make_msg(
        subject="Invoice",
        sender="bob@example.com",
        received=datetime.now() - timedelta(days=1),
        attachments=[_make_att("invoice.pdf")],
    )
    win32_mock = _build_win32_mock([msg])
    _mock_com(monkeypatch, win32_mock)

    attachments, diag = scan_for_attachments()

    assert attachments == []
    assert diag.skipped_non_excel == 1


def test_skips_messages_outside_date_window(monkeypatch):
    """Messages older than *days_back* are not included."""
    att = _make_att("old_data.xlsx")
    msg = _make_msg(
        subject="Old",
        sender="carol@example.com",
        received=datetime.now() - timedelta(days=60),
        attachments=[att],
    )
    win32_mock = _build_win32_mock([msg])
    _mock_com(monkeypatch, win32_mock)

    attachments, diag = scan_for_attachments(days_back=30)

    assert diag.skipped_too_old == 1
    assert diag.in_date_range == 0
    assert attachments == []


def test_subject_filter(monkeypatch):
    """Only messages whose subject matches the filter are returned."""
    att_match = _make_att("budget.xlsx")
    att_other = _make_att("other.xlsx")
    messages = [
        _make_msg("Q1 Budget", "dave@example.com",
                  datetime.now() - timedelta(days=1), [att_match]),
        _make_msg("Meeting Notes", "eve@example.com",
                  datetime.now() - timedelta(days=1), [att_other]),
    ]
    win32_mock = _build_win32_mock(messages)
    _mock_com(monkeypatch, win32_mock)

    attachments, diag = scan_for_attachments(subject_filter="budget")

    assert diag.skipped_subject_filter == 1
    assert len(attachments) == 1
    assert attachments[0].filename == "budget.xlsx"


def test_subfolder_not_found_raises(monkeypatch):
    """ValueError is raised when the requested subfolder does not exist."""
    sub = MagicMock()
    sub.Name = "Archive"

    folder = MagicMock()
    folder.Folders.Count = 1
    folder.Folders.Item.return_value = sub

    namespace = MagicMock()
    namespace.GetDefaultFolder.return_value = folder

    outlook_app = MagicMock()
    outlook_app.GetNamespace.return_value = namespace

    client_mod = MagicMock()
    client_mod.Dispatch.return_value = outlook_app

    win32_mod = MagicMock()
    win32_mod.client = client_mod

    monkeypatch.setitem(sys.modules, "win32com", win32_mod)
    monkeypatch.setitem(sys.modules, "win32com.client", win32_mod.client)
    monkeypatch.setitem(sys.modules, "pythoncom", MagicMock())

    with pytest.raises(ValueError, match="DoesNotExist"):
        scan_for_attachments(subfolder="DoesNotExist")


def test_subfolder_case_insensitive(monkeypatch):
    """Subfolder lookup succeeds regardless of case differences."""
    att = _make_att("report.xlsx")
    msg = _make_msg(
        subject="Report",
        sender="fred@example.com",
        received=datetime.now() - timedelta(days=1),
        attachments=[att],
    )

    sub_folder = MagicMock()
    sub_folder.Name = "Reports"          # capital R in Outlook
    sub_folder.Items = _FakeItems([msg])

    inbox = MagicMock()
    inbox.Folders.Count = 1
    inbox.Folders.Item.return_value = sub_folder

    namespace = MagicMock()
    namespace.GetDefaultFolder.return_value = inbox

    outlook_app = MagicMock()
    outlook_app.GetNamespace.return_value = namespace

    client_mod = MagicMock()
    client_mod.Dispatch.return_value = outlook_app

    win32_mod = MagicMock()
    win32_mod.client = client_mod

    monkeypatch.setitem(sys.modules, "win32com", win32_mod)
    monkeypatch.setitem(sys.modules, "win32com.client", win32_mod.client)
    monkeypatch.setitem(sys.modules, "pythoncom", MagicMock())

    attachments, diag = scan_for_attachments(subfolder="reports")  # lowercase

    assert len(attachments) == 1
    assert attachments[0].filename == "report.xlsx"


def test_xls_extension_accepted(monkeypatch):
    """.xls files (legacy Excel format) are also accepted."""
    att = _make_att("legacy.xls")
    msg = _make_msg(
        subject="Legacy File",
        sender="frank@example.com",
        received=datetime.now() - timedelta(days=1),
        attachments=[att],
    )
    win32_mock = _build_win32_mock([msg])
    _mock_com(monkeypatch, win32_mock)

    attachments, diag = scan_for_attachments()

    assert len(attachments) == 1
    assert attachments[0].filename == "legacy.xls"
