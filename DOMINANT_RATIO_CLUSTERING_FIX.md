# Dominant-Ratio Clustering Fix for Geometry Scale Calibration

## Problem

The calibration algorithm was rejecting valid scale factors because it treated all diameter matches equally. When multiple OCR-to-geometry ratios differed (e.g., `[3.1445, 2.0]`), the algorithm rejected calibration due to spread. However, the `2.0` ratio came from a small feature diameter (shoulder/groove), not the main body, which should be the only scale reference.

## Solution: Dominant-Ratio Clustering

Implemented filtering to only accept matches to main body segments:

1. **Filtering Criteria:**
   - `geometry_od >= 70% of global_max_geometry_od` (main body, not small features)
   - `segment_length >= 40% of total_length` (significant segment, not tiny shoulder)

2. **Discard Small Feature Matches:**
   - Matches to segments that don't meet the above criteria are discarded
   - Only dominant segment matches are used for calibration

3. **Simplified Clustering:**
   - If at least 1 valid pair exists → calibrate using median ratio
   - Removed "spread rejection" when only dominant cluster exists
   - Single valid pair is acceptable (confidence = 0.8)

## Changes Made

### 1. `match_ocr_to_geometry()` Method

**Before:**
- Returned all matched pairs
- No filtering by segment dominance

**After:**
- Returns `(valid_pairs, discarded_pairs)` tuple
- Filters matches based on:
  - OD threshold: `geometry_od >= 70% * global_max_od`
  - Length threshold: `segment_length >= 40% * total_length`
- Logs which matches are valid vs discarded

### 2. `calculate_scale_factor()` Method

**Before:**
- Required 2+ pairs
- Rejected if ratios didn't cluster within ±6-8%
- Complex clustering logic

**After:**
- Accepts `valid_pairs` and `discarded_pairs` separately
- If at least 1 valid pair exists → calibrate using median ratio
- Confidence: 0.9 (3+ pairs), 0.85 (2 pairs), 0.8 (1 pair)
- Logs: `valid_pairs`, `discarded_pairs`, `selected_ratio`, `scale_factor`

### 3. `calibrate_geometry_scale()` Method

**Before:**
- Called `match_ocr_to_geometry()` without thresholds
- Used all matched pairs for clustering

**After:**
- Calculates `global_max_od` from geometry ODs
- Passes `total_length` and `global_max_od` to `match_ocr_to_geometry()`
- Uses only `valid_pairs` for scale factor calculation
- Updates `scale_report` with `valid_pairs` and `discarded_pairs` counts

## Example Log Output

### Before Fix (Failed Calibration)

```
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.9400 in / Geometry 0.2990 in = ratio 3.1445
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.5000 in / Geometry 0.2500 in = ratio 2.0000
[RFQ_SCALE_CALIBRATION] All ratios: [2.0000, 3.1445]
[RFQ_SCALE_CALIBRATION] 2 ratios do not match (spread: 36.45%), cannot calibrate
```

### After Fix (Successful Calibration)

```
[RFQ_SCALE_CALIBRATION] Dominant-ratio filtering thresholds: od_threshold=0.2093, length_threshold=0.1888
[RFQ_SCALE_CALIBRATION] Valid match (dominant): OCR 0.9400 in / Geometry 0.2990 in = ratio 3.1445
[RFQ_SCALE_CALIBRATION] Discarded match (small feature): OCR 0.5000 in / Geometry 0.2500 in = ratio 2.0000
[RFQ_SCALE_CALIBRATION]
valid_pairs=1
discarded_pairs=1
selected_ratio=3.1445
scale_factor=3.1445
All valid ratios: [3.1445]
[RFQ_SCALE_CALIBRATION] Applying scale factor 3.1445 to geometry
[RFQ_SCALE_CALIBRATION] Geometry scaling complete
  Valid pair 0: OCR 0.9400 in / Geo 0.2990 in = 3.1445
```

## Expected Result

For the example job:
- **OCR OD**: 0.94 in
- **Geometry OD**: 0.299 in (main body segment)
- **Scale Factor**: 0.94 / 0.299 ≈ **3.14**
- **Calibration**: ✅ **SUCCESS** (was failing before)

## Files Modified

- `backend/app/services/geometry_scale_calibration.py`
  - `match_ocr_to_geometry()`: Added dominant-ratio filtering
  - `calculate_scale_factor()`: Simplified to accept single valid pair
  - `calibrate_geometry_scale()`: Passes thresholds and uses valid pairs only

## Testing

After this fix, `/rfq/autofill` response should show:
- `scale_calibration_applied: true`
- `scale_factor_used: ~3.14`
- `matched_pairs: 1` (or more if multiple dominant segment matches)

The calibration will now succeed even when small feature diameters produce different ratios, as long as at least one match to the main body segment exists.
