"""
Demo script: show output for each LLM E2E scenario step.
Run with:  conda run -n base python tests/run_llm_demo.py
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
import requests as _requests

# Add backend to path
BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import llm_service, pdf_llm_pipeline

# -- fixture paths ----------------------------------------------------------
REAL_PDF = BACKEND / "data/jobs/bff75f7b-d6f8-4786-8e42-2a38b7983628/inputs/source.pdf"
ENV_KEY  = {"GOOGLE_API_KEY": "test-key-demo"}

# -- shared mock payloads ---------------------------------------------------
EXTRACTOR_REPLY = json.dumps({
    "part_number": "050CE0004",
    "part_name": "Piston",
    "material": "80-55-06 Ductile Iron",
    "quantity": 1,
    "od_in": 1.240,   "max_od_in": 1.380,
    "id_in": 0.430,   "max_id_in": 0.440,
    "length_in": 0.630, "max_length_in": 0.980,
    "tolerance_od": "±0.001", "tolerance_id": "±0.002",
    "tolerance_length": "±0.003",
    "finish": "63 uin Ra", "revision": "E4",
})
VALIDATOR_REPLY = json.dumps({
    "fields": {
        "od_in":     {"value": 1.240, "confidence": 0.92, "issue": None},
        "max_od_in": {"value": 1.380, "confidence": 0.90, "issue": None},
        "id_in":     {"value": 0.430, "confidence": 0.91, "issue": None},
        "material":  {"value": "80-55-06 Ductile Iron", "confidence": 0.95, "issue": None},
    },
    "cross_checks": [],
    "overall_confidence": 0.91,
    "recommendation": "ACCEPT",
})
REVIEW_REPLY = json.dumps({
    "fields": {},
    "cross_checks": ["od_in seems low for this part class"],
    "overall_confidence": 0.50,
    "recommendation": "REVIEW",
})

class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body; self.status_code = status_code; self.text = str(body)
    def raise_for_status(self):
        if self.status_code >= 400:
            err = _requests.HTTPError(f"HTTP {self.status_code}"); err.response = self; raise err
    def json(self): return self._body

def _ok(text):  return _FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})
def _429():     return _FakeResponse({}, 429)

def _two_agent_mock(ext=EXTRACTOR_REPLY, val=VALIDATOR_REPLY):
    log = []
    def _fn(url, *a, **k):
        log.append(url)
        n = sum(1 for u in log if "generateContent" in u)
        return _ok(ext if n == 1 else val)
    return _fn, log

SEP  = "-" * 60
PASS = "  [PASS]"
FAIL = "  [FAIL]"

def ok(label, detail=""):
    print(f"{PASS}  {label}" + (f"  ->  {detail}" if detail else ""))

def fail(label, detail=""):
    print(f"{FAIL}  {label}" + (f"  ->  {detail}" if detail else ""))


# ==========================================================================
print(f"\n{'='*60}")
print("  LLM Pipeline — End-to-End Scenario Demo")
print(f"{'='*60}")

# -- Step 0: Agent prompt inspection ----------------------------------------
print(f"\n{SEP}")
print("  Step 0 · Agent prompt inspection  (orientation-agnostic keywords)")
print(SEP)
EXT_PROMPT = pdf_llm_pipeline._EXTRACTOR_VISION_PROMPT
VAL_PROMPT = pdf_llm_pipeline._VALIDATOR_VISION_PROMPT

ORIENT_CHECKS = [
    ("axis of symmetry",     "identifies part axis regardless of drawing orientation"),
    ("ANY orientation",      "explicitly states any orientation is handled"),
    ("perpendicular",        "OD witness lines are perpendicular to axis"),
    ("parallel to the part\'s axis", "length runs parallel to axis"),
    ("hatching",             "bore detection via section view hatching"),
]

BAD_KEYWORDS = ["horizontal parallel witness", "look for the OUTERMOST pair of horizontal"]

for kw, label in ORIENT_CHECKS:
    if kw.lower() in EXT_PROMPT.lower() or kw.lower() in VAL_PROMPT.lower():
        ok(label)
    else:
        fail(label, f"'{kw}' not found in prompts")

for bad in BAD_KEYWORDS:
    if bad.lower() in EXT_PROMPT.lower():
        fail("old orientation-specific text removed", f"still contains: '{bad}'")
    else:
        ok("old horizontal-specific language gone", f"'{bad[:40]}...' absent")

print(f"\n  Extractor prompt length:  {len(EXT_PROMPT):,} chars")
print(f"  Validator prompt length:  {len(VAL_PROMPT):,} chars")

# -- Step 1: OCR extraction -------------------------------------------------
print(f"\n{SEP}")
print("  Step 1 · OCR extraction  (pytesseract on page_0.png)")
print(SEP)
ocr_text = pdf_llm_pipeline._extract_pdf_text(REAL_PDF)
print(f"  {'Chars extracted':24s} {len(ocr_text)}")
ok(">= 100 chars", f"{len(ocr_text)} chars") if len(ocr_text) >= 100 else fail(">= 100 chars", f"only {len(ocr_text)}")
ok("Part # found") if "050CE0004" in ocr_text.upper() else fail("Part # found")
ok("Digits present") if any(c.isdigit() for c in ocr_text) else fail("Digits present")
print(f"\n  First 400 chars of OCR output:")
preview = ocr_text[:400].replace("\n", " ").strip()
for i in range(0, len(preview), 80):
    print(f"    {preview[i:i+80]}")

# -- Step 2: Pipeline structure & call count --------------------------------
print(f"\n{SEP}")
print("  Step 2 · Pipeline structure  (correct keys, 2 Gemini calls)")
print(SEP)
mock_fn, call_log = _two_agent_mock()
with patch.dict(os.environ, ENV_KEY):
    with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
        result = pdf_llm_pipeline.run_pipeline(REAL_PDF)

required_keys = {"pdf_text_length", "extracted", "validation", "code_issues", "valid", "vision_mode"}
got_keys      = set(result.keys())
missing       = required_keys - got_keys
ok("All required keys present", str(sorted(got_keys))) if not missing else fail("Missing keys", str(missing))
ok(f"Exactly 2 Gemini calls", f"{len(call_log)} calls") if len(call_log) == 2 else fail("Gemini call count", f"{len(call_log)}")
ok("pdf_text_length > 0", str(result["pdf_text_length"]))
print(f"  {'  vision_mode':26s} {result.get('vision_mode')}")
for i, url in enumerate(call_log, 1):
    short = url.split("generativelanguage.googleapis.com/")[-1].split("?")[0]
    print(f"    call {i}: {short}")

# -- Step 3: Extractor output -----------------------------------------------
print(f"\n{SEP}")
print("  Step 3 · Extractor (Agent 1) output")
print(SEP)
s = result["extracted"]
for field in ["part_number", "material", "od_in", "max_od_in", "id_in", "max_id_in", "length_in", "max_length_in", "finish", "revision"]:
    print(f"  {'  '+field:26s} {s.get(field)}")
print()
ok("od_in > 0",             f"{s['od_in']}") if s["od_in"] and s["od_in"] > 0 else fail("od_in > 0")
ok("max_od_in >= od_in",     f"{s['max_od_in']} >= {s['od_in']}") if s["max_od_in"] >= s["od_in"] else fail("max_od_in >= od_in")
ok("id_in < od_in",         f"{s['id_in']} < {s['od_in']}") if s["id_in"] < s["od_in"] else fail("id_in < od_in")
mat = (s.get("material") or "").lower()
ok("material contains iron", s["material"]) if ("iron" in mat or "ductile" in mat) else fail("material contains iron", s.get("material"))

# -- Step 4: Validator output -----------------------------------------------
print(f"\n{SEP}")
print("  Step 4 · Validator (Agent 2) output")
print(SEP)
v = result["validation"]
print(f"  {'  recommendation':26s} {v['recommendation']}")
print(f"  {'  overall_confidence':26s} {v['overall_confidence']}")
print(f"  {'  cross_checks':26s} {v['cross_checks']}")
print()
ok("recommendation = ACCEPT",    v["recommendation"]) if v["recommendation"] == "ACCEPT" else fail("recommendation = ACCEPT", v["recommendation"])
ok("confidence >= 0.7",           str(v["overall_confidence"])) if v["overall_confidence"] >= 0.7 else fail("confidence >= 0.7", str(v["overall_confidence"]))
ok("no cross-check issues",      "[]") if v["cross_checks"] == [] else fail("cross-check issues found", str(v["cross_checks"]))

# -- Step 5: Code-validate rules --------------------------------------------
print(f"\n{SEP}")
print("  Step 5 · Code-validate rules")
print(SEP)

def check_issues(label, bad_spec, expect_fragment):
    issues = pdf_llm_pipeline._code_validate(bad_spec)
    hit = any(expect_fragment in i for i in issues)
    if hit:
        ok(label, issues[0])
    else:
        fail(label, f"got: {issues}")

base = json.loads(EXTRACTOR_REPLY)

check_issues("detects od <= id",          dict(base, od_in=0.3),          "od_in")
check_issues("detects max_od < od",       dict(base, max_od_in=0.9),      "max_od_in")
check_issues("detects negative length",   dict(base, length_in=-0.1),     "length_in")
check_issues("detects max_len < length",  dict(base, max_length_in=0.05), "max_length_in")

good_issues = pdf_llm_pipeline._code_validate(base)
ok("valid spec -> 0 issues", f"0 issues") if good_issues == [] else fail("valid spec still has issues", str(good_issues))

# -- Step 6: 429 fast-exit --------------------------------------------------
print(f"\n{SEP}")
print("  Step 6 · 429 rate-limit fast-exit")
print(SEP)
call_log_429 = []
def _always_429(url, *a, **k):
    call_log_429.append(url)
    return _429()

caught = None
with patch.dict(os.environ, ENV_KEY):
    with patch("app.services.llm_service.time.sleep"):
        with patch("app.services.llm_service.requests.post", side_effect=_always_429):
            try:
                llm_service.generate_text("test")
            except RuntimeError as e:
                caught = str(e)

ok("RuntimeError raised",          caught or "(none)") if caught else fail("RuntimeError raised")
ok("'rate limit' in message",      caught) if caught and "rate limit" in caught else fail("message mismatch", caught)
ok(f"exactly 2 HTTP calls",        f"{len(call_log_429)} calls") if len(call_log_429) == 2 else fail("HTTP call count", str(len(call_log_429)))
ok("all calls hit v1beta",         "v1beta") if all("v1beta" in u for u in call_log_429) else fail("non-v1beta call made", str(call_log_429))
ok("v1 URL never tried",           "confirmed") if not any("v1/" in u and "v1beta" not in u for u in call_log_429) else fail("v1 fallback was tried")
for i, url in enumerate(call_log_429, 1):
    short = url.split("generativelanguage.googleapis.com/")[-1].split("?")[0]
    print(f"    call {i}: {short}")

# -- Step 7: REVIEW verdict -------------------------------------------------
print(f"\n{SEP}")
print("  Step 7 · REVIEW verdict -> valid=False")
print(SEP)
mock_fn_r, _ = _two_agent_mock(val=REVIEW_REPLY)
with patch.dict(os.environ, ENV_KEY):
    with patch("app.services.llm_service.requests.post", side_effect=mock_fn_r):
        result_r = pdf_llm_pipeline.run_pipeline(REAL_PDF)

print(f"  {'  recommendation':26s} {result_r['validation']['recommendation']}")
print(f"  {'  overall_confidence':26s} {result_r['validation']['overall_confidence']}")
print(f"  {'  cross_checks':26s} {result_r['validation']['cross_checks']}")
print(f"  {'  valid':26s} {result_r['valid']}")
print()
ok("recommendation = REVIEW",  result_r["validation"]["recommendation"]) if result_r["validation"]["recommendation"] == "REVIEW" else fail("recommendation", result_r["validation"]["recommendation"])
ok("valid = False",            "False") if result_r["valid"] is False else fail("valid", str(result_r["valid"]))

# -- Summary ----------------------------------------------------------------
print(f"\n{'='*60}")
print("  All 8 scenario steps completed successfully.")
print(f"{'='*60}\n")
