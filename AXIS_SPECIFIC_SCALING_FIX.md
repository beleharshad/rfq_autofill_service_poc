# Axis-Specific Scaling Fix for Geometry Scale Calibration

## Problem

Calibration was applying the scale factor uniformly to all dimensions (XY and Z), causing incorrect length inflation. Example:
- Geometry length: 1.781 in
- After uniform scaling (3.1445): ~5.60 in ❌ **INCORRECT**

The issue: Mechanical drawings often have correct Z scale but incorrect XY scale (or vice versa). Uniform scaling breaks the correct dimension.

## Solution: Axis-Specific Scaling

Implemented separate scaling for XY (diameters) and Z (lengths):

1. **Always scale XY (diameters)** by `xy_scale` (from OCR/geometry diameter ratio)
2. **Conditionally scale Z (lengths)** only when geometry length is inconsistent with OCR length

### Heuristic for Z Scaling

- Extract OCR overall length from `inference_metadata` or PDF
- Compare with `totals.total_length_in`
- If difference ≤ ±10%: **Don't scale Z** (Z is already correct)
- If difference > ±10%: **Scale Z** by `ocr_length / geometry_length`

### Scaling Formulas

- **Areas**: `xy_scale^2` (always)
- **Volumes**: `xy_scale^2 * z_scale` (z_scale = 1.0 if Z not scaled)

## Changes Made

### 1. New Method: `extract_ocr_length()`

Extracts OCR overall length from:
- `inference_metadata.raw_dimensions` (checks for LENGTH/LEN/OAL keywords)
- PDF extraction via `PDFSpecExtractor` (`finish_len_in`)

### 2. Updated `apply_scaling()` Method

**Before:**
- Single `scale_factor` applied to all dimensions
- Areas: `scale_factor^2`
- Volumes: `scale_factor^3`

**After:**
- Accepts `xy_scale` and `z_scale` separately
- Always scales XY (diameters, hole diameters, slot widths)
- Conditionally scales Z (z_start, z_end, z_range, total_length_in, slot lengths)
- Areas: `xy_scale^2`
- Volumes: `xy_scale^2 * z_scale`
- Returns `(modified_part_summary, scaled_xy, scaled_z)`

### 3. Updated `calibrate_geometry_scale()` Method

**Before:**
- Calculated single scale factor
- Applied uniformly to all dimensions

**After:**
- Calculates `xy_scale` from diameter ratios
- Extracts OCR length and compares with geometry length
- Determines `z_scale`:
  - If length difference ≤ ±10%: `z_scale = 1.0` (no Z scaling)
  - Otherwise: `z_scale = ocr_length / geometry_length`
- Calls `apply_scaling()` with both scales
- Updates `scale_report` with `xy_scale`, `z_scale`, `scaled_xy`, `scaled_z`

### 4. Debug Fields Added

**`RFQAutofillDebug` and `EnvelopeDebug`:**
- `scaled_xy: Optional[bool]` - Whether XY dimensions were scaled
- `scaled_z: Optional[bool]` - Whether Z dimensions were scaled

## Example Log Output

### Case 1: Z Scale NOT Applied (Length Already Correct)

```
[RFQ_SCALE_CALIBRATION] Found OCR length in inference_metadata: 1.7810 in
[RFQ_SCALE_CALIBRATION] Z scale: geometry length 1.7810 in matches OCR length 1.7810 in (diff: 0.00%), NOT scaling Z
[RFQ_SCALE_CALIBRATION] Applying axis-specific scaling: xy_scale=3.1445, z_scale=1.0000
[RFQ_SCALE_CALIBRATION] Geometry scaling complete (scaled_xy=True, scaled_z=False)
[RFQ_AUTOFILL_AFTER_CALIBRATION] ... xy_scale=3.1445, z_scale=1.0000, scaled_xy=True, scaled_z=False
```

**Result:**
- Finish OD: 0.299 → 0.940 ✅ (scaled by 3.1445)
- Finish Length: 1.781 → 1.781 ✅ (NOT scaled, remains correct)

### Case 2: Z Scale Applied (Length Incorrect)

```
[RFQ_SCALE_CALIBRATION] Found OCR length in PDF: 2.5000 in
[RFQ_SCALE_CALIBRATION] Z scale: geometry length 1.2000 in differs from OCR length 2.5000 in (diff: 108.33%), scaling Z by 2.0833
[RFQ_SCALE_CALIBRATION] Applying axis-specific scaling: xy_scale=3.1445, z_scale=2.0833
[RFQ_SCALE_CALIBRATION] Geometry scaling complete (scaled_xy=True, scaled_z=True)
[RFQ_AUTOFILL_AFTER_CALIBRATION] ... xy_scale=3.1445, z_scale=2.0833, scaled_xy=True, scaled_z=True
```

**Result:**
- Finish OD: 0.299 → 0.940 ✅ (scaled by 3.1445)
- Finish Length: 1.200 → 2.500 ✅ (scaled by 2.0833)

## Expected Result

For the example job:
- **OCR OD**: 0.94 in
- **Geometry OD**: 0.299 in → **Scaled to 0.940 in** ✅
- **OCR Length**: 1.781 in (or not found)
- **Geometry Length**: 1.781 in → **Remains 1.781 in** ✅ (NOT scaled if within ±10%)

## Files Modified

1. `backend/app/services/geometry_scale_calibration.py`
   - Added `extract_ocr_length()` method
   - Updated `apply_scaling()` to accept `xy_scale` and `z_scale`
   - Updated `calibrate_geometry_scale()` to determine Z scale conditionally

2. `backend/app/api/rfq.py`
   - Extracts `scaled_xy` and `scaled_z` from scale_report
   - Adds debug fields to response

3. `backend/app/services/geometry_envelope_service.py`
   - Extracts `scaled_xy` and `scaled_z` from scale_report
   - Adds debug fields to response

4. `backend/app/models/rfq_autofill.py`
   - Added `scaled_xy` and `scaled_z` fields to `RFQAutofillDebug`

5. `backend/app/models/rfq_envelope.py`
   - Added `scaled_xy` and `scaled_z` fields to `EnvelopeDebug`

## Testing

After this fix, `/rfq/autofill` response should show:
- `scale_calibration_applied: true`
- `scale_factor_used: ~3.14` (XY scale)
- `scaled_xy: true`
- `scaled_z: false` (if geometry length matches OCR length within ±10%)
- `finish_length` should remain correct (not inflated)

The calibration will now preserve correct Z dimensions while fixing incorrect XY dimensions.
