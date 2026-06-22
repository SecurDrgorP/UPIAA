"""
nodes/excel_document.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊  Excel Document — Read or Create .xlsx Files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT THIS NODE DOES
───────────────────
Reads or creates Microsoft Excel (.xlsx) spreadsheets using the openpyxl
library. Operates in two modes:

  • **read**  — Opens an existing .xlsx file and returns all rows as a list
                of dicts (using the first row as column headers).
  • **write** — Creates a new .xlsx file from a list of row arrays or dicts
                and returns the output file path.

QUICK START
───────────
  1. Select a mode: "read" or "write".
  2. For read:  provide the file_path to an existing .xlsx file.
  3. For write: provide the output_path and rows data (JSON array).
  4. All text fields support {{expression}} interpolation.

OUTPUT FIELDS
─────────────
  mode            The mode used ("read" or "write")
  rows            (read mode) List of dicts, one per data row
  row_count       (read mode) Number of data rows
  headers         (read mode) List of column header strings
  file_path       (write mode) The path of the created spreadsheet
  rows_written    (write mode) Number of rows written
"""

import asyncio
import json
from typing import Any

from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping


@register_action(
    type="excel_document",
    label="Excel Document",
    icon="📊",
    description="Read or create .xlsx spreadsheets using openpyxl.",
    category="Actions",
    config_schema={
        "mode": {
            "type": "select",
            "label": "Mode",
            "required": True,
            "default": "read",
            "options": [
                {"value": "read", "label": "Read existing file"},
                {"value": "write", "label": "Write / Create file"},
            ],
            "description": "Whether to read an existing spreadsheet or create a new one.",
        },
        "file_path": {
            "type": "file",
            "label": "File Path",
            "required": True,
            "template": True,
            "placeholder": "/app/uploads/report.xlsx",
            "visibleWhen": {"mode": "read"},
            "description": "Path to the .xlsx file to read. Supports {{expression}} interpolation.",
        },
        "sheet_name": {
            "type": "string",
            "label": "Sheet Name (optional)",
            "required": False,
            "template": True,
            "placeholder": "Sheet1",
            "description": "Name of the sheet to read/write. Leave blank for the active sheet (read) or default (write).",
        },
        "output_path": {
            "type": "string",
            "label": "Output Path",
            "required": True,
            "template": True,
            "placeholder": "/app/uploads/output.xlsx",
            "visibleWhen": {"mode": "write"},
            "description": "Path where the new .xlsx file will be saved. Supports {{expression}} interpolation.",
        },
        "rows_json": {
            "type": "string",
            "label": "Rows (JSON)",
            "multiline": True,
            "template": True,
            "placeholder": '[{"name": "Alice", "score": 95}, {"name": "Bob", "score": 87}]',
            "visibleWhen": {"mode": "write"},
            "description": (
                "JSON array of rows. Each row can be a dict (keys become headers) "
                "or an array of values. Supports {{expression}} interpolation."
            ),
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"data": "rows"}',
        },
    },
    example_output={
        "mode": "read",
        "rows": [{"name": "Alice", "score": 95}],
        "row_count": 1,
        "headers": ["name", "score"],
    },
)
async def handle_excel_document(
    config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:

    # ── Validate openpyxl availability ─────────────────────────────────────────
    try:
        import openpyxl  # noqa: F811
    except ImportError:
        return {
            "error": "openpyxl is not installed. Run: pip install openpyxl",
            "__input_error__": True,
        }

    mode = config.get("mode", "read")
    sheet_name = resolve_template(config.get("sheet_name", ""), input_data).strip() or None

    # ── READ mode ──────────────────────────────────────────────────────────────
    if mode == "read":
        file_path = resolve_template(config.get("file_path", ""), input_data).strip()
        if not file_path:
            return {"error": "missing field: file_path", "__input_error__": True}

        if file_path.startswith("/data/"):
            file_path = "/app/uploads/" + file_path[6:]
        elif "/" not in file_path and "\\" not in file_path:
            file_path = "/app/uploads/" + file_path

        try:
            def _read():
                wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
                ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
                rows_iter = ws.iter_rows(values_only=True)

                # First row = headers
                headers_raw = next(rows_iter, None)
                if headers_raw is None:
                    return [], [], 0

                headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(headers_raw)]
                data_rows = []
                for row in rows_iter:
                    data_rows.append({headers[i]: (cell if cell is not None else "") for i, cell in enumerate(row)})
                wb.close()
                return headers, data_rows, len(data_rows)

            headers, data_rows, row_count = await asyncio.to_thread(_read)

            result = {
                "mode": "read",
                "rows": data_rows,
                "row_count": row_count,
                "headers": headers,
                "file_path": file_path,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        except FileNotFoundError:
            return {"error": f"File not found: {file_path}", "__input_error__": True}
        except Exception as e:
            return {"error": f"Failed to read spreadsheet: {e}", "__service_error__": True}

    # ── WRITE mode ─────────────────────────────────────────────────────────────
    elif mode == "write":
        output_path = resolve_template(config.get("output_path", ""), input_data).strip()
        rows_raw = resolve_template(config.get("rows_json", ""), input_data).strip()

        if not output_path:
            return {"error": "missing field: output_path", "__input_error__": True}
        if not rows_raw:
            return {"error": "missing field: rows_json", "__input_error__": True}

        if output_path.startswith("/data/"):
            output_path = "/app/uploads/" + output_path[6:]
        elif "/" not in output_path and "\\" not in output_path:
            output_path = "/app/uploads/" + output_path

        import os
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        try:
            rows = json.loads(rows_raw)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON in rows_json: {e}", "__input_error__": True}

        if not isinstance(rows, list) or len(rows) == 0:
            return {"error": "rows_json must be a non-empty JSON array", "__input_error__": True}

        try:
            def _write():
                wb = openpyxl.Workbook()
                ws = wb.active
                if sheet_name:
                    ws.title = sheet_name

                # Determine if rows are dicts or arrays
                first = rows[0]
                if isinstance(first, dict):
                    headers = list(first.keys())
                    ws.append(headers)
                    for row in rows:
                        ws.append([row.get(h, "") for h in headers])
                else:
                    for row in rows:
                        ws.append(list(row) if isinstance(row, (list, tuple)) else [row])

                wb.save(output_path)
                wb.close()
                return len(rows)

            rows_written = await asyncio.to_thread(_write)

            result = {
                "mode": "write",
                "file_path": output_path,
                "rows_written": rows_written,
            }
            return apply_output_mapping(result, config.get("output_mapping", ""))

        except Exception as e:
            return {"error": f"Failed to write spreadsheet: {e}", "__service_error__": True}

    else:
        return {"error": f"Unknown mode: {mode}", "__input_error__": True}
