# Design Specification: PDF→3D Conversion Modes

## Overview

This document specifies the design for two distinct PDF→3D conversion modes that share a common downstream pipeline. The modes are kept separate to maintain clear responsibilities and enable incremental development.

## Architecture Principles

1. **Separation of Concerns**: Mode A (Assisted Manual) and Mode B (Auto Convert) are independent input methods that converge at the Profile2D/TurnedPartStack stage.
2. **Shared Pipeline**: Both modes feed the same downstream processing pipeline.
3. **Incremental Development**: Mode B is experimental and can be developed/debugged independently.
4. **Debuggability**: All intermediate results are stored and traceable.
5. **Graceful Degradation**: Mode B falls back to Mode A UI when confidence is low.

## Mode A: Assisted Manual

### Purpose
PDF serves as a visual reference only. User manually enters dimensions through a guided form.

### Current State
- ✅ PDF viewer with page navigation
- ✅ Manual Profile2D input form (line segments)
- ✅ Manual Stack Input form (segment dimensions)
- ✅ Both forms can load sample presets

### Design Requirements

#### Input Flow
1. User uploads PDF → stored in `inputs/`
2. User views PDF in viewer
3. User selects input method:
   - **Option A1**: Profile2D Sketch (line segments + arcs)
   - **Option A2**: Segment Stack (z_start, z_end, od_diameter, id_diameter)
4. User enters dimensions manually (guided by PDF visual reference)
5. System validates input
6. System processes through shared pipeline

#### Data Storage
- `inputs/{pdf_files}` - Original PDFs (read-only reference)
- `outputs/manual_input.json` - User-entered data (Profile2D or Stack)
- `outputs/part_summary.json` - Final results

#### API Endpoints (Existing)
- `POST /jobs/{job_id}/profile2d` - Submit Profile2D primitives
- `POST /jobs/{job_id}/stack-input` - Submit segment stack
- `POST /jobs/{job_id}/run` - Run analysis (Stack mode only)

#### UI Components (Existing)
- PDF viewer with zoom/navigation
- Profile2D sketch form
- Segment stack form
- Results visualization

### No Changes Required
Mode A is already implemented. This spec documents its design for clarity.

---

## Mode B: Auto Convert (Experimental)

### Purpose
Automatically analyze PDF to detect turned section views, extract dimensions, and infer TurnedPartStack/Profile2D with confidence scores.

### Design Philosophy
- **NOT full OCR→perfect CAD**: Focus on detecting step structures, not perfect dimension extraction
- **Incremental**: Start with simple heuristics, add ML/AI later if needed
- **Debuggable**: Store all intermediate detection results
- **Confidence-based**: Low confidence triggers fallback to Mode A

### Detection Pipeline

#### Stage 1: PDF Analysis
**Input**: PDF file(s)
**Output**: `outputs/pdf_analysis.json`

```json
{
  "pages_analyzed": [1, 2],
  "section_views_detected": [
    {
      "page": 1,
      "bbox": [x, y, width, height],
      "type": "section_view",
      "confidence": 0.85,
      "has_axis_marker": true,
      "axis_position": {"x": 0.0, "y": 0.0},
      "has_dimension_lines": true,
      "dimension_count": 8
    }
  ],
  "dimension_text_detected": [
    {
      "text": "1.63",
      "bbox": [x, y, width, height],
      "page": 1,
      "associated_line": "horizontal|vertical",
      "confidence": 0.75
    }
  ]
}
```

**Detection Methods** (incremental):
1. **Phase 1 (MVP)**: Simple heuristics
   - Look for section view indicators (section line, hatch patterns)
   - Detect axis lines (centerlines, symmetry indicators)
   - Find dimension text near lines
   - Extract numeric values from text

2. **Phase 2 (Future)**: Enhanced detection
   - ML-based section view detection
   - OCR with geometric context
   - Dimension line association
   - Multi-view correlation

#### Stage 2: Structure Inference
**Input**: `outputs/pdf_analysis.json`
**Output**: `outputs/inferred_structure.json`

```json
{
  "inference_method": "heuristic|ml",
  "confidence": 0.72,
  "detected_axis": {
    "position": {"x": 0.0, "y": 0.0},
    "orientation": "vertical|horizontal",
    "confidence": 0.90
  },
  "detected_segments": [
    {
      "index": 0,
      "z_start": 0.0,
      "z_end": 3.27,
      "od_diameter": 1.63,
      "id_diameter": 1.13,
      "confidence": 0.80,
      "source": "dimension_text|inferred",
      "validation_errors": []
    },
    {
      "index": 1,
      "z_start": 3.27,
      "z_end": 4.25,
      "od_diameter": 0.806,
      "id_diameter": 0.753,
      "confidence": 0.65,
      "source": "inferred",
      "validation_errors": ["missing_id_dimension"]
    }
  ],
  "validation_summary": {
    "total_segments": 2,
    "valid_segments": 1,
    "missing_dimensions": ["segment_1.id_diameter"],
    "inferred_values": ["segment_1.id_diameter"]
  }
}
```

**Inference Logic**:
1. Parse detected dimensions into numeric values
2. Group dimensions by position (Z-axis alignment)
3. Infer segment boundaries (step changes in OD/ID)
4. Match OD/ID pairs to form segments
5. Validate: all segments have OD, ID (if applicable), z_range
6. Compute confidence per segment and overall

#### Stage 3: Confidence Evaluation
**Input**: `outputs/inferred_structure.json`
**Output**: Decision: `auto_use` | `manual_review` | `fallback_to_mode_a`

