# OCR Failure Diagnosis: Why Dimensions Aren't Being Found

## Common Reasons OCR Extraction Fails:

### 1. **Missing Diameter Symbol in Text** ⚠️
**Problem**: The code requires diameter symbols (`Ø`, `DIA`, `DIAMETER`, `OD`, `O.D.`) in the text.

**Example Failure**:
- Text: `"1.006-1.008 [25.553-25.603]"` (no diameter symbol)
- Result: ✗ Skipped (no symbol detected)

**Fix Applied**: 
- Now accepts dimensions that "look like diameters" (value in 0.1-10" range) even without explicit symbol
- Added fallback logic

### 2. **Bracket Rejection Too Strict** ⚠️
**Problem**: Lines with brackets `[25.553-25.603]` were being rejected entirely.

**Example Failure**:
- Text: `"Ø1.006-1.008 [25.553-25.603]"`
- Old behavior: ✗ Rejected (brackets present)
- New behavior: ✓ Accepted (brackets allowed if inch values present)

**Fix Applied**: 
- Only reject if entire line is brackets
- Allow lines like `"Ø1.006-1.008 [25.553-25.603]"`

### 3. **normalize_diameter_tokens Failing** ⚠️
**Problem**: If `normalize_diameter_tokens()` returns empty, dimension is skipped.

**Example Failure**:
- Text: `"Ø1.006-1.008 [25.553-25.603]"`
- `normalize_diameter_tokens()` fails (bracket rejection)
- Result: ✗ Skipped

**Fix Applied**: 
- Added fallback: if tolerance range was parsed OR diameter symbol exists, add dimension even if `normalize_diameter_tokens` fails

### 4. **Value Field Only Contains First Number** ⚠️
**Problem**: `value` field might be `1.006` but text has `"1.006-1.008"`.

**Fix Applied**: 
- Parse tolerance range from text FIRST
- Extract MAX value (1.008) before using value field

### 5. **OCR Text Format Issues** ⚠️
**Possible Issues**:
- OCR might read as separate fragments: `"1.006"` and `"1.008"` (two entries)
- Special characters might be corrupted: `"Ø"` → `"0"` or `"O"`
- Tolerance dash might be wrong character: `"1.006–1.008"` (en dash) vs `"1.006-1.008"` (hyphen)

**Current Regex**: `r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)'` (handles both hyphen and en dash)

## Enhanced Debugging Added:

### New Log Messages:
1. **All raw dimensions logged**:
   ```
   [RFQ_SCALE_CALIBRATION] Found X raw dimensions in inference_metadata
   [0] raw_dimension: text='...', value=X, unit=in
   ```

2. **Processing decisions logged**:
   ```
   [0] ✓ Added: 1.008 in - 'Ø1.006-1.008 [25.553]' (has_symbol=true, tolerance=true)
   [1] ✗ Skipped: bracketed=true, value=1.006 (out of range)
   [2] ✗ Skipped: has_symbol=false, looks_like_dia=false, value=null
   ```

3. **Tolerance parsing logged**:
   ```
   [0] ✓ Parsed tolerance range from text: 1.006-1.008, using MAX=1.008 in
   ```

## How to Debug:

### Step 1: Check Backend Logs
Look for these log lines when calling `/rfq/autofill`:

```
[RFQ_SCALE_CALIBRATION] Starting OCR diameter extraction (job_id=...)
[RFQ_SCALE_CALIBRATION] Found X raw dimensions in inference_metadata
[0] raw_dimension: text='...', value=..., unit=...
```

### Step 2: Identify Why Dimensions Are Skipped
Check for `✗ Skipped` messages and their reasons:
- `no symbol` → Text doesn't contain diameter keywords
- `bracketed=true` → Entire line is brackets (should be fixed now)
- `normalize failed` → `normalize_diameter_tokens` returned empty
- `out of range` → Value not in 0.01-10.0" range

### Step 3: Verify Text Format
Check if the actual OCR text matches expected patterns:
- Expected: `"Ø1.006-1.008 [25.553-25.603]"`
- Actual might be: `"1.006-1.008"` (no symbol)
- Actual might be: `"1.006 1.008"` (space instead of dash)
- Actual might be: `"1.0061.008"` (no separator)

### Step 4: Check part_summary.json
Inspect the actual `inference_metadata.raw_dimensions` array:
```json
{
  "inference_metadata": {
    "raw_dimensions": [
      {
        "text": "Ø1.006-1.008 [25.553-25.603]",
        "value": 1.006,
        "unit": "in"
      }
    ]
  }
}
```

## Expected Behavior After Fixes:

1. **Tolerance ranges parsed correctly**:
   - `"1.006-1.008"` → extracts MAX = 1.008 ✓

2. **Brackets no longer reject valid dimensions**:
   - `"Ø1.006-1.008 [25.553]"` → accepted ✓

3. **Fallback logic adds dimensions**:
   - Even if `normalize_diameter_tokens` fails, dimension added if tolerance parsed ✓

4. **Relaxed symbol requirement**:
   - Dimensions in 0.1-10" range accepted even without explicit symbol ✓

5. **Comprehensive logging**:
   - All dimensions logged with processing decisions ✓

## Next Steps:

1. **Test the endpoint** and check backend logs
2. **Look for** `[RFQ_SCALE_CALIBRATION]` log lines
3. **Identify** which dimensions are being skipped and why
4. **Share logs** if dimensions still not found
