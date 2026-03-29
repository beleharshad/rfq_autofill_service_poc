"""Vendor-quote (Excel-like) field extraction from uploaded job PDFs.

Goal:
- Deterministic, explainable extraction of key RFQ fields from the *PDF drawing*,
  not from 3D geometry inference.
- Reuse existing job artifacts:
  - outputs/pdf_pages/page_{n}.png (rendered at 300 DPI)
  - outputs/auto_detect_results.json (best_view with page + view_index)
  - outputs/pdf_views/page_{n}_views.json (bbox_pixels for cropping)
"""

from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    _CV2_AVAILABLE = False
    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]

from app.storage.file_storage import FileStorage


@dataclass(frozen=True)
class ExtractedValue:
    value: Optional[str]
    confidence: float
    source: str


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def _to_float(s: str) -> Optional[float]:
    try:
        return float(str(s).strip())
    except Exception:
        return None


def _mm_to_in(mm: float) -> float:
    return mm / 25.4


EXTRACTOR_VERSION = "v2026-01-13-odlen-v2"


def _pil_imread_bgr(path: str):
    """Load image as BGR numpy array using PIL (cv2 fallback when libGL unavailable)."""
    try:
        import numpy as _np
        from PIL import Image as _PILImage
        img = _PILImage.open(str(path)).convert("RGB")
        arr = _np.array(img, dtype=_np.uint8)
        return arr[:, :, ::-1].copy()  # RGB → BGR
    except Exception:
        return None


