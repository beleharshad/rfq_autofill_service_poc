# Calibration Fixes Summary

## Issues Fixed:

### 1. **Tolerance Range Parsing** ✅
**Problem**: Tolerance ranges like "1.006-1.008" were not being parsed correctly to extract MAX value.

**Fix**: 
- Parse tolerance range from text FIRST, before using value field
- Extract MAX value (1.008) for conservative sizing
- Added fallback: if `normalize_diameter_tokens` fails but tolerance range was parsed, still add the dimension

**Location**: `geometry_scale_calibration.py` lines 259-310

### 2. **Removed Duplicate Code** ✅
**Problem**: Unreachable duplicate code after return statement.

**Fix**: Removed duplicate code block (lines 451-500)

### 3. **Improved OCR Extraction Priority** ✅
**Fix Applied**:
- Check tolerance range in text FIRST (even if value field exists)
- Parse "1.006-1.008" → extract MAX = 1.008
- Fallback to value field if no tolerance range found

### 4. **Enhanced Logging** ✅
**Added Logs**:
- `Parsed tolerance range from text: X-Y, using MAX=Z`
- Better logging for tolerance vs non-tolerance dimensions

## Expected Behavior After Fixes:

### For Part 050CE0004 (PISTON):

1. **OCR Extraction**:
   ```
   [RFQ_SCALE_CALIBRATION] Found X raw dimensions in inference_metadata
   [0] Parsed tolerance range from text: 1.006-1.008, using MAX=1.008 in
   [RFQ_SCALE_CALIBRATION] After inference_metadata: X candidates
   ```

2. **Geometry Collection**:
   ```
   [RFQ_SCALE_CALIBRATION] Collected X geometry OD candidates (global_max=0.299)
   ```

3. **Matching**:
   ```
   [RFQ_SCALE_CALIBRATION] Valid match (dominant): OCR 1.008 in / Geometry 0.299 in = ratio 3.3700
   ```

4. **Calibration**:
   ```
   [RFQ_SCALE_CALIBRATION] selected_ratio=3.3700
   [RFQ_AUTOFILL] using_part_summary_scale_method=calibrated_from_ocr calibration_applied=true scale_factor=3.3700
   ```

5. **Final Dimensions**:
   - `finish_od_in = 1.008` ✅ (scaled from 0.299 * 3.37)
   - `finish_id_in = 0.443` ✅ (from OCR override)
   - `finish_len_in = 0.63` ✅ (from drawing)
   - `scale_calibration_applied = true` ✅
   - `matched_pairs >= 1` ✅

## Testing:

1. **Restart backend server** (already done)
2. **Call `/rfq/autofill` with part 050CE0004**
3. **Check logs** for:
   - Tolerance range parsing
   - OCR extraction counts
   - Matching results
   - Calibration application

## Next Steps if Still Failing:

If `matched_pairs=0` persists, check:

1. **Is `inference_metadata.raw_dimensions` populated?**
   - Check part_summary.json for this field
   - Verify it contains text with "1.006-1.008"

2. **Is tolerance regex matching?**
   - Pattern: `r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)'`
   - Should match: "1.006-1.008", "1.006–1.008" (en dash)

3. **Is diameter symbol detection working?**
   - Pattern: `r'[Ø∅]|DIA|DIAMETER|OD|O\.D\.'`
   - Should match: "Ø1.006-1.008", "DIA 1.006-1.008", etc.

4. **Are geometry ODs being collected?**
   - Check logs: `[RFQ_SCALE_CALIBRATION] Collected X geometry OD candidates`
   - Should see candidates > 0

5. **Is dominant-ratio filtering too strict?**
   - Check thresholds: `od_threshold=70% of global_max`, `length_threshold=40% of total_length`
   - May need to relax if geometry segments are small
