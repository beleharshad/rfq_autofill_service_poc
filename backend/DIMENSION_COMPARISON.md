# Dimension Comparison: Expected vs Actual Autofill Response

## Reference Drawing (Part 050CE0004 - PISTON)

### Correct Dimensions from Drawing:
- **Finish OD**: `1.006-1.008 inches` (Ø1.006-1.008 [25.553-25.603])
  - Should use **MAX value = 1.008 inches** (conservative sizing)
- **Finish ID**: `0.443 inches` (Ø.443 [11.252])
- **Finish Length**: `0.63 inches` (.63 [1.6])

## Current Autofill Response Issues:

### Expected Response (After Calibration):
```json
{
  "fields": {
    "finish_od_in": 1.008,  // Scaled from geometry 0.299 * 3.37 ≈ 1.008
    "finish_id_in": 0.443,  // From OCR override
    "finish_len_in": 0.63   // From OCR or geometry
  },
  "debug": {
    "scale_calibration_applied": true,
    "scale_factor_used": 3.37,  // ≈ 1.008 / 0.299
    "matched_pairs": 2,
    "scaled_xy": true,
    "scaled_z": false
  }
}
```

### Actual Response (Current):
```json
{
  "fields": {
    "finish_od_in": 0.299,  // ❌ WRONG - unscaled geometry
    "finish_id_in": null,   // ❌ MISSING
    "finish_len_in": 1.781  // ⚠️ May be incorrect
  },
  "debug": {
    "scale_calibration_applied": false,  // ❌ Calibration not running
    "scale_factor_used": null,
    "matched_pairs": 0,  // ❌ No OCR matches found
    "scaled_xy": null,
    "scaled_z": null
  }
}
```

## Root Cause Analysis:

### Issue 1: OCR Extraction Not Finding "1.006-1.008"
**Problem**: The tolerance range "1.006-1.008" is not being extracted from:
- `inference_metadata.raw_dimensions`
- Vendor quote extraction
- PDFSpecExtractor

**Expected Log Output**:
```
[RFQ_SCALE_CALIBRATION] Found X raw dimensions in inference_metadata
  [0] Parsed tolerance range: 1.006-1.008, using MAX=1.008 in
```

**Check**: Look for logs starting with `[RFQ_SCALE_CALIBRATION]` in backend console.

### Issue 2: Calibration Not Running or Failing
**Problem**: `scale_calibration_applied=false` means:
- OCR diameters not found (matched_pairs=0)
- OR calibration service not being called
- OR calibration returning None

**Expected Log Output**:
```
[RFQ_AUTOFILL] using_part_summary_scale_method=calibrated_from_ocr calibration_applied=true scale_factor=3.3700
```

**Check**: Verify calibration service is called in `/api/v1/rfq/autofill` endpoint.

### Issue 3: Geometry Scale Mismatch
**Problem**: Geometry OD = 0.299" but drawing shows 1.008"
- Scale factor needed: `1.008 / 0.299 ≈ 3.37`
- This matches the drawing scale notation "SCALE 3:1" (drawing is 3x larger)

## Debugging Steps:

### Step 1: Check OCR Extraction Logs
Look for these log lines in backend console:
```
[RFQ_SCALE_CALIBRATION] Starting OCR diameter extraction (job_id=...)
[RFQ_SCALE_CALIBRATION] Found X raw dimensions in inference_metadata
[RFQ_SCALE_CALIBRATION] After inference_metadata: X candidates
[RFQ_SCALE_CALIBRATION] Total extracted OCR diameter candidates: X
```

**If candidates = 0**: OCR extraction is failing. Check:
- Is `inference_metadata.raw_dimensions` populated?
- Does the PDF contain "1.006-1.008" text?
- Is tolerance range regex matching correctly?

### Step 2: Check Calibration Matching Logs
Look for:
```
[RFQ_SCALE_CALIBRATION] Collected X geometry OD candidates
[RFQ_SCALE_CALIBRATION] Matched OCR to geometry: X valid pairs
[RFQ_SCALE_CALIBRATION] selected_ratio=3.3700
```

**If matched_pairs = 0**: Matching logic is failing. Check:
- Are geometry ODs being collected? (should see candidates > 0)
- Is dominant-ratio filtering too strict?
- Are OCR and geometry values in compatible ranges?

### Step 3: Verify Calibration Applied
Look for:
```
[RFQ_AUTOFILL] using_part_summary_scale_method=calibrated_from_ocr calibration_applied=true
[RFQ_AUTOFILL_AFTER_CALIBRATION] scale_method=calibrated_from_ocr, max_od_in=1.008, ...
```

**If calibration_applied=false**: Check:
- Is `calibrated_summary` being returned from calibration service?
- Is `scale_factor` None?
- Is `scale_report.method` set to "calibrated_from_ocr"?

## Expected Calibration Flow:

1. **OCR Extraction**:
   - Find "1.006-1.008" in `raw_dimensions` text
   - Parse tolerance range → extract MAX = 1.008
   - Add to `ocr_diameters` list

2. **Geometry Collection**:
   - Find geometry OD = 0.299" from segments
   - Filter: length > 5% total, confidence > 0.6, OD >= 0.08"

3. **Matching**:
   - Match OCR 1.008" to geometry 0.299"
   - Calculate ratio = 1.008 / 0.299 = 3.37
   - Apply dominant-ratio filtering (should pass)

4. **Scaling**:
   - Apply scale_factor = 3.37 to XY dimensions
   - Scale OD: 0.299 * 3.37 = 1.008 ✓
   - Don't scale Z (if length already correct)

5. **Autofill**:
   - Use calibrated part_summary
   - Extract finish_od_in = 1.008 ✓
   - Extract finish_id_in = 0.443 (from OCR override if needed)

## Fix Verification:

After fixes, verify:
- ✅ `finish_od_in ≈ 1.008` (within tolerance)
- ✅ `finish_id_in ≈ 0.443`
- ✅ `finish_len_in ≈ 0.63` (or correct length from drawing)
- ✅ `scale_calibration_applied = true`
- ✅ `matched_pairs >= 1`
- ✅ `scale_factor_used ≈ 3.37`
