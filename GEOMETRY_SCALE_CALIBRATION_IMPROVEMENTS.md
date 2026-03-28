# Geometry Scale Calibration Improvements

## Summary

This document describes the improvements made to the geometry scale calibration system to ensure accurate dimension extraction and calibration.

## Changes Made

### 1. Runtime Verification Logging

**Added logging before/after calibration in both endpoints:**

- **POST /api/v1/rfq/autofill**: Logs `[RFQ_AUTOFILL_BEFORE_CALIBRATION]` and `[RFQ_AUTOFILL_AFTER_CALIBRATION]`
- **POST /api/v1/rfq/envelope**: Logs `[RFQ_ENVELOPE_BEFORE_CALIBRATION]` and `[RFQ_ENVELOPE_AFTER_CALIBRATION]`

Each log includes:
- `scale_method`: Current scale method (e.g., "estimated", "calibrated_from_ocr")
- `max_od_in`: Maximum OD across all segments
- `total_length_in`: Total length from totals or z_range

### 2. Improved OCR Diameter Extraction Quality

**New `normalize_diameter_tokens()` function:**
- Rejects bracketed values: `[2.38]`
- Rejects tolerance ranges unless explicitly parsed
- Rejects thread specs: `UNC`, `UN-`, `THREAD`, `Mx`, `NPT`, `BSP`
- Rejects radii: `R0.5`, `R.5`, `RAD`
- Prefers values with `Ø`, `DIA`, `O.D.` in same line

**Confidence scoring:**
- `+0.3` if line contains `Ø` or `DIA`
- `+0.2` if line contains `OD` or `O.D.`
- `-0.4` if line has tolerance range
- `-0.5` if line has brackets
- `-0.4` if line looks like thread spec

**Returns top 8 candidates by confidence (score >= 0.3)**

### 3. Improved Matching Logic

**Updated `match_ocr_to_geometry()`:**
- Skips geometry ODs that are global_max_od (raw stock suspect)
- Skips geometry ODs < 0.08" (noise)
- Requires ratio to be within [0.5, 5.0] range
- Keeps multiple matched pairs even if OCR repeats

**Updated `collect_geometry_ods()`:**
- Adds `is_global_max` flag to each geometry OD candidate
- Filters out ODs < 0.08" (noise threshold)

### 4. Fixed Clustering Criteria

**Updated `calculate_scale_factor()`:**
- **For >=3 pairs**: Requires at least 2 ratios within ±8% of median
- **For exactly 2 pairs**: Requires they match within ±6%
- Returns: `(scale_factor, confidence, matched_pairs, ratios)`
- Logs all ratios and chosen median

### 5. Comprehensive Scaling

**Updated `apply_scaling()`:**
- Scales both new and old totals keys:
  - `total_length_in`, `total_od_area_in2`, `total_id_area_in2`, `total_volume_in3`
  - Legacy: `od_area_in2`, `id_area_in2`, `volume_in3`
- Scales feature-derived diameters in `features.holes` and `features.slots`
- Scales segment-level volume and area fields

### 6. Debug Fields in Response

**Added to `RFQAutofillDebug` and `EnvelopeDebug`:**
- `scale_calibration_applied: Optional[bool]` - Whether calibration was applied
- `scale_factor_used: Optional[float]` - Scale factor used (null if not applied)
- `matched_pairs: Optional[int]` - Number of matched OCR-geometry pairs

## Example Log Output

### Successful Calibration

```
[RFQ_AUTOFILL_BEFORE_CALIBRATION] scale_method=estimated, max_od_in=0.2990, total_length_in=0.4720
[RFQ_SCALE_CALIBRATION] Extracted 3 OCR diameter candidates (top 8)
  [0] OCR: 0.9400 in - FINISH OD 0.94 (conf: 0.80)
  [1] OCR: 0.5000 in - FINISH ID 0.50 (conf: 0.80)
  [2] OCR: 0.7500 in - OD 0.75 (conf: 0.75)
[RFQ_SCALE_CALIBRATION] Collected 2 geometry OD candidates (global_max=0.2990)
  [0] Geometry: 0.2990 in (seg_idx: 0, len: 0.4720, conf: 0.85, max_od: True)
  [1] Geometry: 0.2500 in (seg_idx: 1, len: 0.1200, conf: 0.70, max_od: False)
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.9400 in / Geometry 0.2990 in = ratio 3.1445
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.5000 in / Geometry 0.2500 in = ratio 2.0000
[RFQ_SCALE_CALIBRATION] All ratios: [2.0000, 3.1445]
[RFQ_SCALE_CALIBRATION] 2 ratios do not match (spread: 36.45%), cannot calibrate
[RFQ_AUTOFILL_AFTER_CALIBRATION] scale_calibration_applied=false, using original geometry
```

### Successful Calibration (with clustering)

