"""
Shows what the agents WOULD send to Gemini, and what OCR produced.
Runs entirely offline — no API calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.services import pdf_llm_pipeline

PDF = Path(__file__).parent.parent / "data/jobs/bff75f7b-d6f8-4786-8e42-2a38b7983628/inputs/source.pdf"

SEP = "=" * 56

# ── OCR text ────────────────────────────────────────────────
print(SEP)
print("  STEP 1 — OCR TEXT EXTRACTED FROM PDF")
print(SEP)
ocr = pdf_llm_pipeline._extract_pdf_text(PDF)
print(f"  Characters : {len(ocr)}")
print()
print(ocr[:2000])
print()

# ── Show all diameter candidates visible in OCR ──────────────
import re
phis = re.findall(r'(?:D|Phi|O|ø)?\s*(\d+\.\d+)', ocr)
print(SEP)
print("  NUMERIC VALUES FOUND IN OCR (diameter candidates)")
print(SEP)
all_nums = sorted(set(float(x) for x in phis if 0.05 <= float(x) <= 30), reverse=True)
for n in all_nums:
    tag = ""
    if n == 1.240: tag = "  <-- EXPECTED Finish OD"
    if n == 1.380: tag = "  <-- EXPECTED MAX OD (RM)"
    if n == 0.430: tag = "  <-- EXPECTED Finish ID"
    if n == 0.630: tag = "  <-- EXPECTED Finish Length"
    print(f"    {n:.3f}{tag}")

print()

# ── System prompt preview ────────────────────────────────────
print(SEP)
print("  AGENT 1 SYSTEM PROMPT  (what Gemini is told to do)")
print(SEP)
for line in pdf_llm_pipeline._EXTRACTOR_SYSTEM.splitlines()[:40]:
    print(f"  {line}")
print(f"  ... ({len(pdf_llm_pipeline._EXTRACTOR_SYSTEM)} chars total)")
print()

# ── Vision prompt highlights ─────────────────────────────────
print(SEP)
print("  AGENT 1 VISION PROMPT STRATEGY  (orientation-agnostic)")
print(SEP)
for line in pdf_llm_pipeline._EXTRACTOR_VISION_PROMPT.splitlines():
    if line.strip().startswith("STEP") or "axis" in line.lower() or "perpendicular" in line.lower() or "ANY" in line or "largest" in line.lower():
        print(f"  {line.rstrip()}")
print()

print(SEP)
print("  WHAT GEMINI SHOULD EXTRACT  (expected from Excel)")
print(SEP)
print("  Finish OD     : 1.240 in   (largest Phi on part profile)")
print("  MAX OD  (RM)  : 1.380 in   (from RM/stock table)")
print("  Finish ID     : 0.430 in   (smallest bore diameter)")
print("  Finish Length : 0.630 in   (overall end-to-end span)")
print()
print("  API quota exhausted for today. Re-run tomorrow to see live output.")
print(SEP)
