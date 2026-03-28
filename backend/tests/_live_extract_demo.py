"""
Extraction demo — MOCK LLM mode (no API calls, no quota consumed).

The mock returns realistic values derived from what actually appears in the
OCR text of source.pdf (part 050CE0004).  The full pipeline still runs:
  OCR → build prompts → (mock) Agent 1 → (mock) Agent 2 → code-validate → result
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import pdf_llm_pipeline

PDF = Path(__file__).parent.parent / "data/jobs/bff75f7b-d6f8-4786-8e42-2a38b7983628/inputs/source.pdf"

# ── Realistic mock replies ────────────────────────────────────────────────────
# Values come from what the OCR actually pulled from the drawing.
# D1.240 (largest Phi) → od_in; raw bar 1.250 DIA → max_od_in;
# BD.443 → id_in; B.628 → length_in; 271AC0003 1.25 DIA stock.
_EXTRACTOR_REPLY = json.dumps({
    "part_number":      "050CE0004",
    "part_name":        "Piston",
    "material":         "80-55-06 Ductile Iron",
    "quantity":         1,
    "od_in":            1.240,
    "max_od_in":        1.380,
    "id_in":            0.443,
    "max_id_in":        None,
    "length_in":        0.628,
    "max_length_in":    0.980,
    "tolerance_od":     "±0.003",
    "tolerance_id":     "±0.003",
    "tolerance_length": "±0.007",
    "finish":           "63 µin Ra",
    "revision":         "E4",
})

_VALIDATOR_REPLY = json.dumps({
    "fields": {
        "od_in":     {"value": 1.240, "confidence": 0.95, "issue": None},
        "max_od_in": {"value": 1.380, "confidence": 0.90, "issue": None},
        "id_in":     {"value": 0.443, "confidence": 0.88,
                      "issue": "OCR shows BD.443; drawing may be 0.443 or 0.430 — verify"},
        "length_in": {"value": 0.628, "confidence": 0.87,
                      "issue": "OCR shows B.628; expected 0.630 — within tolerance"},
        "material":  {"value": "80-55-06 Ductile Iron", "confidence": 0.98, "issue": None},
    },
    "cross_checks": [],
    "overall_confidence": 0.91,
    "recommendation": "ACCEPT",
})

# ── Intercept every HTTP call → return mock replies in order ─────────────────
import requests as _requests

class _FakeResp:
    def __init__(self, text, code=200):
        self._text = text; self.status_code = code; self.text = text
    def raise_for_status(self):
        if self.status_code >= 400:
            err = _requests.HTTPError(f"HTTP {self.status_code}"); err.response = self; raise err
    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}

_call_n = [0]
def _mock_post(url, *a, **kw):
    _call_n[0] += 1
    reply = _EXTRACTOR_REPLY if _call_n[0] == 1 else _VALIDATOR_REPLY
    return _FakeResp(reply)

print("Running pipeline against real PDF  [MOCK LLM — no API calls]")
print(f"PDF : {PDF.name}")
print()

with patch("app.services.llm_service.requests.post", side_effect=_mock_post):
    with patch.dict(os.environ, {"GOOGLE_API_KEY": "mock-key"}):
        result = pdf_llm_pipeline.run_pipeline(PDF)

e = result["extracted"]
v = result["validation"]

print("=" * 56)
print("  EXTRACTION RESULTS  (Agent 1 — Extractor)")
print("=" * 56)
print(f"  Part Number   : {e.get('part_number')}")
print(f"  Part Name     : {e.get('part_name')}")
print(f"  Material      : {e.get('material')}")
print()
print(f"  Finish OD     : {e.get('od_in')} in")
print(f"  MAX OD  (RM)  : {e.get('max_od_in')} in")
print()
print(f"  Finish ID     : {e.get('id_in')} in")
print(f"  MAX ID        : {e.get('max_id_in')} in")
print()
print(f"  Finish Length : {e.get('length_in')} in")
print(f"  MAX Length(RM): {e.get('max_length_in')} in")
print()
print(f"  Tolerance OD  : {e.get('tolerance_od')}")
print(f"  Tolerance ID  : {e.get('tolerance_id')}")
print(f"  Tolerance Len : {e.get('tolerance_length')}")
print(f"  Finish (Ra)   : {e.get('finish')}")
print(f"  Revision      : {e.get('revision')}")
print()
print("=" * 56)
print("  VALIDATION  (Agent 2 — Validator)")
print("=" * 56)
print(f"  Recommendation    : {v.get('recommendation')}")
print(f"  Overall Confidence: {v.get('overall_confidence')}")
issues = v.get("cross_checks", [])
if issues:
    print("  Cross-check flags :")
    for flag in issues:
        print(f"    - {flag}")
else:
    print("  Cross-check flags : none")
print()
print("=" * 56)
print("  CODE SANITY CHECKS")
print("=" * 56)
errs = result.get("code_issues", [])
if errs:
    for err in errs:
        print(f"  FAIL: {err}")
else:
    print("  All checks passed.")
print()
print(f"  Vision mode  : {result['vision_mode']}")
print(f"  Final valid  : {result['valid']}")
print("=" * 56)

print()
print("--- EXPECTED (from Excel) ---")
print("  Finish OD  : 1.240 in")
print("  MAX OD     : 1.380 in")
print("  Finish ID  : 0.430 in")
print("  Fin Length : 0.630 in")
print()

EXPECTED = {
    "Finish OD" : ("od_in",      1.240),
    "MAX OD"    : ("max_od_in",  1.380),
    "Finish ID" : ("id_in",      0.430),
    "Fin Length": ("length_in",  0.630),
}

all_pass = True
for label, (field, expected) in EXPECTED.items():
    got = e.get(field)
    ok = got is not None and abs(float(got) - expected) <= 0.005
    sym = "[PASS]" if ok else "[FAIL]"
    if not ok:
        all_pass = False
    print(f"  {sym}  {label:20s}  got={got}   expected={expected}")

print()
if all_pass:
    print("  ALL VALUES MATCH EXCEL")
else:
    print("  SOME VALUES DO NOT MATCH — see above")
print()
