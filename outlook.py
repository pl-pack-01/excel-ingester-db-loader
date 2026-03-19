"""Pull Excel attachments from the local Outlook application via COM automation.

Requires pywin32 and a locally installed Outlook desktop client.
Install with:  pip install pywin32
"""

import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

EXCEL_EXTENSIONS = {".xlsx", ".xls"}


@dataclass
class OutlookAttachment:
    """Metadata and a local temp-file path for a single email attachment."""

    subject: str
    sender: str
    received: str   # ISO-8601-style string, e.g. "2026-03-01 09:23:11"
    filename: str
    temp_path: Path  # attachment already saved here; caller owns deletion


@dataclass
class ScanDiagnostics:
    """Step-by-step counts and any per-message errors from a scan."""

    folder_name: str = ""
    date_from: str = ""
    date_to: str = ""
    total_items: int = 0        # folder.Items.Count
    skipped_non_mail: int = 0   # items with no ReceivedTime (tasks, etc.)
    skipped_too_old: int = 0    # before the cutoff
    in_date_range: int = 0      # passed the date filter
    skipped_no_attachment: int = 0
    skipped_subject_filter: int = 0
    skipped_non_excel: int = 0
    excel_attachments_found: int = 0
    errors: list[str] = field(default_factory=list)  # per-message error strings


def _find_subfolder(parent_folder, name: str):
    """Case-insensitive child-folder lookup under *parent_folder*.

    Lists available folder names in the error to make typos easy to spot.
    """
    available = []
    for i in range(1, parent_folder.Folders.Count + 1):
        f = parent_folder.Folders.Item(i)
        available.append(f.Name)
        if f.Name.lower() == name.lower():
            return f
    available_str = ", ".join(repr(n) for n in available) if available else "(none)"
    raise ValueError(
        f"Subfolder {name!r} not found under Inbox. "
        f"Available folders: {available_str}"
    )


def scan_for_attachments(
    subfolder: str = "",
    days_back: int = 30,
    subject_filter: str = "",
) -> tuple[list[OutlookAttachment], ScanDiagnostics]:
    """Scan the Outlook Inbox (or a named subfolder) for Excel attachments.

    Parameters
    ----------
    subfolder:
        Name of a direct child folder under Inbox to scan.
        Leave empty to scan the Inbox itself.
    days_back:
        How many calendar days back to look. Default 30.
    subject_filter:
        If non-empty, only include messages whose subject contains this
        string (case-insensitive).

    Returns
    -------
    (attachments, diagnostics)
        attachments  — list of OutlookAttachment instances, each already
                       saved to a temp file; caller owns deletion.
        diagnostics  — ScanDiagnostics with per-stage counts and any errors.

    Raises
    ------
    RuntimeError  — pywin32 is not installed.
    ValueError    — the requested subfolder does not exist under Inbox.
    """
    try:
        import pythoncom        # noqa: PLC0415
        import win32com.client  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 is required to connect to Outlook. "
            "Install it with:  pip install pywin32"
        ) from exc

    # CoInitialize must be called on every thread that touches COM.
    # Streamlit reruns can land on different threads, so we call it
    # unconditionally and balance it with CoUninitialize in the finally block.
    pythoncom.CoInitialize()
    try:
        return _do_scan(subfolder=subfolder, days_back=days_back,
                        subject_filter=subject_filter,
                        win32com=win32com)
    finally:
        pythoncom.CoUninitialize()


def _do_scan(subfolder, days_back, subject_filter, win32com) -> tuple[list[OutlookAttachment], ScanDiagnostics]:
    """Inner implementation — runs inside a CoInitialize/CoUninitialize guard."""
    outlook = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")

    folder = namespace.GetDefaultFolder(6)  # 6 = olFolderInbox
    if subfolder.strip():
        folder = _find_subfolder(folder, subfolder.strip())

    cutoff = datetime.now() - timedelta(days=days_back)
    now = datetime.now()

    diag = ScanDiagnostics(
        folder_name=folder.Name,
        date_from=cutoff.strftime("%Y-%m-%d %H:%M"),
        date_to=now.strftime("%Y-%m-%d %H:%M"),
        total_items=folder.Items.Count,
    )

    results: list[OutlookAttachment] = []

    for message in folder.Items:
        # ── Stage 1: get ReceivedTime — skips tasks, meeting requests, etc. ──
        try:
            rt = message.ReceivedTime
            received_dt = datetime(rt.year, rt.month, rt.day,
                                   rt.hour, rt.minute, rt.second)
        except Exception as exc:
            diag.skipped_non_mail += 1
            diag.errors.append(f"[non-mail item] {exc}")
            continue

        # ── Stage 2: date filter ─────────────────────────────────────────────
        if received_dt < cutoff:
            diag.skipped_too_old += 1
            continue

        diag.in_date_range += 1

        # ── Stage 3: must have attachments ───────────────────────────────────
        try:
            att_count = message.Attachments.Count
        except Exception as exc:
            diag.errors.append(
                f"[{received_dt}] Could not read Attachments.Count: {exc}"
            )
            continue

        if att_count == 0:
            diag.skipped_no_attachment += 1
            continue

        # ── Stage 4: subject filter ──────────────────────────────────────────
        try:
            subject = getattr(message, "Subject", "") or ""
        except Exception:
            subject = ""

        if subject_filter and subject_filter.lower() not in subject.lower():
            diag.skipped_subject_filter += 1
            continue

        # ── Stage 5: iterate attachments ─────────────────────────────────────
        try:
            sender = getattr(message, "SenderName", "") or ""
            received_str = received_dt.strftime("%Y-%m-%d %H:%M:%S")

            for att in message.Attachments:
                try:
                    ext = Path(att.FileName).suffix.lower()
                    if ext not in EXCEL_EXTENSIONS:
                        diag.skipped_non_excel += 1
                        continue

                    tmp = tempfile.NamedTemporaryFile(
                        suffix=ext, delete=False
                    )
                    tmp.close()
                    att.SaveAsFile(tmp.name)

                    results.append(
                        OutlookAttachment(
                            subject=subject,
                            sender=sender,
                            received=received_str,
                            filename=att.FileName,
                            temp_path=Path(tmp.name),
                        )
                    )
                    diag.excel_attachments_found += 1

                except Exception as exc:
                    diag.errors.append(
                        f"[{received_dt}] Error saving attachment: {exc}"
                    )

        except Exception as exc:
            diag.errors.append(
                f"[{received_dt}] Error iterating attachments: {exc}"
            )

    return results, diag
