"""Populate logs, export cleaned CSV files, zip them, and email via Gmail.

Examples:
  python scripts/export_consolidated_logs_and_send_gmail.py --populate --send-email
  docker compose -f docker-compose.local.yml exec django \
    python scripts/export_consolidated_logs_and_send_gmail.py --populate --send-email

Environment variables for Gmail:
  GMAIL_SENDER_EMAIL=<your gmail address>
  GMAIL_APP_PASSWORD=<gmail app password>
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import smtplib
import sys
from collections import Counter
from datetime import UTC
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED
from zipfile import ZipFile
from typing import Iterable

Primary_EXPORT_HEADERS = [
    "log_id",
    "session_id",
    "user_id",
    "user_email",
    "action",
    "content",
    "payload",
    "catered",
    "method",
    "request",
    "response",
    "error_code",
]

Secondary_EXPORT_HEADERS = [
    "log_id",
    "content",
    "method",
    "request",
    "response",
    "error_code",
]

def configure_django() -> None:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    import django

    django.setup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate logs, export cleaned CSVs, zip them, and optionally send to Gmail"
    )
    parser.add_argument(
        "--output-dir",
        default="exported",
        help="Output directory for csv/zip artifacts (default: exported)",
    )
    parser.add_argument(
        "--populate",
        action="store_true",
        help="Populate database before export (defaults: 9000 Primary, 110000 Secondary)",
    )
    parser.add_argument(
        "--Primary-count",
        type=int,
        default=9000,
        help="Primary rows to generate when --populate is used (default: 9000)",
    )
    parser.add_argument(
        "--Secondary-count",
        type=int,
        default=110000,
        help="Secondary rows to generate when --populate is used (default: 110000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Bulk insert batch size when --populate is used (default: 1000)",
    )
    parser.add_argument(
        "--db-chunk-size",
        type=int,
        default=2000,
        help="Iterator chunk size when reading from DB (default: 2000)",
    )
    parser.add_argument(
        "--rows-per-csv",
        "--consolidated-chunk-rows",
        dest="rows_per_csv",
        type=int,
        default=32000,
        help="Rows per Primary/Secondary CSV chunk (default: 32000)",
    )
    parser.add_argument(
        "--max-zip-mb",
        type=float,
        default=25.0,
        help="Max zip size target for Gmail in MB (default: 25.0)",
    )
    parser.add_argument(
        "--single-zip",
        action="store_true",
        help="Try to send all CSV exports in one zip attachment",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send produced zip files by email",
    )
    parser.add_argument(
        "--recipient",
        default="kier.abiad@gmail.com",
        help="Recipient email (default: kier.abiad@gmail.com)",
    )
    parser.add_argument(
        "--sender-env",
        default="GMAIL_SENDER_EMAIL",
        help="Environment variable name containing Gmail sender address",
    )
    parser.add_argument(
        "--password-env",
        default="GMAIL_APP_PASSWORD",
        help="Environment variable name containing Gmail app password",
    )
    parser.add_argument(
        "--gmail-env-file",
        default=".envs/.local/.gmail",
        help="Optional local env file to load before sending email",
    )
    parser.add_argument(
        "--smtp-host",
        default="smtp.gmail.com",
        help="SMTP host (default: smtp.gmail.com)",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=587,
        help="SMTP port (default: 587)",
    )
    return parser.parse_args()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    text = str(value)
    # Keep a single-line CSV-friendly representation.
    return " ".join(text.split())


def normalize_error_code(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def populate_if_requested(args: argparse.Namespace) -> None:
    if not args.populate:
        return

    from django.core.management import call_command

    print(
        "Populating logs "
        f"(Primary={args.Primary_count}, Secondary={args.Secondary_count}, batch={args.batch_size})..."
    )
    call_command(
        "populate_logs",
        Primary_count=args.Primary_count,
        Secondary_count=args.Secondary_count,
        batch_size=args.batch_size,
    )


def open_csv_writer(path: Path, headers: list[str]) -> tuple[object, csv.DictWriter]:
    fp = path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fp, fieldnames=headers)
    writer.writeheader()
    return fp, writer


def run_Primary_logs(db_chunk_size: int) -> Iterable[object]:
    from logs.models import PrimaryLog

    return PrimaryLog.objects.select_related("user").order_by("id").iterator(
        chunk_size=db_chunk_size
    )


def run_Secondary_logs(db_chunk_size: int) -> Iterable[object]:
    from logs.models import SecondaryLog

    return SecondaryLog.objects.order_by("id").iterator(chunk_size=db_chunk_size)


def run_Primary_export(
    *,
    logs: Iterable[object],
    output_dir: Path,
    timestamp: str,
    rows_per_csv: int,
) -> tuple[list[Path], int, Counter, Counter]:
    Primary_paths: list[Path] = []
    Primary_part = 1
    Primary_rows_in_part = 0
    Primary_fp = None
    Primary_writer: csv.DictWriter | None = None
    by_error = Counter()
    by_action = Counter()
    count = 0

    def write_Primary_row(row: dict[str, str]) -> None:
        nonlocal Primary_fp, Primary_writer, Primary_rows_in_part, Primary_part
        if Primary_writer is None:
            part_path = output_dir / f"Primary_logs_cleaned_{timestamp}_part_{Primary_part:03d}.csv"
            Primary_fp, Primary_writer = open_csv_writer(part_path, Primary_EXPORT_HEADERS)
            Primary_paths.append(part_path)

        if Primary_rows_in_part >= rows_per_csv:
            assert Primary_fp is not None
            Primary_fp.close()
            Primary_part += 1
            Primary_rows_in_part = 0
            part_path = output_dir / f"Primary_logs_cleaned_{timestamp}_part_{Primary_part:03d}.csv"
            Primary_fp, Primary_writer = open_csv_writer(part_path, Primary_EXPORT_HEADERS)
            Primary_paths.append(part_path)

        assert Primary_writer is not None
        Primary_writer.writerow(row)
        Primary_rows_in_part += 1

    for log in logs:
        user_email = ""
        if log.user_id and getattr(log, "user", None):
            user_email = normalize_text(getattr(log.user, "email", ""))

        Primary_row = {
            "log_id": str(log.id),
            "session_id": "",
            "user_id": str(log.user_id or ""),
            "user_email": user_email,
            "action": normalize_text(log.action),
            "content": normalize_text(log.content),
            "payload": "",
            "catered": "",
            "method": "",
            "request": normalize_text(log.request),
            "response": normalize_text(log.response),
            "error_code": normalize_error_code(log.error_code),
        }

        write_Primary_row(Primary_row)
        count += 1
        by_error[("Primary", normalize_error_code(log.error_code))] += 1
        by_action[normalize_text(log.action)] += 1

    if Primary_fp is not None:
        Primary_fp.close()

    return Primary_paths, count, by_error, by_action


def run_Secondary_export(
    *,
    logs: Iterable[object],
    output_dir: Path,
    timestamp: str,
    rows_per_csv: int,
) -> tuple[list[Path], int, Counter, Counter]:
    Secondary_paths: list[Path] = []
    Secondary_part = 1
    Secondary_rows_in_part = 0
    Secondary_fp = None
    Secondary_writer: csv.DictWriter | None = None
    by_error = Counter()
    by_method = Counter()
    count = 0

    def write_Secondary_row(row: dict[str, str]) -> None:
        nonlocal Secondary_fp, Secondary_writer, Secondary_rows_in_part, Secondary_part
        if Secondary_writer is None:
            part_path = output_dir / f"Secondary_logs_cleaned_{timestamp}_part_{Secondary_part:03d}.csv"
            Secondary_fp, Secondary_writer = open_csv_writer(part_path, Secondary_EXPORT_HEADERS)
            Secondary_paths.append(part_path)

        if Secondary_rows_in_part >= rows_per_csv:
            assert Secondary_fp is not None
            Secondary_fp.close()
            Secondary_part += 1
            Secondary_rows_in_part = 0
            part_path = output_dir / f"Secondary_logs_cleaned_{timestamp}_part_{Secondary_part:03d}.csv"
            Secondary_fp, Secondary_writer = open_csv_writer(part_path, Secondary_EXPORT_HEADERS)
            Secondary_paths.append(part_path)

        assert Secondary_writer is not None
        Secondary_writer.writerow(row)
        Secondary_rows_in_part += 1

    for log in logs:
        Secondary_row = {
            "log_id": str(log.id),
            "content": normalize_text(log.content),
            "method": normalize_text(log.method),
            "request": normalize_text(log.request),
            "response": normalize_text(log.response),
            "error_code": normalize_error_code(log.error_code),
        }
        write_Secondary_row(Secondary_row)
        count += 1
        by_error[("Secondary", normalize_error_code(log.error_code))] += 1
        by_method[normalize_text(log.method)] += 1

    if Secondary_fp is not None:
        Secondary_fp.close()

    return Secondary_paths, count, by_error, by_method


def export_cleaned_data(
    output_dir: Path,
    db_chunk_size: int,
    rows_per_csv: int,
) -> tuple[list[Path], list[Path], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    Primary_paths, Primary_count, Primary_by_error, by_action = run_Primary_export(
        logs=run_Primary_logs(db_chunk_size),
        output_dir=output_dir,
        timestamp=timestamp,
        rows_per_csv=rows_per_csv,
    )
    Secondary_paths, Secondary_count, Secondary_by_error, by_method = run_Secondary_export(
        logs=run_Secondary_logs(db_chunk_size),
        output_dir=output_dir,
        timestamp=timestamp,
        rows_per_csv=rows_per_csv,
    )

    counters = {
        "Primary": Primary_count,
        "Secondary": Secondary_count,
    }
    by_error = Counter()
    by_error.update(Primary_by_error)
    by_error.update(Secondary_by_error)
    summary = {
        "timestamp": timestamp,
        "Primary_rows": counters["Primary"],
        "Secondary_rows": counters["Secondary"],
        "total_rows": counters["Primary"] + counters["Secondary"],
        "Primary_parts": len(Primary_paths),
        "Secondary_parts": len(Secondary_paths),
        "top_errors": by_error.most_common(5),
        "top_actions": by_action.most_common(5),
        "top_methods": by_method.most_common(5),
    }

    return Primary_paths, Secondary_paths, summary


def zip_for_email(
    csv_paths: list[Path],
    output_dir: Path,
    max_zip_bytes: int,
    *,
    single_zip: bool,
    timestamp: str,
) -> list[Path]:
    zip_paths: list[Path] = []

    if single_zip:
        zip_path = output_dir / f"logs_export_{timestamp}_all.zip"
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
            for csv_path in csv_paths:
                zf.write(csv_path, arcname=csv_path.name)

        size = zip_path.stat().st_size
        if size > max_zip_bytes:
            raise RuntimeError(
                f"Single zip {zip_path.name} is {size / (1024 * 1024):.2f}MB, over "
                f"limit {max_zip_bytes / (1024 * 1024):.2f}MB. Disable --single-zip "
                "to send multiple attachments."
            )
        return [zip_path]

    for csv_path in csv_paths:
        zip_path = output_dir / f"{csv_path.stem}.zip"
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(csv_path, arcname=csv_path.name)

        size = zip_path.stat().st_size
        if size > max_zip_bytes:
            raise RuntimeError(
                f"Zip {zip_path.name} is {size / (1024 * 1024):.2f}MB, over "
                f"limit {max_zip_bytes / (1024 * 1024):.2f}MB. "
                "Reduce --rows-per-csv and run again."
            )

        zip_paths.append(zip_path)

    return zip_paths


def _format_top_entries(entries: list[tuple[Any, int]], *, item_label: str) -> str:
    if not entries:
        return f"- Top {item_label}: none"

    lines = [f"- Top {item_label}:"]
    for key, count in entries:
        display_key = key
        if isinstance(key, tuple) and len(key) == 2:
            display_key = f"{key[0]}:{key[1]}"
        lines.append(f"  - {display_key}: {count}")
    return "\n".join(lines)


def build_email_body(
    *,
    summary: dict[str, Any],
    zip_file_name: str,
    part_index: int,
    total_parts: int,
) -> str:
    lines = [
        "Hi,",
        "",
        "This is the exported logs file.",
        f"Attachment: {zip_file_name}",
        f"Part: {part_index}/{total_parts}",
        "",
        "Summary:",
        f"- Primary rows: {summary['Primary_rows']}",
        f"- Secondary rows: {summary['Secondary_rows']}",
        f"- Total rows: {summary['total_rows']}",
        f"- Primary CSV parts: {summary['Primary_parts']}",
        f"- Secondary CSV parts: {summary['Secondary_parts']}",
        _format_top_entries(summary["top_errors"], item_label="error codes"),
        _format_top_entries(summary["top_actions"], item_label="Primary actions"),
        _format_top_entries(summary["top_methods"], item_label="Secondary methods"),
    ]
    return "\n".join(lines)


def send_email(
    zip_paths: list[Path],
    summary: dict[str, Any],
    recipient: str,
    sender: str,
    app_password: str,
    smtp_host: str,
    smtp_port: int,
) -> None:
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(sender, app_password)

            for idx, zip_path in enumerate(zip_paths, start=1):
                msg = EmailMessage()
                msg["Subject"] = (
                    "Mock Logs Export "
                    f"(cleaned+summary source-separated) part {idx}/{len(zip_paths)}"
                )
                msg["From"] = sender
                msg["To"] = recipient
                msg.set_content(
                    build_email_body(
                        summary=summary,
                        zip_file_name=zip_path.name,
                        part_index=idx,
                        total_parts=len(zip_paths),
                    )
                )

                payload = zip_path.read_bytes()
                msg.add_attachment(
                    payload,
                    maintype="application",
                    subtype="zip",
                    filename=zip_path.name,
                )

                server.send_message(msg)
                print(f"Sent email part {idx}/{len(zip_paths)}: {zip_path.name}")
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError(
            "Gmail authentication failed. Use a Google App Password, not your regular "
            "account password. Enable 2-Step Verification on the sender account, then "
            "generate a 16-character app password and put it in .envs/.local/.gmail."
        ) from error
    except socket.gaierror as error:
        raise RuntimeError(
            "SMTP hostname resolution failed. Check internet/DNS connectivity from the "
            "django container and verify --smtp-host (for Gmail use smtp.gmail.com)."
        ) from error


def load_env_file_if_exists(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    args = parse_args()
    configure_django()

    output_dir = Path(args.output_dir)
    max_zip_bytes = int(args.max_zip_mb * 1024 * 1024)

    if args.rows_per_csv <= 0:
        raise ValueError("--rows-per-csv must be greater than 0")

    populate_if_requested(args)

    Primary_paths, Secondary_paths, summary = export_cleaned_data(
        output_dir=output_dir,
        db_chunk_size=args.db_chunk_size,
        rows_per_csv=args.rows_per_csv,
    )

    zip_paths = zip_for_email(
        csv_paths=[*Primary_paths, *Secondary_paths],
        output_dir=output_dir,
        max_zip_bytes=max_zip_bytes,
        single_zip=args.single_zip,
        timestamp=summary["timestamp"],
    )

    print(f"Primary CSV parts: {len(Primary_paths)}")
    print(f"Secondary CSV parts: {len(Secondary_paths)}")
    print(f"Prepared zip parts for email: {len(zip_paths)}")
    print(
        "Row totals -> "
        f"Primary: {summary['Primary_rows']}, Secondary: {summary['Secondary_rows']}"
    )

    if not args.send_email:
        print("--send-email not provided; skipping email send.")
        return

    load_env_file_if_exists(args.gmail_env_file)

    sender = os.getenv(args.sender_env, "").strip()
    app_password = os.getenv(args.password_env, "").strip()

    if not sender:
        raise RuntimeError(
            f"Missing sender email in environment variable: {args.sender_env}"
        )
    if not app_password:
        raise RuntimeError(
            f"Missing Gmail app password in environment variable: {args.password_env}"
        )

    send_email(
        zip_paths=zip_paths,
        summary=summary,
        recipient=args.recipient,
        sender=sender,
        app_password=app_password,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
    )


if __name__ == "__main__":
    main()