```
[RFQ_AUTOFILL_BEFORE_CALIBRATION] scale_method=estimated, max_od_in=0.2990, total_length_in=0.4720
[RFQ_SCALE_CALIBRATION] Extracted 4 OCR diameter candidates (top 8)
  [0] OCR: 0.9400 in - FINISH OD 0.94 (conf: 0.80)
  [1] OCR: 0.9350 in - OD 0.935 (conf: 0.75)
  [2] OCR: 0.5000 in - FINISH ID 0.50 (conf: 0.80)
[RFQ_SCALE_CALIBRATION] Collected 2 geometry OD candidates (global_max=0.2990)
  [0] Geometry: 0.2990 in (seg_idx: 0, len: 0.4720, conf: 0.85, max_od: True)
  [1] Geometry: 0.2500 in (seg_idx: 1, len: 0.1200, conf: 0.70, max_od: False)
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.9400 in / Geometry 0.2990 in = ratio 3.1445
[RFQ_SCALE_CALIBRATION] Matched: OCR 0.9350 in / Geometry 0.2990 in = ratio 3.1271
[RFQ_SCALE_CALIBRATION] All ratios: [3.1271, 3.1445]
[RFQ_SCALE_CALIBRATION] 2 ratios match within ±6%: [3.1271, 3.1445]
[RFQ_SCALE_CALIBRATION] Scale factor: 3.1358 (confidence: 0.85)
[RFQ_SCALE_CALIBRATION] Geometry scaling complete
[RFQ_AUTOFILL_AFTER_CALIBRATION] scale_method=calibrated_from_ocr, max_od_in=0.9377, total_length_in=1.4801, scale_factor=3.1358
```

## How to Verify Response Debug Fields

### Using curl

```bash
# Test autofill endpoint
curl -X POST http://localhost:8000/api/v1/rfq/autofill \
  -H "Content-Type: application/json" \
  -d '{
    "rfq_id": "TEST-001",
    "part_no": "050CE0004",
    "mode": "GEOMETRY",
    "source": {
      "job_id": "your-job-id"
    },
    "tolerances": {
      "rm_od_allowance_in": 0.26,
      "rm_len_allowance_in": 0.10
    }
  }' | jq '.debug | {scale_calibration_applied, scale_factor_used, matched_pairs}'
```

Expected output:
```json
{
  "scale_calibration_applied": true,
  "scale_factor_used": 3.1358,
  "matched_pairs": 2
}
```

### Using Python

```python
import requests

response = requests.post(
    "http://localhost:8000/api/v1/rfq/autofill",
    json={
        "rfq_id": "TEST-001",
        "part_no": "050CE0004",
        "mode": "GEOMETRY",
        "source": {"job_id": "your-job-id"},
        "tolerances": {
            "rm_od_allowance_in": 0.26,
            "rm_len_allowance_in": 0.10
        }
    }
)

data = response.json()
debug = data["debug"]

print(f"Scale calibration applied: {debug['scale_calibration_applied']}")
print(f"Scale factor used: {debug['scale_factor_used']}")
print(f"Matched pairs: {debug['matched_pairs']}")
```

### Frontend Integration

The frontend can check these fields to display calibration status:

```typescript
const response = await api.rfqAutofill(request);
const { debug } = response;

if (debug.scale_calibration_applied) {
  console.log(`Geometry calibrated with scale factor: ${debug.scale_factor_used}`);
  console.log(`Used ${debug.matched_pairs} matched pairs`);
} else {
  console.log("Geometry scale calibration not applied (using original geometry)");
}
```

## Testing Checklist

1. **Test with part that has correct OCR dimensions:**
   - Verify `scale_calibration_applied = true`
   - Verify `scale_factor_used` is reasonable (0.5 - 5.0)
   - Verify `matched_pairs >= 2`
   - Check logs show BEFORE/AFTER calibration values

2. **Test with part that has poor OCR quality:**
   - Verify `scale_calibration_applied = false`
   - Verify `scale_factor_used = null`
   - Verify `matched_pairs = 0` or insufficient clustering
   - Check logs show calibration failure reason

3. **Test envelope endpoint:**
   - Verify same debug fields are populated
   - Check logs show `[RFQ_ENVELOPE_BEFORE_CALIBRATION]` and `[RFQ_ENVELOPE_AFTER_CALIBRATION]`

4. **Verify geometry scaling:**
   - Check that `max_od_in` and `total_length_in` change after calibration
   - Verify all totals keys are scaled (both old and new keys)
   - Verify feature diameters are scaled if present

## Files Modified

1. `backend/app/services/geometry_scale_calibration.py` - Core calibration logic improvements
2. `backend/app/api/rfq.py` - Added runtime verification and debug fields
3. `backend/app/api/envelope.py` - Added runtime verification logging
4. `backend/app/services/geometry_envelope_service.py` - Added calibration before envelope computation
5. `backend/app/models/rfq_autofill.py` - Added debug fields to `RFQAutofillDebug`
6. `backend/app/models/rfq_envelope.py` - Added debug fields to `EnvelopeDebug`

## Key Improvements Summary

1. ✅ Runtime verification logging before/after calibration
2. ✅ Improved OCR diameter extraction with quality filtering
3. ✅ Better matching logic avoiding raw-stock OD and noise
4. ✅ Fixed clustering criteria with proper ratio validation
5. ✅ Comprehensive scaling of all geometry keys
6. ✅ Debug fields in response for frontend visibility