**Confidence Thresholds**:
- `auto_use`: Overall confidence ≥ 0.85 AND all segments valid
- `manual_review`: Overall confidence ≥ 0.60 AND some segments valid
- `fallback_to_mode_a`: Overall confidence < 0.60 OR critical errors

**Confidence Factors**:
- Dimension text detection confidence
- Structure completeness (all required dimensions present)
- Geometric consistency (OD > ID, segments non-overlapping)
- Validation errors count

### API Design

#### Endpoint: `POST /jobs/{job_id}/auto-convert`
**Request**: 
```json
{
  "pdf_page": 1,  // Optional: specific page to analyze
  "detection_options": {
    "method": "heuristic",  // "heuristic" | "ml" (future)
    "min_confidence": 0.60
  }
}
```

**Response**:
```json
{
  "job_id": "...",
  "status": "completed|manual_review_required|fallback_to_manual",
  "confidence": 0.72,
  "inferred_structure": {
    // Same as outputs/inferred_structure.json
  },
  "outputs": [
    "pdf_analysis.json",
    "inferred_structure.json"
  ],
  "warnings": [
    "Low confidence on segment 1 ID diameter",
    "Missing dimension for segment boundary at z=3.27"
  ],
  "suggested_action": "manual_review"  // "auto_use" | "manual_review" | "fallback"
}
```

#### Endpoint: `POST /jobs/{job_id}/accept-inferred`
**Request**:
```json
{
  "accept_all": true,  // Or provide manual corrections
  "corrections": {
    "segment_1": {
      "id_diameter": 0.753  // Override inferred value
    }
  }
}
```

**Response**: Same as Profile2D/Stack input endpoints (feeds shared pipeline)

### Data Flow

```
PDF Upload
    ↓
[Mode Selection UI]
    ↓
┌─────────────────┬─────────────────┐
│   Mode A        │   Mode B        │
│ (Assisted)      │ (Auto Convert)  │
└─────────────────┴─────────────────┘
    ↓                    ↓
Manual Input      PDF Analysis
    ↓                    ↓
    │              Structure Inference
    │                    ↓
    │              Confidence Check
    │                    ↓
    │         ┌──────────┴──────────┐
    │         │                     │
    │    Auto Use          Fallback to Mode A
    │         │                     │
    └─────────┴─────────────────────┘
                ↓
        Profile2D / TurnedPartStack
                ↓
        [Shared Pipeline]
                ↓
        part_summary.json
```

### Shared Pipeline (No Changes)

Both modes converge here:
1. **Profile2D → RevolvedSolidBuilder** → `model.step`
2. **FeatureExtractor** → `TurnedPartStack`
3. **Metrics Computation** → `part_summary.json`

### Implementation Phases

#### Phase 1: Foundation (MVP)
- [ ] PDF text extraction (basic OCR)
- [ ] Simple dimension text detection (regex: numbers + units)
- [ ] Basic section view detection (heuristic: look for section lines)
- [ ] Axis detection (heuristic: centerlines, symmetry)
- [ ] Simple structure inference (group dimensions by position)
- [ ] Confidence scoring (basic: completeness + validation)
- [ ] Fallback UI (redirect to Mode A with detected values pre-filled)

#### Phase 2: Enhanced Detection
- [ ] Dimension line association (link text to geometry)
- [ ] Multi-segment detection (step changes)
- [ ] Improved confidence scoring (geometric consistency)
- [ ] Manual review UI (show inferred structure, allow corrections)

#### Phase 3: Advanced (Future)
- [ ] ML-based section view detection
- [ ] OCR with geometric context
- [ ] Multi-view correlation
- [ ] Learning from user corrections

### Debugging & Traceability

All intermediate results stored:
- `outputs/pdf_analysis.json` - Raw PDF analysis
- `outputs/inferred_structure.json` - Structure inference
- `outputs/confidence_report.json` - Confidence breakdown
- `outputs/debug_images/` - Visualization of detections (optional, for debugging)

### UI Design (Mode B)

#### Auto Convert Tab
1. **Detection Status**: Shows progress of PDF analysis
2. **Confidence Display**: Overall confidence score + per-segment breakdown
3. **Inferred Structure Preview**: Table showing detected segments
4. **Action Buttons**:
   - "Accept & Process" (if confidence ≥ threshold)
   - "Review & Edit" (if confidence in middle range)
   - "Switch to Manual Entry" (if confidence too low, pre-fills detected values)

#### Manual Review UI (if confidence medium)
- Show inferred structure in editable form
- Highlight low-confidence values
- Allow corrections
- "Accept & Process" button

### Error Handling

- **PDF parsing errors**: Fallback to Mode A immediately
- **No section view detected**: Fallback to Mode A
- **Low confidence**: Show manual review UI or fallback
- **Validation errors**: Show in UI, allow corrections

### Testing Strategy

1. **Unit Tests**: Each detection stage independently
2. **Integration Tests**: Full Mode B pipeline with known PDFs
3. **Regression Tests**: Ensure Mode A still works
4. **Confidence Calibration**: Test with various PDF quality levels

### Future Considerations

- **Learning System**: Store user corrections, improve inference
- **Multi-PDF**: Correlate dimensions across multiple drawings
- **3D View Integration**: Show inferred structure in 3D viewer
- **Export/Import**: Save/load inferred structures

---

## Summary

- **Mode A (Assisted Manual)**: Already implemented, no changes needed
- **Mode B (Auto Convert)**: New experimental mode, incremental development
- **Shared Pipeline**: Both modes converge at Profile2D/TurnedPartStack stage
- **Separation**: Clear boundaries, independent development possible
- **Debuggability**: All intermediate results stored
- **Graceful Degradation**: Low confidence → fallback to Mode A





