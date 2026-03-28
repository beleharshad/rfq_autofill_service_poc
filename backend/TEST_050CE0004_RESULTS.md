# Test Results for Part 050CE0004

## Test Date
February 21, 2026

## Expected Values (from Excel file: RFQ-2025-01369 - R1.xlsx, Row 8)

| Dimension | Excel Value | Drawing Value (from image) |
|-----------|-------------|---------------------------|
| Finish OD | 1.24" | 1.008" (MAX from 1.006-1.008) |
| Finish ID | 0.433" | 0.443" |
| Finish Length | 0.63" | 0.63" |
| RM OD | ~1.378" (35/25.4) | - |
| RM Length | ~0.98" (0.63 + 0.35) | - |

## Test Results

### Job Used
- **Job ID**: `0002a0d8-aade-4398-9b1a-b292b53509f5`
- **PDF File**: `050dz0017_C.pdf` (NOT part 050ce0004)
- **Part No in part_summary**: N/A (not found)

### OCR Extraction Status

**Status**: ❌ FAILED - No OCR diameters extracted

**Details**:
- `inference_metadata.raw_dimensions`: 0 entries
- PDFSpecExtractor returned:
  - `finish_od_in`: None
  - `finish_id_in`: None
  - `finish_len_in`: 174.0 (likely in mm, not inches)
- Total OCR diameter candidates: **0**

**Root Cause**:
1. No job found with part number `050ce0004`
2. PDFSpecExtractor failed to extract diameters from PDF
3. No `raw_dimensions` in `inference_metadata`

### Calibration Status

**Status**: ❌ NOT APPLIED

**Reason**: No OCR diameters found, cannot calibrate

**Details**:
- `scale_factor`: None
- `matched_pairs`: 0
- `scale_method`: "estimated"
- `scale_calibration_applied`: None

### Autofill Results

| Dimension | Autofill Result | Excel Expected | Drawing Expected | Status |
|-----------|----------------|----------------|------------------|--------|
| Finish OD | 2.5210" | 1.2400" | 1.0080" | ❌ MISMATCH |
| Finish ID | 0.8170" | 0.4330" | 0.4430" | ❌ MISMATCH |
| Finish Length | 0.4050" | 0.6300" | 0.6300" | ❌ MISMATCH |
| RM OD | 2.8000" | 1.3780" | - | ❌ MISMATCH |
| RM Length | 0.8000" | 0.9800" | - | ❌ MISMATCH |

**Differences**:
- Finish OD: +1.2810" from Excel, +1.5130" from Drawing
- Finish ID: +0.3840" from Excel, +0.3740" from Drawing
- Finish Length: -0.2250" from Excel and Drawing
- RM OD: +1.4220" from Excel
- RM Length: -0.1800" from Excel

### Field Sources

- `finish_od_in`: `part_summary.main_segment.od_diameter` (geometry)
- `finish_id_in`: `geometry` (geometry)
- `finish_len_in`: `part_summary.z_range` (geometry)

## Issues Identified

1. **No Job with Part 050CE0004**: The test used a different job (`050dz0017`) because no job with part `050ce0004` was found.

2. **OCR Extraction Failure**: PDFSpecExtractor is not extracting diameters from the PDF:
   - Returns `finish_od_in: None`
   - Returns `finish_id_in: None`
   - Only returns `finish_len_in: 174.0` (likely in wrong units)

3. **No Raw Dimensions**: The `part_summary.inference_metadata` has no `raw_dimensions` array, which is the primary source for OCR extraction.

4. **Geometry Scale Issue**: The geometry values are significantly different from expected:
   - Finish OD is 2.5x larger than expected
   - This suggests the geometry needs calibration, but calibration cannot run without OCR data

## Recommendations

1. **Find or Create Job with Part 050CE0004**:
   - Upload a PDF drawing for part `050ce0004`
   - Run the pipeline to generate `part_summary.json`
   - Ensure `inference_metadata.raw_dimensions` is populated

2. **Fix PDFSpecExtractor**:
   - Investigate why `finish_od_in` and `finish_id_in` are returning `None`
   - Check if the PDF contains the dimensions but they're not being parsed correctly
   - Verify unit conversion (174.0 for length suggests mm, not inches)

3. **Improve OCR Extraction**:
   - Ensure `raw_dimensions` are populated during PDF processing
   - Add fallback logic to extract dimensions directly from PDF text/OCR
   - Improve tolerance range parsing (e.g., "1.006-1.008" → 1.008)

4. **Test with Correct Part**:
   - Once a job with part `050ce0004` is available, re-run the test
   - Verify OCR extraction works correctly
   - Verify calibration applies the correct scale factor
   - Verify autofill results match Excel values

## Next Steps

1. ✅ Fixed FileStorage issue (`fs.data_root` → `fs.get_inputs_path()`)
2. ⏳ Find or create job with part `050ce0004`
3. ⏳ Fix PDFSpecExtractor to extract diameters correctly
4. ⏳ Re-run test with correct part and verify results
