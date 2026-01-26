"""RFQ Excel export service.

Creates a new filled copy of an RFQ Excel template, writing values derived from
`/api/v1/rfq/autofill` into the appropriate columns.

Important: never modify the template file in-place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side


# Standard formatting constants
STANDARD_FONT = Font(name="Calibri", size=11)
STANDARD_FONT_BOLD = Font(name="Calibri", size=11, bold=True)
STANDARD_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
BOLD_BORDER = Border(
    left=Side(style="medium"),
    right=Side(style="medium"),
    top=Side(style="medium"),
    bottom=Side(style="medium"),
)


@dataclass(frozen=True)
class ExcelWriteResult:
    output_path: Path
    sheet_name: str
    header_row: int
    row_written: int


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _guess_header_row(ws, max_scan_rows: int = 20) -> int:
    """Pick the row with the most non-empty cells (early rows)."""
    best_row = 1
    best_cnt = -1
    maxr = min(max_scan_rows, ws.max_row or 1)
    for r in range(1, maxr + 1):
        cnt = 0
        for c in range(1, (ws.max_column or 1) + 1):
            v = ws.cell(r, c).value
            if v not in (None, ""):
                cnt += 1
        if cnt > best_cnt:
            best_cnt = cnt
            best_row = r
    return best_row


def _build_header_map(ws, header_row: int) -> Dict[str, int]:
    headers: Dict[str, int] = {}
    for c in range(1, (ws.max_column or 1) + 1):
        v = ws.cell(header_row, c).value
        if v in (None, ""):
            continue
        key = _norm(v)
        if key and key not in headers:
            headers[key] = c
    return headers


def _find_or_append_part_row(ws, header_row: int, part_no: str, part_col: int, srno_col: Optional[int]) -> int:
    target = _norm(part_no)
    # Scan existing rows
    for r in range(header_row + 1, (ws.max_row or header_row) + 1):
        v = ws.cell(r, part_col).value
        if _norm(v) == target:
            return r

    # Append new row
    new_r = (ws.max_row or header_row) + 1
    if srno_col:
        # sr no: try to increment from previous numeric
        prev = ws.cell(new_r - 1, srno_col).value
        try:
            ws.cell(new_r, srno_col).value = int(prev) + 1  # type: ignore[arg-type]
        except Exception:
            ws.cell(new_r, srno_col).value = new_r - header_row
    ws.cell(new_r, part_col).value = part_no
    return new_r


def _prepare_comparison_rows(
    ws,
    header_row: int,
    part_no: str,
    part_col: int,
    srno_col: Optional[int],
) -> int:
    """Ensure a comparison row exists below the base row and return the write row."""
    base_row = _find_or_append_part_row(ws, header_row, part_no, part_col, srno_col)
    write_row = base_row + 1
    ws.insert_rows(write_row)
    if srno_col:
        ws.cell(write_row, srno_col).value = None
    ws.cell(write_row, part_col).value = part_no
    return write_row


def write_autofill_to_rfq_template(
    *,
    template_path: Path,
    output_path: Path,
    part_no: str,
    autofill_response: Dict[str, Any],
    cost_inputs: Optional[Dict[str, Any]] = None,
    sheet_name: str = "RFQ Details",
) -> ExcelWriteResult:
    """Write autofill values into a copy of the RFQ template.

    `autofill_response` should be the JSON-serializable dict returned by `/rfq/autofill`.
    """
    if not template_path.exists() or not template_path.is_file():
        raise FileNotFoundError(f"Template not found: {template_path}")

    # Load template (never modify on disk in-place)
    wb = openpyxl.load_workbook(template_path, data_only=False)
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        used_sheet = sheet_name
    else:
        ws = wb[wb.sheetnames[0]]
        used_sheet = wb.sheetnames[0]

    header_row = _guess_header_row(ws)
    header_map = _build_header_map(ws, header_row=header_row)

    # Required columns by reference sheet
    part_col = header_map.get(_norm("Part No"))
    if not part_col:
        raise ValueError("Could not find 'Part No' column in template")

    srno_col = header_map.get(_norm("Sr.No"))
    row = _prepare_comparison_rows(ws, header_row=header_row, part_no=part_no, part_col=part_col, srno_col=srno_col)

    fields = (autofill_response or {}).get("fields") or {}
    estimate = (autofill_response or {}).get("estimate") or None

    def _shift_header_map(start_col: int, delta: int) -> None:
        for k, v in list(header_map.items()):
            if v >= start_col:
                header_map[k] = v + delta

    def ensure_header_after(header: str, after_header: str) -> int:
        key = _norm(header)
        col = header_map.get(key)
        if col:
            return col
        after_col = header_map.get(_norm(after_header))
        if not after_col:
            raise ValueError(f"Could not find '{after_header}' column in template")
        insert_at = after_col + 1
        ws.insert_cols(insert_at)
        _shift_header_map(insert_at, 1)
        header_cell = ws.cell(header_row, insert_at)
        header_cell.value = header
        # Apply standard formatting to new header cell
        header_cell.font = STANDARD_FONT_BOLD
        header_cell.alignment = STANDARD_ALIGNMENT
        header_cell.border = BOLD_BORDER
        header_map[key] = insert_at
        return insert_at

    def _round_value(value: Any, decimals: int = 2) -> Any:
        """Round numeric values to specified decimal places."""
        if value is None:
            return None
        try:
            num = float(value)
            return round(num, decimals)
        except (TypeError, ValueError):
            return value

    def _apply_cell_formatting(cell) -> None:
        """Apply standard formatting to a cell."""
        cell.font = STANDARD_FONT
        cell.alignment = STANDARD_ALIGNMENT
        cell.border = BOLD_BORDER

    def set_by_header(header: str, value: Any, decimals: int = 2) -> None:
        col = header_map.get(_norm(header))
        if not col:
            return
        rounded_value = _round_value(value, decimals)
        cell = ws.cell(row, col)
        cell.value = rounded_value
        _apply_cell_formatting(cell)

    def fv(field_key: str, decimals: int = 2) -> Optional[float]:
        try:
            v = fields.get(field_key, {}).get("value")
            if v is None:
                return None
            return round(float(v), decimals)
        except Exception:
            return None

    # Dimensions (Inches)
    set_by_header("Finish OD (Inch)", fv("finish_od_in"))
    set_by_header("Finish ID (Inch)", fv("finish_id_in"))
    set_by_header("Finish Length (Inch)", fv("finish_len_in"))
    set_by_header("RM OD (Inch)", fv("rm_od_in"))
    set_by_header("RM ID (Inch)", fv("rm_id_in"))
    set_by_header("Length (Inch)", fv("rm_len_in"))
    
    # Dimensions (MM)
    set_by_header("Finish OD (MM)", fv("finish_od_mm"))
    set_by_header("Finish ID (MM)", fv("finish_id_mm"))
    set_by_header("Finish Length (MM)", fv("finish_len_mm"))

    # Optional: cost inputs (if provided)
    if isinstance(cost_inputs, dict):
        if cost_inputs.get("rm_rate_per_kg") is not None:
            set_by_header("RM Rate", float(cost_inputs["rm_rate_per_kg"]))
        if cost_inputs.get("exchange_rate") is not None:
            set_by_header("Exchange Rate", float(cost_inputs["exchange_rate"]))
        if cost_inputs.get("currency") is not None:
            set_by_header("Currency", str(cost_inputs["currency"]))

    # Optional: estimate block (if present)
    if isinstance(estimate, dict):
        def ev(key: str, decimals: int = 2) -> Optional[float]:
            try:
                v = estimate.get(key, {}).get("value")
                if v is None:
                    return None
                return round(float(v), decimals)
            except Exception:
                return None

        # Weight and material
        set_by_header("RM Weight Kg", ev("rm_weight_kg"))
        set_by_header("Material Cost", ev("material_cost"))
        set_by_header("Roughing Cost", ev("roughing_cost"))
        
        # Turning
        set_by_header("Turning Time In Min", ev("turning_minutes"))
        set_by_header("Turning cost", ev("turning_cost"))
        
        # VMC (includes drilling + milling time)
        set_by_header("VMC Time In Min", ev("vmc_minutes"))
        set_by_header("VMC cost", ev("vmc_cost"))
        
        # Ensure drilling/milling columns sit between Turning cost and VMC cost
        ensure_header_after("Drilling Time In Min", "Turning cost")
        ensure_header_after("Drilling Cost", "Drilling Time In Min")
        ensure_header_after("Milling Time In Min", "Drilling Cost")
        ensure_header_after("Milling Cost", "Milling Time In Min")
        set_by_header("Drilling Time In Min", ev("drilling_minutes"))
        set_by_header("Drilling Cost", ev("drilling_cost"))
        set_by_header("Milling Time In Min", ev("milling_minutes"))
        set_by_header("Milling Cost", ev("milling_cost"))
        
        # Other costs
        set_by_header("Special Process Cost", ev("special_process_cost"))
        set_by_header("Others", ev("others_cost"))
        set_by_header("Inspection and testing Cost", ev("inspection_cost"))
        
        # Subtotal and markups
        set_by_header("Sub Total", ev("subtotal"))
        set_by_header("P&F", ev("pf_cost"))
        set_by_header("OH & Profit", ev("oh_profit"))
        set_by_header("Rejection Cost", ev("rejection_cost"))
        
        # Final prices
        set_by_header("Price/Each In INR", ev("price_each_inr"))
        set_by_header("Price/Each In Currency", ev("price_each_currency"))
        
        # Contribution
        set_by_header("RM Contribution %", ev("rm_contribution_pct"))

    # Apply formatting to header row (bold, centered, bordered)
    for col in range(1, (ws.max_column or 1) + 1):
        header_cell = ws.cell(header_row, col)
        header_cell.font = STANDARD_FONT_BOLD
        header_cell.alignment = STANDARD_ALIGNMENT
        header_cell.border = BOLD_BORDER

    # Highlight the newly written row for easy comparison
    highlight = PatternFill(start_color="E6CCFF", end_color="E6CCFF", fill_type="solid")
    for col in range(1, (ws.max_column or 1) + 1):
        cell = ws.cell(row, col)
        cell.fill = highlight
        # Ensure all cells in the row have standard formatting
        cell.font = STANDARD_FONT_BOLD
        cell.alignment = STANDARD_ALIGNMENT
        cell.border = BOLD_BORDER

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    return ExcelWriteResult(
        output_path=output_path,
        sheet_name=used_sheet,
        header_row=header_row,
        row_written=row,
    )