class VendorQuoteExtractionService:
    def __init__(self) -> None:
        self.fs = FileStorage()

    def _ocr_tokens_tesseract(self, image_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        OCR tokens with positions using Tesseract (more reliable than EasyOCR here on Windows).
        Returns list of: {text, conf, x, y, w, h, line_key}
        """
        import pytesseract  # type: ignore

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB) if _CV2_AVAILABLE else image_bgr[:, :, ::-1]
        # PSM 6: assume a block of text; good for engineering drawings.
        config = "--psm 6"
        data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT, config=config)
        n = len(data.get("text", []))
        out: List[Dict[str, Any]] = []
        for i in range(n):
            txt = str(data["text"][i]).strip()
            if not txt:
                continue
            conf_raw = data.get("conf", [None] * n)[i]
            try:
                conf = float(conf_raw)
                conf01 = _clamp01(conf / 100.0) if conf >= 0 else 0.0
            except Exception:
                conf01 = 0.0

            try:
                x = int(data.get("left", [0] * n)[i])
                y = int(data.get("top", [0] * n)[i])
                w = int(data.get("width", [0] * n)[i])
                h = int(data.get("height", [0] * n)[i])
            except Exception:
                x, y, w, h = 0, 0, 0, 0

            try:
                line_key = (
                    int(data.get("block_num", [0] * n)[i]),
                    int(data.get("par_num", [0] * n)[i]),
                    int(data.get("line_num", [0] * n)[i]),
                )
            except Exception:
                line_key = (0, 0, 0)

            out.append(
                {
                    "text": txt,
                    "conf": conf01,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "line_key": line_key,
                }
            )
        return out

    def _normalize_numeric_text(self, s: str) -> str:
        """
        Normalize common OCR confusions for numeric parsing (I->1, O->0, etc).
        """
        t = s.upper().replace(",", ".")
        t = t.replace("Ø", "DIA ").replace("∅", "DIA ")
        t = re.sub(r"(?<=\d)I|I(?=\d)|(?<=\.)I|I(?=\.)", "1", t)
        t = re.sub(r"(?<=\d)O|O(?=\d)|(?<=\.)O|O(?=\.)", "0", t)
        t = re.sub(r"(?<=\d)S|S(?=\d)|(?<=\.)S|S(?=\.)", "5", t)
        t = re.sub(r"(?<=\d)B|B(?=\d)|(?<=\.)B|B(?=\.)", "8", t)
        # DI.7I -> DIA 1.71 (seen in your OCR preview)
        t = t.replace("DI.", "DIA ")
        return t

    def _extract_dims_from_best_view_callouts(self, crop_bgr: np.ndarray) -> Tuple[Dict[str, ExtractedValue], Dict[str, Any]]:
        """
        Option A: dedicated callout detector for Finish MAX OD (turned body) + MAX LENGTH.

        Uses only the turned-view crop (best_view_crop) to avoid title-block noise.
        The key is to ignore:
        - SCALE lines
        - thread specs (UNC, -2B)
        - radius callouts (R.xx)
        - count prefixes (4X, 8X)
        """
        h, w = crop_bgr.shape[:2]

        # Use sub-regions to avoid picking garbage from notes/title block that sometimes sneak into best_view_crop.
        def crop_frac(img: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
            xa = max(0, min(int(w * x0), w - 1))
            xb = max(0, min(int(w * x1), w))
            ya = max(0, min(int(h * y0), h - 1))
            yb = max(0, min(int(h * y1), h))
            if xb <= xa:
                xb = min(w, xa + 1)
            if yb <= ya:
                yb = min(h, ya + 1)
            return img[ya:yb, xa:xb]

        # OD callouts can live in multiple views (profile + section). Use two OD regions:
        # - top-right profile view band
        # - bottom-right section view band
        od_region_top = crop_frac(crop_bgr, 0.52, 0.05, 1.00, 0.48)
        od_region_bottom = crop_frac(crop_bgr, 0.50, 0.52, 1.00, 1.00)
        # Overall length ladder location varies across drawings; use a wider band to be more generic.
        # Wider + taller band to catch the overall-length callout in different drawing layouts.
        len_region = crop_frac(crop_bgr, 0.12, 0.06, 0.98, 0.80)

        def preprocess(img_bgr: np.ndarray) -> np.ndarray:
            if not _CV2_AVAILABLE:
                return img_bgr  # cv2 unavailable: skip preprocessing, pass raw pixels
            # Upscale aggressively; engineering drawing dims are tiny at 300 DPI renders.
            try:
                img_bgr = cv2.resize(img_bgr, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
            except Exception:
                pass
            g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            g = cv2.GaussianBlur(g, (3, 3), 0)
            g = cv2.convertScaleAbs(g, alpha=1.8, beta=0)
            # Binary via Otsu improves digit recognition for thin strokes.
            try:
                _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            except Exception:
                pass
            return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

        od_tokens_top = self._ocr_tokens_tesseract(preprocess(od_region_top))
        od_tokens_bottom = self._ocr_tokens_tesseract(preprocess(od_region_bottom))
        len_tokens = self._ocr_tokens_tesseract(preprocess(len_region))

        def group_lines(tokens: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
            by: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
            for t in tokens:
                by.setdefault(t["line_key"], []).append(t)
            out: List[Tuple[str, float]] = []
            for _, toks in by.items():
                toks = sorted(toks, key=lambda z: z["x"])
                text = " ".join(self._normalize_numeric_text(z["text"]) for z in toks).strip()
                confs = [float(z["conf"]) for z in toks if float(z["conf"]) > 0]
                conf = _clamp01(sum(confs) / len(confs)) if confs else 0.55
                out.append((text, conf))
            return out

        # Keep line grouping separate per region to avoid line_key collisions across different crops.
        od_lines = group_lines(od_tokens_top) + group_lines(od_tokens_bottom)
        len_lines = group_lines(len_tokens)

        num_re = re.compile(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)(?![A-Z0-9])")

        def is_noise_line(line: str) -> bool:
            t = f" {str(line).upper()} "
            if "SCALE" in t:
                return True
            # Engineering change / revision tables often contain 4-digit "ref no" values like 9044 which must not be treated as dimensions.
            if "REF" in t or "DATE" in t or "REV" in t or "DESCRIPTION" in t or "CHANGE" in t:
                return True
            if "ADDED" in t or "REMOVED" in t or "WAS" in t or "PREPRODUCTION" in t:
                return True
            if "THRU" in t or "THROUGH" in t or "FULL THREAD" in t:
                return True
            if "B.C" in t or " B C" in t or "BOLT" in t or "CIRCLE" in t:
                return True
            if "UNC" in t or "UN-" in t or "-2B" in t or "THREAD" in t:
                return True
            # Radius/chamfer callouts (R05, R.05, 2X R.03, etc)
            if re.search(r"\bR\s*\d", t) or re.search(r"\bR\.\d", t) or "R." in t:
                return True
            if "NOTE" in t or "NOTES" in t:
                return True
            if "DUCTILE" in t or "MATERIAL" in t or "DESCRIPTION" in t:
                return True
            return False

        def has_count_prefix(line: str) -> bool:
            return re.search(r"\b\d+\s*X\b", line) is not None

        def extract_nums(line: str) -> List[Tuple[float, bool, str]]:
            out: List[Tuple[float, bool, str]] = []
            for m in num_re.finditer(line):
                raw = m.group(1)
                try:
                    v = float(raw)
                except Exception:
                    continue
                out.append((v, False, raw))
                if "." not in raw and raw.isdigit() and 3 <= len(raw) <= 4:
                    n = int(raw)
                    out.append((n / 100.0, True, raw))
                    out.append((n / 1000.0, True, raw))
            return out

        # ---- MAX OD (turned body) ----
        od_candidates: List[Tuple[float, float, str]] = []
        # For turned parts, OD callouts are typically shown with 3 decimal places in this drawing set (e.g. 1.495, 1.459).
        # OCR often drops the decimal and even the leading digit, so we accept a few structured reconstructions.
        od_3dp_re = re.compile(r"\b(\d+\.\d{3})\b")
        od_4d_re = re.compile(r"\b(\d{4})\b")  # 1456 -> 1.456
        od_dX_3d_re = re.compile(r"\bD(\d)\s*(\d{3})\b")  # D1 456 -> 1.456
        od_Xdot_3d_re = re.compile(r"\b(\d)\.\s*(\d{3})\b")  # 1. 495 -> 1.495
        od_Xspace_3d_re = re.compile(r"\b(\d)\s+(\d{3})\b")  # 1 495 -> 1.495

        # Pass 1: strong patterns
        for line, conf in od_lines:
            if is_noise_line(line) or has_count_prefix(line):
                continue

            seen: List[float] = []

            for raw_v in od_3dp_re.findall(line):
                try:
                    seen.append(float(raw_v))
                except Exception:
                    pass

            for raw4 in od_4d_re.findall(line):
                try:
                    n = int(raw4)
                except Exception:
                    continue
                v = n / 1000.0
                seen.append(v)

            for a, b in od_dX_3d_re.findall(line):
                try:
                    seen.append(int(a) + (int(b) / 1000.0))
                except Exception:
                    pass

            for a, b in od_Xdot_3d_re.findall(line):
                try:
                    seen.append(int(a) + (int(b) / 1000.0))
                except Exception:
                    pass

            for a, b in od_Xspace_3d_re.findall(line):
                try:
                    seen.append(int(a) + (int(b) / 1000.0))
                except Exception:
                    pass

            for v in seen:
                # Exclude tiny diameters (holes) for "MAX OD" intent
                if v < 0.30:
                    continue
                if not (0.8 <= v <= 10.0):
                    continue
                # Prefer these OD reconstructions, but keep confidence conservative.
                od_candidates.append((v, conf + 0.08, line))

        # Pass 2: suffix-only tokens like "495" (meaning 1.495) when we already saw 1.xxx elsewhere.
        # This is common when OCR drops the leading "1." but keeps the 3-digit thousandths.
        if od_candidates:
            prefix_int = int(max(od_candidates, key=lambda x: x[0])[0])  # e.g., 1 for 1.456
            suffix_3d_re = re.compile(r"\b(\d{3})\b")
            for line, conf in od_lines:
                if is_noise_line(line) or has_count_prefix(line):
                    continue
                # Avoid picking up length ladder lines
                if "TYP" in line.upper() or "SEE" in line.upper():
                    continue
                for raw3 in suffix_3d_re.findall(line):
                    try:
                        n3 = int(raw3)
                    except Exception:
                        continue
                    if not (300 <= n3 <= 999):
                        continue
                    v = prefix_int + (n3 / 1000.0)
                    if not (0.8 <= v <= 10.0):
                        continue
                    od_candidates.append((v, conf + 0.02, line))

        # Pass 3: 2-decimal OD callouts for drawings that dimension OD at 2 decimals.
        # (Avoid bare 3-digit token -> OD conversions; they create lots of false positives.)
        for line, conf in od_lines:
            if is_noise_line(line) or has_count_prefix(line):
                continue
            # Explicit 2dp values
            for raw_v in re.findall(r"\b(\d+\.\d{2})\b", line):
                try:
                    v = float(raw_v)
                except Exception:
                    continue
                if 0.8 <= v <= 5.0:
                    od_candidates.append((v, conf - 0.05, line))

        # ---- MAX LENGTH (overall) ----
        len_candidates: List[Tuple[float, float, str]] = []
        # Length ladder dims are commonly 2 decimal places (e.g. 4.25, 3.81, 2.87).
        len_2dp_re = re.compile(r"\b(\d+\.\d{2})\b")
        for line, conf in len_lines:
            if has_count_prefix(line):
                continue
            t = f" {line} "
            # avoid diameter/ID lines
            if ("DIA" in t) or ("OD" in t) or ("O.D" in t) or ("BORE" in t) or ("I.D" in t):
                continue
            # With the tighter crop, we can treat SCALE as noise (dimension ladder is elsewhere).
            if is_noise_line(line):
                continue
            matches = len_2dp_re.findall(line)
            if not matches:
                continue
            for raw_v in matches:
                try:
                    v = float(raw_v)
                except Exception:
                    continue
                if not (2.0 <= v <= 50.0):
                    continue
                len_candidates.append((v, conf + 0.08, line))

        # Extra pass: digits-only OCR on length region to recover values like "300" -> 3.00 when decimal is dropped.
        try:
            import pytesseract  # type: ignore

            rgb_len = cv2.cvtColor(preprocess(len_region), cv2.COLOR_BGR2RGB)
            txt = pytesseract.image_to_string(rgb_len, config="--psm 6 -c tessedit_char_whitelist=0123456789.")
            raw_nums = re.findall(r"\d+", txt or "")
            for raw in raw_nums:
                # 300 -> 3.00 (common for overall length callouts)
                if len(raw) == 3:
                    # Only accept common callout endings to avoid 360->3.60 from 1.360, etc.
                    if raw[1:] not in ("00", "25", "50", "75"):
                        continue
                    try:
                        v = int(raw) / 100.0
                    except Exception:
                        continue
                    if 2.0 <= v <= 50.0:
                        len_candidates.append((v, 0.55, f"digits_only_len:{v:.2f}"))
        except Exception:
            pass

        len_val = None
        len_conf = 0.55
        if len_candidates:
            # For overall length, pick the maximum numeric value in the dimension ladder region.
            len_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            len_val, len_conf = len_candidates[0][0], len_candidates[0][1]

        # Choose Finish OD as the maximum OD callout on the main turned profile.
        # Filter out values that look like they came from the length ladder (same numeric values as length ladder dims),
        # unless the source line is clearly an OD callout (contains DIA/D token).
        od_val = None
        od_conf = 0.55
        if od_candidates:
            ladder_vals = [v for (v, _s, _t) in len_candidates]

            def is_ladder_like(v: float) -> bool:
                return any(abs(v - lv) <= 0.01 for lv in ladder_vals)

            filtered: List[Tuple[float, float, str]] = []
            for v, s, t in od_candidates:
                tt = t.upper()
                if ("DIA" in tt) or re.search(r"\bD\d\b", tt):
                    filtered.append((v, s, t))
                    continue
                if "TYP" in tt:
                    continue
                if is_ladder_like(v):
                    continue
                filtered.append((v, s, t))

            (filtered or od_candidates).sort(key=lambda x: (x[0], x[1]), reverse=True)
            od_val, od_conf = (filtered or od_candidates)[0][0], (filtered or od_candidates)[0][1]

        debug = {
            "regions": {
                "od_region_top_shape": list(od_region_top.shape),
                "od_region_bottom_shape": list(od_region_bottom.shape),
                "len_region_shape": list(len_region.shape),
            },
            "od_top": [{"v": v, "score": s, "line": t} for (v, s, t) in sorted(od_candidates, key=lambda x: x[0], reverse=True)[:15]],
            "len_top": [{"v": v, "score": s, "line": t} for (v, s, t) in sorted(len_candidates, key=lambda x: x[0], reverse=True)[:15]],
        }

        return {
            "finish_od_in": ExtractedValue(f"{od_val:.4f}" if isinstance(od_val, float) else None, _clamp01(od_conf), "ocr.callouts"),
            "finish_id_in": ExtractedValue(None, 0.55, "ocr.callouts.not_extracted"),
            "finish_len_in": ExtractedValue(f"{len_val:.4f}" if isinstance(len_val, float) else None, _clamp01(len_conf), "ocr.callouts"),
        }, debug

    def _load_best_view_crop(self, job_id: str) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        """Load the best view crop (turned view) if auto-detect artifacts exist.

        Returns (crop_image_bgr or None, debug dict).
        """
        outputs = self.fs.get_outputs_path(job_id)
        pages_dir = outputs / "pdf_pages"
        debug: Dict[str, Any] = {
            "used_best_view_crop": False,
            "best_view": None,
            "bbox_pixels": None,
            "page_image": None,
        }

        results_file = outputs / "auto_detect_results.json"
        if not results_file.exists():
            return None, debug

        try:
            auto_detect = json.loads(results_file.read_text(encoding="utf-8"))
        except Exception:
            return None, debug

        best_view = auto_detect.get("best_view") or None
        if not isinstance(best_view, dict):
            return None, debug

        page = best_view.get("page")
        view_index = best_view.get("view_index")
        if page is None or view_index is None:
            return None, debug

        try:
            page = int(page)
            view_index = int(view_index)
        except Exception:
            return None, debug

        page_img_path = pages_dir / f"page_{page}.png"
        if not page_img_path.exists():
            return None, debug

        img = cv2.imread(str(page_img_path)) if _CV2_AVAILABLE else _pil_imread_bgr(str(page_img_path))
        if img is None:
            return None, debug

        views_file = outputs / "pdf_views" / f"page_{page}_views.json"
        if not views_file.exists():
            return None, debug

        try:
            page_views = json.loads(views_file.read_text(encoding="utf-8"))
        except Exception:
            return None, debug

        views = page_views.get("views")
        if not isinstance(views, list) or not views:
            return None, debug

        if view_index < 0 or view_index >= len(views):
            return None, debug

        bbox_pixels = views[view_index].get("bbox_pixels")
        if not (isinstance(bbox_pixels, list) and len(bbox_pixels) == 4):
            return None, debug

        x, y, w, h = bbox_pixels
        try:
            x, y, w, h = int(x), int(y), int(w), int(h)
        except Exception:
            return None, debug

        crop = img[max(0, y) : max(0, y) + max(1, h), max(0, x) : max(0, x) + max(1, w)]

        debug["used_best_view_crop"] = True
        debug["best_view"] = {"page": page, "view_index": view_index}
        debug["bbox_pixels"] = [x, y, w, h]
        debug["page_image"] = f"outputs/pdf_pages/page_{page}.png"

        return crop, debug

    def _load_title_block_crop(self, job_id: str, page: int = 0) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        """Heuristic crop for title block (bottom-right area)."""
        outputs = self.fs.get_outputs_path(job_id)
        page_img_path = outputs / "pdf_pages" / f"page_{page}.png"
        debug: Dict[str, Any] = {"used_title_block_crop": False, "page_image": None}

        if not page_img_path.exists():
            return None, debug

        img = cv2.imread(str(page_img_path)) if _CV2_AVAILABLE else _pil_imread_bgr(str(page_img_path))
        if img is None:
            return None, debug

        h, w = img.shape[:2]
        # Bottom 35%, right 55% (tuned for typical engineering title blocks)
        y0 = int(h * 0.65)
        x0 = int(w * 0.45)
        crop = img[y0:h, x0:w]

        debug["used_title_block_crop"] = True
        debug["page_image"] = f"outputs/pdf_pages/page_{page}.png"
        debug["bbox_pixels"] = [x0, y0, w - x0, h - y0]
        return crop, debug

    def _load_full_page(self, job_id: str, page: int = 0) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        outputs = self.fs.get_outputs_path(job_id)
        page_img_path = outputs / "pdf_pages" / f"page_{page}.png"
        debug: Dict[str, Any] = {"used_full_page": False, "page_image": None}
        if not page_img_path.exists():
            return None, debug
        img = cv2.imread(str(page_img_path)) if _CV2_AVAILABLE else _pil_imread_bgr(str(page_img_path))
        if img is None:
            return None, debug
        debug["used_full_page"] = True
        debug["page_image"] = f"outputs/pdf_pages/page_{page}.png"
        return img, debug

    def _load_notes_crop(self, job_id: str, page: int = 0) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        """Heuristic crop for notes/dimension table area (often contains OD/ID/LENGTH labels)."""
        outputs = self.fs.get_outputs_path(job_id)
        page_img_path = outputs / "pdf_pages" / f"page_{page}.png"
        debug: Dict[str, Any] = {"used_notes_crop": False, "page_image": None}
        if not page_img_path.exists():
            return None, debug
        img = cv2.imread(str(page_img_path)) if _CV2_AVAILABLE else _pil_imread_bgr(str(page_img_path))
        if img is None:
            return None, debug
        h, w = img.shape[:2]
        # Top 45%, left 65%
        y0, y1 = 0, int(h * 0.45)
        x0, x1 = 0, int(w * 0.65)
        crop = img[y0:y1, x0:x1]
        debug["used_notes_crop"] = True
        debug["page_image"] = f"outputs/pdf_pages/page_{page}.png"
        debug["bbox_pixels"] = [x0, y0, x1 - x0, y1 - y0]
        return crop, debug

    def _ocr(self, image_bgr: np.ndarray) -> List[Tuple[str, float]]:
        """Run OCR and return [(text, confidence)].

        Tries EasyOCR first; falls back to pytesseract if EasyOCR/torch is unavailable.
        """
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB) if _CV2_AVAILABLE else image_bgr[:, :, ::-1]

        # 1) EasyOCR path
        try:
            import easyocr  # type: ignore

            reader = easyocr.Reader(["en"], gpu=False)
            results = reader.readtext(rgb)
            out: List[Tuple[str, float]] = []
            for r in results:
                try:
                    text = str(r[1]).strip()
                    conf = float(r[2])
                except Exception:
                    continue
                if text:
                    out.append((text, _clamp01(conf)))
            return out
        except Exception:
            pass

        # 2) pytesseract fallback
        try:
            import pytesseract  # type: ignore

            # Use TSV to capture per-token confidence
            data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT)
            n = len(data.get("text", []))

            # Group tokens into lines using (block_num, par_num, line_num)
            lines: Dict[Tuple[int, int, int], List[Tuple[str, float]]] = {}
            for i in range(n):
                txt = str(data["text"][i]).strip()
                if not txt:
                    continue
                conf_raw = data.get("conf", [None] * n)[i]
                try:
                    conf = float(conf_raw)
                    conf01 = _clamp01(conf / 100.0) if conf >= 0 else 0.0
                except Exception:
                    conf01 = 0.0

                try:
                    key = (
                        int(data.get("block_num", [0] * n)[i]),
                        int(data.get("par_num", [0] * n)[i]),
                        int(data.get("line_num", [0] * n)[i]),
                    )
                except Exception:
                    key = (0, 0, 0)

                lines.setdefault(key, []).append((txt, conf01))

            out2: List[Tuple[str, float]] = []
            for _, toks in lines.items():
                # Join tokens into a line
                line_txt = " ".join(t for t, _ in toks).strip()
                if not line_txt:
                    continue
                # Use robust confidence: average of positive confs, else 0.55 fallback
                confs = [c for _, c in toks if c > 0]
                line_conf = _clamp01(sum(confs) / len(confs)) if confs else 0.55
                out2.append((line_txt, line_conf))

            return out2
        except Exception as e:
            raise RuntimeError(
                "No OCR engine available. Install either EasyOCR (requires working torch) "
                "or Tesseract + pytesseract. Under Windows/conda: `conda install -y -c conda-forge tesseract pytesseract`."
            ) from e

    def _normalize_text(self, s: str) -> str:
        # Uppercase, normalize common symbols
        s2 = s.upper()
        s2 = s2.replace("Ø", "DIA ").replace("∅", "DIA ")
        return s2

    def _extract_first(self, haystack: str, patterns: List[str]) -> Optional[str]:
        for p in patterns:
            m = re.search(p, haystack, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    def _extract_dimensions(self, texts: List[Tuple[str, float]]) -> Dict[str, ExtractedValue]:
        """Parse key fields from OCR text list."""
        normalized_lines: List[Tuple[str, float]] = [(self._normalize_text(t), c) for t, c in texts]
        joined = " \n".join(t for t, _ in normalized_lines)

        # Part no: allow e.g. 050DZ0017, 050DZ0017-C, 050DZ0017_C
        part_no = self._extract_first(
            joined,
            [
                r"(?:PART\s*(?:NO|NUMBER)|P/N|PN|DRAWING\s*NO|DWG\s*NO)\s*[:#]?\s*([A-Z0-9]{6,}[-_]*[A-Z0-9]*)",
                r"\b([0-9]{2,}[A-Z]{1,}[0-9]{2,}[A-Z0-9]*)\b",
            ],
        )
        if part_no:
            part_no = re.sub(r"[-_][A-Z0-9]{1,3}$", "", part_no)  # strip trailing rev-like suffix

        rev = self._extract_first(joined, [r"(?:REV|REVISION)\s*[:#]?\s*([A-Z0-9]{1,4})"])
        material_grade = self._extract_first(joined, [r"\b(\d{2,3}-\d{2}-\d{2})\b"])
        qty = self._extract_first(joined, [r"(?:QTY|QUANTITY|MOQ)\s*[:#]?\s*(\d{1,6})"])

        # Dimensions:
        # We MUST avoid grabbing random callouts (radii, thread specs, scale ratios, tolerances).
        # Strategy:
        # - Parse numeric candidates per line with context flags
        # - OD: choose max value from lines that look like diameter callouts (DIA/DI./Ø) and NOT SCALE/R./UNC
        # - ID: choose from ID/BORE lines, else diameter callouts < OD
        # - LEN: choose max value from non-radius/non-thread/non-scale lines; allow SCALE line only as last resort

        number_re = re.compile(r"([0-9]+(?:\.[0-9]+)?)")

        def to_inches_from_line(num: float, line: str) -> float:
            l = f" {line} "
            if "MM" in l and not re.search(r"\bIN\b|\bINCH\b", l) and "\"" not in l:
                return _mm_to_in(num)
            return num

        def norm_numeric_line(line: str) -> str:
            s = line.replace(",", ".")
            # Common OCR substitutions
            s = re.sub(r"(?<=\d)I|I(?=\d)|(?<=\.)I|I(?=\.)", "1", s)
            s = re.sub(r"(?<=\d)O|O(?=\d)|(?<=\.)O|O(?=\.)", "0", s)
            s = re.sub(r"(?<=\d)S|S(?=\d)|(?<=\.)S|S(?=\.)", "5", s)
            s = re.sub(r"(?<=\d)B|B(?=\d)|(?<=\.)B|B(?=\.)", "8", s)
            # DI.7I -> DIA 1.71 (we already normalize Ø to DIA earlier)
            s = s.replace("DI.", "DIA ").replace("D1.", "DIA 1.")
            return s

        def line_flags(line: str) -> Dict[str, bool]:
            l = f" {line} "
            # Check for tolerance ranges (e.g., 0.723-0.727, 1.006-1.008, .185-.190)
            has_tolerance_range = bool(
                re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', l) or
                re.search(r'\.\d+\s*[-–]\s*\.\d+', l)
            )
            # Check for metric brackets [mm]
            has_metric_bracket = bool(re.search(r'\[[\d.]+\]', l))
            
            return {
                "is_scale": "SCALE" in l,
                "is_radius": bool(re.search(r"\bR\.\d", l)) or ("R." in l),
                "is_thread": ("UNC" in l) or ("UN-" in l) or ("-2B" in l) or ("THREAD" in l),
                "is_tol": ("TOL" in l) or ("±" in l) or ("DEG" in l) or has_tolerance_range,
                "is_tolerance_range": has_tolerance_range,  # New flag
                "is_metric_bracket": has_metric_bracket,  # New flag
                "is_cost": any(k in l for k in ["USD", "INR", "COST", "PRICE", "RATE", "EXCHANGE", "CURRENCY"]),
                "is_material_grade": bool(re.search(r"\b\d{2,3}-\d{2}-\d{2}\b", l)),
                "has_dia": ("DIA" in l) or ("Ø" in l) or ("∅" in l) or (" DI." in l) or (" DI " in l),
                "has_id": (" ID" in l) or ("I.D" in l) or ("BORE" in l) or ("INNER" in l),
                "has_len": ("LENGTH" in l) or (" LEN" in l) or ("OAL" in l) or ("OVERALL" in l),
            }

        # Collect candidates
        dia_cands: List[Tuple[float, float, str, Dict[str, bool]]] = []
        id_cands: List[Tuple[float, float, str, Dict[str, bool]]] = []
        len_cands: List[Tuple[float, float, str, Dict[str, bool]]] = []
        generic_cands: List[Tuple[float, float, str, Dict[str, bool]]] = []

        for raw_line, conf in normalized_lines:
            flags = line_flags(raw_line)
            if flags["is_cost"] or flags["is_material_grade"]:
                continue
            
            # Rule 1: Ignore metric bracket values
            if flags["is_metric_bracket"]:
                continue
            
            # Rule 2: Ignore tolerance ranges
            if flags["is_tolerance_range"]:
                continue

            line_num = norm_numeric_line(raw_line)
            for m in number_re.finditer(line_num):
                raw = m.group(1)
                v = _to_float(raw)
                if v is None:
                    continue
                
                # Skip if value is inside brackets (metric)
                match_start = m.start()
                match_end = m.end()
                context_before = raw_line[max(0, match_start-20):match_start]
                context_after = raw_line[match_end:min(len(raw_line), match_end+20)]
                if '[' in context_before and ']' in context_after:
                    continue  # Skip metric bracket values
                
                base = to_inches_from_line(v, raw_line)
                
                # Check for tolerance range in the line (e.g., 0.723-0.727, 1.006-1.008)
                tolerance_range_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', raw_line) or \
                                       re.search(r'\.(\d+)\s*[-–]\s*\.(\d+)', raw_line)
                
                if tolerance_range_match:
                    # Extract both values from tolerance range
                    try:
                        if tolerance_range_match.lastindex == 2:
                            val1 = float(tolerance_range_match.group(1))
                            val2 = float(tolerance_range_match.group(2))
                        else:
                            # Leading decimal: .185-.190
                            val1_str = '0.' + tolerance_range_match.group(1)
                            val2_str = '0.' + tolerance_range_match.group(2)
                            val1 = float(val1_str)
                            val2 = float(val2_str)
                        
                        # For diameters: use MAX (conservative)
                        # For lengths: use average
                        if flags["has_dia"] or flags["has_id"]:
                            base = max(val1, val2)  # MAX for diameters
                        else:
                            base = (val1 + val2) / 2.0  # Average for lengths
                    except (ValueError, IndexError):
                        pass  # Use original base value if parsing fails
                
                candidates: List[float] = [base]
                if "." not in raw and raw.isdigit() and 3 <= len(raw) <= 5:
                    n = int(raw)
                    candidates.append(to_inches_from_line(n / 100.0, raw_line))   # 171 -> 1.71
                    candidates.append(to_inches_from_line(n / 1000.0, raw_line))  # 1710 -> 1.710

                for vin in candidates:
                    if vin <= 0 or vin > 200:
                        continue
                    # drop huge inch values unless explicitly metric
                    if ("MM" not in raw_line) and vin > 20.0:
                        continue

                    score = _clamp01(conf + 0.10)
                    if vin != base:
                        score = _clamp01(score - 0.08)
                    if "." not in raw and raw.isdigit() and len(raw) <= 2:
                        score = _clamp01(score - 0.20)
                    
                    # Slight penalty for tolerance ranges (prefer explicit values)
                    if flags.get("is_tolerance_range", False):
                        score = _clamp01(score - 0.05)

                    tup = (vin, score, raw_line, flags)
                    generic_cands.append(tup)
                    if flags["has_dia"]:
                        dia_cands.append(tup)
                    if flags["has_id"]:
                        id_cands.append(tup)
                    if flags["has_len"]:
                        len_cands.append(tup)

        def best_from(pool: List[Tuple[float, float, str, Dict[str, bool]]], pred) -> Optional[Tuple[float, float, str]]:
            items = [(v, s, l) for (v, s, l, f) in pool if pred(v, s, l, f)]
            if not items:
                return None
            # prefer high confidence then value magnitude
            items.sort(key=lambda x: (x[1], x[0]), reverse=True)
            return items[0]

        # 1) OD: diameter callout, not scale/radius/thread/bracket
        # Note: Tolerance ranges are now parsed to extract MAX value, so accept them
        # Prefer dimensions in main body range (0.5-2.5") to avoid small features
        od_pick = best_from(
            dia_cands,
            lambda v, s, l, f: (
                (not f["is_scale"]) and 
                (not f["is_radius"]) and 
                (not f["is_thread"]) and 
                (not f.get("is_metric_bracket", False)) and
                (0.25 <= v <= 5.0)  # Updated range per requirements
            ),
        )
        
        # If we got a very large OD (>2.5"), try to find a better match in main body range
        if od_pick and od_pick[0] > 2.5:
            od_pick_main_body = best_from(
                dia_cands,
                lambda v, s, l, f: (
                    (not f["is_scale"]) and 
                    (not f["is_radius"]) and 
                    (not f["is_thread"]) and 
                    (not f.get("is_metric_bracket", False)) and
                    (0.5 <= v <= 2.5)  # Prefer main body range
                ),
            )
            if od_pick_main_body:
                od_pick = od_pick_main_body
        
        od_val = od_pick[0] if od_pick else None
        od_conf = od_pick[1] if od_pick else 0.55

        # 2) ID: explicit ID/BORE line, not scale/radius/thread/bracket; else DIA < OD
        # Note: Tolerance ranges are now parsed to extract MAX value, so accept them
        id_pick = best_from(
            id_cands,
            lambda v, s, l, f: (
                (not f["is_scale"]) and 
                (not f["is_radius"]) and 
                (not f["is_thread"]) and 
                (not f.get("is_metric_bracket", False)) and
                (0.05 <= v <= 5.0)
            ),
        )
        if id_pick:
            id_val = id_pick[0]
            id_conf = id_pick[1]
        else:
            id_val = None  # Changed: None instead of 0.0
            id_conf = 0.55
            if isinstance(od_val, float):
                id_from_dia = best_from(
                    dia_cands,
                    lambda v, s, l, f: (
                        (not f["is_scale"]) and 
                        (not f["is_radius"]) and 
                        (not f["is_thread"]) and 
                        (not f.get("is_metric_bracket", False)) and
                        (0.05 <= v <= od_val * 0.95)
                    ),
                )
                if id_from_dia:
                    id_val = id_from_dia[0]
                    id_conf = id_from_dia[1] - 0.08

        # 3) LEN: explicit length line preferred; else best generic >0.3 not radius/thread/bracket; scale allowed only if no other
        # Note: Tolerance ranges are now parsed to extract average value, so accept them
        # Prefer reasonable lengths (0.3-5.0") to avoid selecting wrong dimensions
        len_pick = best_from(
            len_cands,
            lambda v, s, l, f: (
                (not f["is_radius"]) and 
                (not f["is_thread"]) and 
                (not f.get("is_metric_bracket", False)) and
                (v >= 0.3) and  # Updated: > 0.3" per requirements (not small shoulder)
                (v <= 5.0)  # Prefer reasonable lengths first
            ),
        )
        if not len_pick:
            # Try wider range
            len_pick = best_from(
                len_cands,
                lambda v, s, l, f: (
                    (not f["is_radius"]) and 
                    (not f["is_thread"]) and 
                    (not f.get("is_metric_bracket", False)) and
                    (v >= 0.3) and
                    (v <= 20.0)
                ),
            )
        if not len_pick:
            # Non-scale candidates first
            len_pick = best_from(
                generic_cands,
                lambda v, s, l, f: (
                    (not f["is_scale"]) and 
                    (not f["is_radius"]) and 
                    (not f["is_thread"]) and 
                    (not f.get("is_metric_bracket", False)) and
                    (v >= 0.3) and  # Updated: > 0.3" per requirements
                    (v <= 5.0)  # Prefer reasonable lengths
                ),
            )
        if not len_pick:
            # Try wider range
            len_pick = best_from(
                generic_cands,
                lambda v, s, l, f: (
                    (not f["is_scale"]) and 
                    (not f["is_radius"]) and 
                    (not f["is_thread"]) and 
                    (not f.get("is_metric_bracket", False)) and
                    (v >= 0.3) and
                    (v <= 20.0)
                ),
            )
        if not len_pick:
            # Last resort: allow scale line numbers (but still filter bracket)
            len_pick = best_from(
                generic_cands,
                lambda v, s, l, f: (
                    (f["is_scale"]) and 
                    (not f["is_radius"]) and 
                    (not f["is_thread"]) and 
                    (not f.get("is_metric_bracket", False)) and
                    (v >= 0.3) and
                    (v <= 20.0)
                ),
            )

        len_val = len_pick[0] if len_pick else None
        len_conf = len_pick[1] if len_pick else 0.55

        def pick_conf(keywords: List[str]) -> float:
            # confidence heuristic: max OCR confidence among lines containing any keyword
            best = 0.0
            for t, c in texts:
                tt = self._normalize_text(t)
                if any(k in tt for k in keywords):
                    best = max(best, c)
            return _clamp01(best if best > 0 else 0.55)

        out: Dict[str, ExtractedValue] = {}
        out["part_no"] = ExtractedValue(part_no, pick_conf(["PART", "PN", "P/N", "DWG", "DRAWING"]), "ocr")
        out["part_revision"] = ExtractedValue(rev, pick_conf(["REV"]), "ocr")
        out["material_grade"] = ExtractedValue(material_grade, pick_conf(["MAT", "MATERIAL"]), "ocr")
        out["qty_moq"] = ExtractedValue(qty, pick_conf(["QTY", "MOQ"]), "ocr")

        # If we couldn't pick, fall back to keyword heuristic only for confidence.
        if od_val is None:
            od_conf = pick_conf(["OD", "O.D", "OUTER", "DIA"])
        if id_val is None:
            id_conf = pick_conf(["ID", "I.D", "BORE", "INNER"])
        if len_val is None:
            len_conf = pick_conf(["LENGTH", "LEN", "OAL"])

        # DEPRECATED: OD/Length dimensions moved to pdf_hint - geometry envelope is now source of truth
        # Keep only metadata fields for RFQ processing
        pdf_hint = {}
        pdf_hint["finish_od_in"] = ExtractedValue(f"{od_val:.4f}" if isinstance(od_val, float) else None, _clamp01(od_conf), "ocr.pdf_hint")
        pdf_hint["finish_id_in"] = ExtractedValue(f"{id_val:.4f}" if isinstance(id_val, float) else None, _clamp01(id_conf), "ocr.pdf_hint")
        pdf_hint["finish_len_in"] = ExtractedValue(f"{len_val:.4f}" if isinstance(len_val, float) else None, _clamp01(len_conf), "ocr.pdf_hint")

        out["pdf_hint"] = pdf_hint
        return out

    def extract_from_job(self, job_id: str) -> Dict[str, Any]:
        """Extract vendor-quote fields from the job PDF artifacts."""
        outputs = self.fs.get_outputs_path(job_id)
        pages_dir = outputs / "pdf_pages"
        if not pages_dir.exists():
            raise FileNotFoundError("Missing outputs/pdf_pages. Upload PDF first.")

        # OCR sources: best turned view crop + title block crop (page 0).
        crops: List[Tuple[str, np.ndarray]] = []
        debug: Dict[str, Any] = {"job_id": job_id, "extractor_version": EXTRACTOR_VERSION, "crops": [], "notes": []}

        best_crop, best_dbg = self._load_best_view_crop(job_id)
        if best_crop is not None:
            crops.append(("best_view_crop", best_crop))
            debug["crops"].append({"name": "best_view_crop", **best_dbg})
        else:
            debug["notes"].append("best_view_crop_unavailable")

        title_crop, title_dbg = self._load_title_block_crop(job_id, page=0)
        if title_crop is not None:
            crops.append(("title_block_crop", title_crop))
            debug["crops"].append({"name": "title_block_crop", **title_dbg})
        else:
            debug["notes"].append("title_block_crop_unavailable")

        # Full-page OCR (fallback): helps capture unlabeled dimension callouts that crops may miss.
        full_page, full_dbg = self._load_full_page(job_id, page=0)
        if full_page is not None:
            # Downscale for speed; keep enough detail for text.
            try:
                h, w = full_page.shape[:2]
                scale = 0.5
                full_page = cv2.resize(full_page, (int(w * scale), int(h * scale)))
            except Exception:
                pass
            crops.append(("full_page_0", full_page))
            debug["crops"].append({"name": "full_page_0", **full_dbg, "downscaled": True})

        notes_crop, notes_dbg = self._load_notes_crop(job_id, page=0)
        if notes_crop is not None:
            crops.append(("notes_crop", notes_crop))
            debug["crops"].append({"name": "notes_crop", **notes_dbg})

        if not crops:
            raise FileNotFoundError("No crops available for OCR.")

        all_texts: List[Tuple[str, float]] = []
        callout_fields: Optional[Dict[str, ExtractedValue]] = None
        if best_crop is not None:
            try:
                callout_fields, callout_dbg = self._extract_dims_from_best_view_callouts(best_crop)
                debug["callout_dims"] = {k: {"value": v.value, "confidence": v.confidence, "source": v.source} for k, v in callout_fields.items()}
                debug["callout_debug"] = callout_dbg
            except Exception as e:
                debug["notes"].append(f"callout_detector_failed: {e}")
                callout_fields = None

        for name, crop in crops:
            # Mild preprocessing for OCR readability:
            # Keep it conservative because aggressive binarization can destroy small dimension text.
            crop2 = crop
            try:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (3, 3), 0)
                crop2 = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            except Exception:
                crop2 = crop

            texts = self._ocr(crop2)
            debug.setdefault("ocr_counts", {})[name] = len(texts)
            all_texts.extend(texts)

        # Include a small preview for debugging (helps tune regex without saving images).
        debug["ocr_preview"] = [{"text": t, "conf": c} for (t, c) in all_texts[:80]]

        fields = self._extract_dimensions(all_texts)

        # Prefer callout-based dims if we got them (vendor quote mode intent).
        if callout_fields is not None:
            # Always prefer callout-derived dimension fields (even if value is None).
            # This prevents a noisy full-page fallback (e.g., "9044" -> 9.044) from being treated as a real dimension.
            for k in ("finish_od_in", "finish_id_in", "finish_len_in"):
                if callout_fields.get(k) is not None:
                    fields[k] = callout_fields[k]

        # NOTE: No per-part overrides here. Keep extraction generic and explainable across all PDFs.

        # Shape response in a JSON-friendly way
        response = {
            "job_id": job_id,
            "fields": {k: {"value": v.value, "confidence": v.confidence, "source": v.source} for k, v in fields.items() if k != "pdf_hint"},
            "debug": debug,
        }

        # Include pdf_hint separately (deprecated OD/Length dimensions)
        if "pdf_hint" in fields:
            response["pdf_hint"] = {k: {"value": v.value, "confidence": v.confidence, "source": v.source} for k, v in fields["pdf_hint"].items()}

        return response


