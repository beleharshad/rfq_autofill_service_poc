"""One-off probe: load part_summary for job 6469088f and run band classifier debug."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.rfq_autofill_service import RFQAutofillService, _debug_run_band_classifier

JOB_ID = "6469088f-51ca-4923-9f28-99e2c143bcf3"
SUMMARY_PATH = Path("data/jobs") / JOB_ID / "outputs" / "part_summary.json"

if not SUMMARY_PATH.exists():
    print(f"ERROR: {SUMMARY_PATH} not found")
    sys.exit(1)

with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
    ps = json.load(f)

segments = ps.get("segments", [])
z_range = ps.get("z_range")
totals = ps.get("totals")
unit_len = "in"

print(f"Loaded {len(segments)} segments from {SUMMARY_PATH}")
print(f"z_range = {z_range}")
print(f"totals  = {json.dumps(totals, indent=2) if totals else 'None'}")

# Compute total length
svc = RFQAutofillService()
total_len = svc._compute_total_span(segments, unit_len)
print(f"\ntotal_len (from segments span) = {total_len:.6f}")

if totals and isinstance(totals, dict):
    tl = totals.get("total_length_in")
    if tl is not None:
        print(f"totals.total_length_in          = {float(tl):.6f}")

if z_range and isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
    zlen = float(z_range[1]) - float(z_range[0])
    print(f"z_range span                    = {zlen:.6f}")

# ── RAW segment OD analysis ───────────────────────────────────────────
def to_in(v):
    return float(v)

raw_ods = []
for s in segments:
    if not isinstance(s, dict):
        continue
    od = s.get("od_diameter")
    if od is not None:
        raw_ods.append(float(od))

raw_ods_sorted = sorted(raw_ods)
print(f"\n{'='*70}")
print(f"  RAW SEGMENT OD ANALYSIS  ({len(raw_ods)} OD values)")
print(f"{'='*70}")
print(f"  min OD = {min(raw_ods):.6f}")
print(f"  max OD = {max(raw_ods):.6f}")
print(f"\n  Smallest 10 ODs: {[f'{v:.4f}' for v in raw_ods_sorted[:10]]}")
print(f"  Largest  10 ODs: {[f'{v:.4f}' for v in raw_ods_sorted[-10:]]}")

# Unique ODs rounded to 4 decimals
unique_ods = sorted(set(round(v, 4) for v in raw_ods))
print(f"\n  Unique ODs ({len(unique_ods)}): {[f'{v:.4f}' for v in unique_ods]}")

# Check for ODs in 1.15-1.35 range
in_range = [v for v in raw_ods if 1.15 <= v <= 1.35]
print(f"\n  ODs in [1.15, 1.35]: {len(in_range)} values")
if in_range:
    print(f"    values: {sorted(set(f'{v:.4f}' for v in in_range))}")

# ── Run band classifier ──────────────────────────────────────────────
print()
_debug_run_band_classifier(segments, total_len, unit_len)

# ── Detailed band table ──────────────────────────────────────────────
bands = svc.build_od_bands(segments, total_len, unit_len)
svc.classify_bands(bands, total_len, segments, unit_len)
main_band = svc.score_main_body_bands(bands)

print(f"\n{'='*70}")
print(f"  DETAILED BAND TABLE")
print(f"{'='*70}")
scored = sorted(bands, key=lambda b: b.get("_mb_score", 0), reverse=True)
hdr = (f"{'od_key':>8s} {'z_min':>8s} {'z_max':>8s} {'z_span':>8s} "
       f"{'cov_ratio':>9s} {'z_cont':>8s} {'z_center':>8s} {'type':>10s} {'score':>8s}")
print(f"  {hdr}")
print(f"  {'-'*len(hdr)}")
for b in scored:
    print(
        f"  {b['od_key']:8.4f} {b['z_min']:8.4f} {b['z_max']:8.4f} {b['z_span']:8.4f} "
        f"{b['coverage_ratio']:9.4f} {b['z_continuity_ratio']:8.4f} {b['z_center_ratio']:8.4f} "
        f"{b.get('feature_type','?'):>10s} {b.get('_mb_score',0):8.4f}"
    )

# ── Summary answers ───────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"  SUMMARY")
print(f"{'='*70}")
if main_band:
    print(f"  main_band_od       = {main_band['od_key']:.4f}")
    print(f"  main_band_z_span   = {main_band['z_span']:.4f}")
    print(f"  main_band_score    = {main_band.get('_mb_score', 0):.4f}")

env_max = max(b["od_key"] for b in bands) if bands else 0
print(f"  band_envelope_max  = {env_max:.4f}")

flange = [b for b in bands if b.get("feature_type") == "FLANGE"]
if flange:
    print(f"  flange_candidates  = {len(flange)}:")
    for fb in flange:
        reasons = svc._flange_reasons(fb)
        print(f"    od={fb['od_key']:.4f} z_span={fb['z_span']:.4f} reasons={reasons}")
else:
    print(f"  flange_candidates  = 0 (none)")

# Check for 1.15-1.35 band
band_in_range = [b for b in bands if 1.15 <= b["od_key"] <= 1.35]
print(f"\n  Q(A): Bands with od_key in [1.15, 1.35]: {len(band_in_range)}")
if band_in_range:
    for b in band_in_range:
        print(f"    od_key={b['od_key']:.4f} type={b.get('feature_type')} score={b.get('_mb_score',0):.4f}")
else:
    print("    NONE — no geometry band exists near 1.240\"")
    print("    => Finish OD=1.240 CANNOT come from geometry alone; OCR is required.")
