"""Export Primary logs to CSV.

Usage examples:
  python scripts/export_Primary_logs.py
  python scripts/export_Primary_logs.py --output exports/Primary_logs.csv --limit 50000
  docker compose -f docker-compose.local.yml exec django python scripts/export_Primary_logs.py
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path


def configure_django() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.append(str(project_root / "mock_logs"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    import django

    django.setup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Primary logs to CSV")
    parser.add_argument(
        "--output",
        default="",
        help="Output CSV file path (default: exports/Primary_logs_<timestamp>.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of rows to export",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=0,
        help="Only export rows with id >= start-id",
    )
    parser.add_argument(
        "--end-id",
        type=int,
        default=0,
        help="Only export rows with id <= end-id",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2000,
        help="Iterator chunk size for database reads",
    )
    return parser.parse_args()


def resolve_output_path(raw_output: str) -> Path:
    if raw_output:
        output_path = Path(raw_output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("exports") / f"Primary_logs_{timestamp}.csv"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def export_Primary_logs(
    output_path: Path,
    *,
    limit: int,
    start_id: int,
    end_id: int,
    chunk_size: int,
) -> int:
    from logs.models import PrimaryLog

    queryset = PrimaryLog.objects.all().order_by("id")

    if start_id > 0:
        queryset = queryset.filter(id__gte=start_id)
    if end_id > 0:
        queryset = queryset.filter(id__lte=end_id)
    if limit > 0:
        queryset = queryset[:limit]

    rows = queryset.values_list(
        "id",
        "action",
        "content",
        "request",
        "response",
        "error_code",
        "user_id",
        "created_at",
    )

    exported = 0
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "id",
                "action",
                "content",
                "request",
                "response",
                "error_code",
                "user_id",
                "created_at",
            ]
        )

        for row in rows.iterator(chunk_size=chunk_size):
            row_list = list(row)
            created_at = row_list[-1]
            row_list[-1] = created_at.isoformat() if created_at else ""
            writer.writerow(row_list)
            exported += 1

            if exported % 10000 == 0:
                print(f"Exported {exported} rows...")

    return exported


def main() -> None:
    args = parse_args()
    output_path = resolve_output_path(args.output)

    configure_django()

    total = export_Primary_logs(
        output_path,
        limit=args.limit,
        start_id=args.start_id,
        end_id=args.end_id,
        chunk_size=args.chunk_size,
    )

    print(f"Done. Exported {total} Primary logs to: {output_path}")


if __name__ == "__main__":
    main()
