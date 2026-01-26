# Detailed Design Specification: Dual-Mode PDF→3D Conversion

## 1. API Endpoints

### 1.1 Mode Selection Endpoint

**Route**: `POST /api/v1/jobs/{job_id}/select-mode`

**Purpose**: Explicitly select processing mode after PDF upload.

**Request Schema**:
```json
{
  "mode": "assisted_manual" | "auto_convert",
  "options": {
    // Mode-specific options (optional)
    "pdf_page": 1,  // For auto_convert: specific page to analyze
    "detection_method": "heuristic"  // For auto_convert: "heuristic" | "ml"
  }
}
```

**Response Schema**:
```json
{
  "job_id": "uuid",
  "mode": "assisted_manual" | "auto_convert",
  "status": "ready",
  "message": "Mode selected. Proceed to input."
}
```

**Status Codes**:
- `200 OK`: Mode selected successfully
- `400 Bad Request`: Invalid mode or options
- `404 Not Found`: Job not found

---

### 1.2 Auto Convert Endpoint

**Route**: `POST /api/v1/jobs/{job_id}/auto-convert`

**Purpose**: Analyze PDF and infer TurnedPartStack/Profile2D structure.

**Request Schema**:
```json
{
  "pdf_page": 1,  // Optional: specific page (default: first page with section view)
  "detection_options": {
    "method": "heuristic",  // "heuristic" | "ml" (future)
    "min_confidence": 0.60,  // Minimum confidence to proceed
    "require_all_dimensions": false  // If true, fail if any dimension missing
  }
}
```

**Response Schema**:
```json
{
  "job_id": "uuid",
  "status": "completed" | "manual_review_required" | "fallback_to_manual",
  "confidence": 0.72,  // Overall confidence score [0.0, 1.0]
  "inferred_structure": {
    "method": "heuristic",
    "detected_axis": {
      "position": {"x": 0.0, "y": 0.0},
      "orientation": "vertical",
      "confidence": 0.90
    },
    "segments": [
      {
        "index": 0,
        "z_start": 0.0,
        "z_end": 3.27,
        "od_diameter": 1.63,
        "id_diameter": 1.13,
        "confidence": 0.80,
        "source": "dimension_text",
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
  },
  "outputs": [
    "pdf_analysis.json",
    "inferred_structure.json",
    "confidence_report.json"
  ],
  "warnings": [
    "Low confidence on segment 1 ID diameter (0.65)",
    "Missing dimension for segment boundary at z=3.27"
  ],
  "suggested_action": "manual_review",  // "auto_use" | "manual_review" | "fallback"
  "fallback_data": {
    // Pre-filled data for Mode A if fallback
    "stack_input": {
      "units": "in",
      "segments": [...]  // Same structure as inferred_structure.segments
    }
  }
}
```

**Status Codes**:
- `200 OK`: Analysis completed (status indicates next step)
- `400 Bad Request`: Invalid options or PDF not suitable
- `404 Not Found`: Job not found
- `500 Internal Server Error`: Analysis failed

---

### 1.3 Accept Inferred Structure Endpoint

**Route**: `POST /api/v1/jobs/{job_id}/accept-inferred`

**Purpose**: Accept inferred structure (with optional corrections) and proceed to pipeline.

**Request Schema**:
```json
{
  "accept_all": false,  // If true, accept all as-is
  "corrections": {
    "segment_0": {
      "z_start": 0.0,  // Optional: override inferred value
      "z_end": 3.27,
      "od_diameter": 1.63,
      "id_diameter": 1.13
    },
    "segment_1": {
      "id_diameter": 0.753  // Only override specific fields
    }
  },
  "output_format": "stack" | "profile2d"  // Which format to use for pipeline
}
```

**Response Schema**:
```json
{
  "job_id": "uuid",
  "status": "accepted",
  "format": "stack" | "profile2d",
  "next_step": "run_analysis" | "process_profile2d",
  "message": "Structure accepted. Processing..."
}
```

**Status Codes**:
- `200 OK`: Accepted, pipeline started
- `400 Bad Request`: Invalid corrections or structure
- `404 Not Found`: Job or inferred structure not found

**Note**: This endpoint internally calls either:
- `POST /api/v1/jobs/{job_id}/stack-input` (if format="stack")
- `POST /api/v1/jobs/{job_id}/profile2d` (if format="profile2d")

---

### 1.4 Get PDF Analysis Status Endpoint

**Route**: `GET /api/v1/jobs/{job_id}/auto-convert/status`

**Purpose**: Check status of ongoing PDF analysis (for long-running operations).

**Response Schema**:
```json
{
  "job_id": "uuid",
  "status": "running" | "completed" | "failed",
  "stage": "pdf_analysis" | "structure_inference" | "confidence_evaluation",
  "progress": 0.65,  // [0.0, 1.0]
  "estimated_time_remaining_seconds": 30
}
```

---

### 1.5 Get Inferred Structure Endpoint

**Route**: `GET /api/v1/jobs/{job_id}/inferred-structure`

**Purpose**: Retrieve previously inferred structure (for review/correction).

**Response Schema**: Same as `inferred_structure` in auto-convert response.

---

## 2. Backend Module Layout

### 2.1 Directory Structure

```
backend/app/services/pdf/
├── __init__.py
├── pdf_analyzer.py          # PDF text/image extraction
├── section_view_detector.py # Detect section views in PDF
├── dimension_extractor.py   # Extract dimension text and values
├── axis_detector.py         # Detect revolution axis
├── structure_inferencer.py  # Infer TurnedPartStack from dimensions
├── confidence_scorer.py     # Compute confidence scores
├── fallback_handler.py      # Handle fallback to Mode A
└── models.py                # Pydantic models for PDF analysis
```

### 2.2 Module Responsibilities

#### `pdf_analyzer.py`
- Extract text from PDF pages
- Extract images/vector graphics
- Page layout analysis
- Coordinate system mapping (PDF coords → normalized coords)

**Key Classes/Functions**:
```python
class PDFAnalyzer:
    def analyze_pdf(pdf_path: Path, page: int = None) -> PDFAnalysisResult
    def extract_text(page_num: int) -> List[TextElement]
    def extract_graphics(page_num: int) -> List[GraphicElement]
```

#### `section_view_detector.py`
- Detect section view indicators (section lines, hatch patterns)
- Identify likely turned part section views
- Bounding box extraction

**Key Classes/Functions**:
```python
class SectionViewDetector:
    def detect_section_views(analysis: PDFAnalysisResult) -> List[SectionView]
    def is_turned_part_view(section_view: SectionView) -> bool
```

#### `dimension_extractor.py`
- Extract dimension text (numbers + units)
- Associate text with geometry (dimension lines)
- Parse numeric values with units
- Group dimensions by position

**Key Classes/Functions**:
```python
class DimensionExtractor:
    def extract_dimensions(analysis: PDFAnalysisResult, section_view: SectionView) -> List[Dimension]
    def parse_dimension_text(text: str) -> Optional[DimensionValue]
    def associate_with_geometry(dimensions: List[Dimension]) -> List[AssociatedDimension]
```

#### `axis_detector.py`
- Detect revolution axis (centerlines, symmetry indicators)
- Determine axis position and orientation
- Validate axis consistency

**Key Classes/Functions**:
```python
class AxisDetector:
    def detect_axis(section_view: SectionView, dimensions: List[Dimension]) -> Optional[DetectedAxis]
    def validate_axis_position(axis: DetectedAxis, dimensions: List[Dimension]) -> bool
```

#### `structure_inferencer.py`
- Group dimensions into segments
- Infer segment boundaries (z_start, z_end)
- Match OD/ID pairs
- Build TurnedPartStack structure
- Validate geometric consistency

**Key Classes/Functions**:
```python
class StructureInferencer:
    def infer_structure(
        dimensions: List[Dimension],
        axis: DetectedAxis
    ) -> InferredStructure
    def group_dimensions_by_position(dimensions: List[Dimension]) -> List[DimensionGroup]
    def infer_segment_boundaries(groups: List[DimensionGroup]) -> List[SegmentBoundary]
    def match_od_id_pairs(dimensions: List[Dimension]) -> List[ODIDPair]
```

#### `confidence_scorer.py`
- Compute confidence per segment
- Compute overall confidence
- Evaluate confidence against thresholds
- Generate confidence report

**Key Classes/Functions**:
```python
class ConfidenceScorer:
    def compute_segment_confidence(segment: InferredSegment) -> float
    def compute_overall_confidence(structure: InferredStructure) -> float
    def evaluate_confidence(confidence: float) -> ConfidenceDecision
    def generate_report(structure: InferredStructure) -> ConfidenceReport
```

#### `fallback_handler.py`
- Convert inferred structure to Mode A format
- Pre-fill Mode A forms with detected values
- Handle fallback UI state

**Key Classes/Functions**:
```python
class FallbackHandler:
    def convert_to_stack_input(structure: InferredStructure) -> StackInputRequest
    def convert_to_profile2d(structure: InferredStructure) -> Profile2DRequest
    def prepare_fallback_data(structure: InferredStructure) -> FallbackData
```

#### `models.py`
- Pydantic models for all PDF analysis data structures
- Request/response models
- Validation models

**Key Models**:
```python
class PDFAnalysisResult(BaseModel)
class SectionView(BaseModel)
class Dimension(BaseModel)
class DetectedAxis(BaseModel)
class InferredSegment(BaseModel)
class InferredStructure(BaseModel)
class ConfidenceReport(BaseModel)
```

### 2.3 Service Integration

**Main Service**: `backend/app/services/pdf_service.py`

```python
class PDFService:
    def __init__(self):
        self.analyzer = PDFAnalyzer()
        self.section_detector = SectionViewDetector()
        self.dimension_extractor = DimensionExtractor()
        self.axis_detector = AxisDetector()
        self.structure_inferencer = StructureInferencer()
        self.confidence_scorer = ConfidenceScorer()
        self.fallback_handler = FallbackHandler()
    
    def auto_convert(self, job_id: str, options: AutoConvertOptions) -> AutoConvertResponse
    def accept_inferred(self, job_id: str, corrections: Dict) -> AcceptInferredResponse
```

---

## 3. Frontend UX Flow

### 3.1 Mode Selection Screen

**Route**: `/jobs/{job_id}/mode-selection`

**Components**:
- `ModeSelectionCard` (x2): One for each mode
- `ModeDescription`: Explains each mode
- `ModePreview`: Shows example output

**Flow**:
1. User uploads PDF → redirected to mode selection
2. Two cards displayed:
   - **Card A**: "Assisted Manual" (existing flow)
   - **Card B**: "Auto Convert" (new experimental)
3. User clicks card → mode selected → proceed to respective flow

---

### 3.2 Mode A: Assisted Manual Flow (Existing)

**Route**: `/jobs/{job_id}/input`

**Components** (existing):
- `PDFViewer`: View PDF reference
- `Profile2DSketchForm`: Manual Profile2D input
- `SegmentStackForm`: Manual segment stack input
- `ModeToggle`: Switch between Profile2D and Stack input

**Flow**: Already implemented, no changes.

---

### 3.3 Mode B: Auto Convert Flow

#### Screen 1: Analysis Progress

**Route**: `/jobs/{job_id}/auto-convert`

**Components**:
- `AnalysisProgressBar`: Shows analysis stage and progress
- `StatusMessage`: Current operation (e.g., "Detecting section views...")
- `CancelButton`: Cancel analysis (fallback to Mode A)

**Flow**:
1. User selects Mode B → analysis starts automatically
2. Progress bar shows stages:
   - PDF Analysis (0-30%)
   - Section View Detection (30-50%)
   - Dimension Extraction (50-70%)
   - Structure Inference (70-90%)
   - Confidence Evaluation (90-100%)
3. On completion → redirect to results screen

---

#### Screen 2: Results & Review

**Route**: `/jobs/{job_id}/auto-convert/results`

**Components**:
- `ConfidenceDisplay`: Overall confidence score + breakdown
- `InferredStructureTable`: Editable table of detected segments
- `ValidationErrorsList`: List of validation errors/warnings
- `ActionButtons`:
  - "Accept & Process" (if confidence ≥ 0.85)
  - "Review & Edit" (if 0.60 ≤ confidence < 0.85)
  - "Switch to Manual" (if confidence < 0.60 or user chooses)

**Flow**:
1. Display inferred structure with confidence scores
2. Highlight low-confidence values (red/yellow)
3. Show validation errors
4. User action:
   - **Accept**: Proceed to pipeline
   - **Review**: Edit values, then accept
   - **Switch**: Redirect to Mode A with pre-filled values

---

#### Screen 3: Manual Review (if confidence medium)

**Route**: `/jobs/{job_id}/auto-convert/review`

**Components**:
- `EditableStructureForm`: Form with inferred values (editable)
- `ConfidenceIndicators`: Visual indicators for each field
- `ValidationFeedback`: Real-time validation
- `SaveButton`: Save corrections and proceed

**Flow**:
1. Show inferred structure in editable form
2. Highlight fields with low confidence
3. User edits values as needed
4. Save → proceed to pipeline

---

### 3.4 Component Specifications

#### `ConfidenceDisplay.tsx`
```typescript
interface ConfidenceDisplayProps {
  overall: number;  // 0.0 - 1.0
  breakdown: {
    segment_0: number;
    segment_1: number;
    // ...
  };
  thresholds: {
    auto_use: number;
    manual_review: number;
  };
}
```

#### `InferredStructureTable.tsx`
```typescript
interface InferredStructureTableProps {
  segments: InferredSegment[];
  editable: boolean;
  onCorrection: (segmentIndex: number, field: string, value: number) => void;
  confidenceThreshold: number;
}
```

#### `AnalysisProgressBar.tsx`
```typescript
interface AnalysisProgressBarProps {
  stage: "pdf_analysis" | "section_detection" | "dimension_extraction" | 
        "structure_inference" | "confidence_evaluation";
  progress: number;  // 0.0 - 1.0
  message: string;
}
```

---

## 4. Job Folder Outputs Structure

### 4.1 Mode A (Assisted Manual)

```
backend/data/jobs/{job_id}/
├── inputs/
│   ├── drawing_001.pdf
│   └── drawing_002.pdf
└── outputs/
    ├── manual_input.json          # User-entered data (Profile2D or Stack)
    ├── stack_input.json           # If Stack mode used
    ├── model.step                 # Generated 3D model
    ├── model.glb                  # GLB conversion (if successful)
    ├── part_summary.json          # Final results
    └── run_report.json            # Pipeline execution report
```

**File: `outputs/manual_input.json`**
```json
{
  "mode": "assisted_manual",
  "input_type": "stack" | "profile2d",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    // Either stack_input or profile2d primitives
  }
}
```

---

### 4.2 Mode B (Auto Convert)

```
backend/data/jobs/{job_id}/
├── inputs/
│   └── drawing_001.pdf
└── outputs/
    ├── pdf_analysis.json          # Stage 1: PDF text/graphics extraction
    ├── section_views.json          # Detected section views
    ├── dimensions.json             # Extracted dimension text
    ├── axis_detection.json         # Detected revolution axis
    ├── inferred_structure.json     # Stage 2: Inferred TurnedPartStack
    ├── confidence_report.json      # Stage 3: Confidence scores
    ├── accepted_structure.json     # User-accepted structure (if reviewed)
    ├── model.step                  # Generated 3D model (after acceptance)
    ├── model.glb                   # GLB conversion
    ├── part_summary.json           # Final results
    └── run_report.json             # Pipeline execution report
```

**File: `outputs/pdf_analysis.json`**
```json
{
  "mode": "auto_convert",
  "timestamp": "2024-01-15T10:30:00Z",
  "pdf_file": "drawing_001.pdf",
  "pages_analyzed": [1],
  "text_elements": [
    {
      "text": "1.63",
      "bbox": [100, 200, 50, 20],
      "page": 1,
      "font_size": 12
    }
  ],
  "graphic_elements": [
    {
      "type": "line",
      "bbox": [50, 150, 200, 2],
      "page": 1
    }
  ]
}
```

**File: `outputs/inferred_structure.json`**
```json
{
  "mode": "auto_convert",
  "timestamp": "2024-01-15T10:30:15Z",
  "inference_method": "heuristic",
  "confidence": 0.72,
  "detected_axis": {
    "position": {"x": 0.0, "y": 0.0},
    "orientation": "vertical",
    "confidence": 0.90
  },
  "segments": [
    {
      "index": 0,
      "z_start": 0.0,
      "z_end": 3.27,
      "od_diameter": 1.63,
      "id_diameter": 1.13,
      "confidence": 0.80,
      "source": "dimension_text",
      "validation_errors": []
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

**File: `outputs/confidence_report.json`**
```json
{
  "overall_confidence": 0.72,
  "segment_confidences": {
    "segment_0": 0.80,
    "segment_1": 0.65
  },
  "confidence_factors": {
    "dimension_detection": 0.75,
    "structure_completeness": 0.70,
    "geometric_consistency": 0.80
  },
  "decision": "manual_review",
  "thresholds": {
    "auto_use": 0.85,
    "manual_review": 0.60
  }
}
```

---

## 5. Pipeline Integration

### 5.1 Mode A → Pipeline

**Current Flow** (no changes):
```
User Input (Profile2D or Stack)
    ↓
POST /jobs/{id}/profile2d OR POST /jobs/{id}/stack-input
    ↓
Profile2DService.process_profile2d() OR PipelineService.run_analysis()
    ↓
[Shared Pipeline]
    - RevolvedSolidBuilder → model.step
    - FeatureExtractor → TurnedPartStack
    - Metrics → part_summary.json
```

---

### 5.2 Mode B → Pipeline

**New Flow**:
```
PDF Analysis → Inferred Structure
    ↓
POST /jobs/{id}/accept-inferred
    ↓
FallbackHandler.convert_to_stack_input() OR convert_to_profile2d()
    ↓
POST /jobs/{id}/stack-input OR POST /jobs/{id}/profile2d
    ↓
[Same Shared Pipeline as Mode A]
    - RevolvedSolidBuilder → model.step
    - FeatureExtractor → TurnedPartStack
    - Metrics → part_summary.json
```

**Key Point**: Mode B converts inferred structure to same format as Mode A, then uses existing endpoints.

---

### 5.3 Shared Pipeline (No Changes)

Both modes converge at:
1. **Profile2D or TurnedPartStack** (input format)
2. **RevolvedSolidBuilder** → `model.step`
3. **FeatureExtractor** → `TurnedPartStack`
4. **Metrics Computation** → `part_summary.json`

**Services Used**:
- `Profile2DService` (for Profile2D input)
- `PipelineService` (for Stack input)
- `RevolvedSolidBuilder` (shared)
- `FeatureExtractor` (shared)
- `TurnedPartStack` (shared)

---

## 6. Fallback Logic and Confidence Thresholds

### 6.1 Confidence Thresholds

```python
CONFIDENCE_THRESHOLDS = {
    "auto_use": 0.85,           # High confidence: proceed automatically
    "manual_review": 0.60,      # Medium confidence: show review UI
    "fallback": 0.60            # Low confidence: fallback to Mode A
}
```

**Decision Logic**:
```python
if overall_confidence >= 0.85 and all_segments_valid:
    action = "auto_use"
elif overall_confidence >= 0.60 and some_segments_valid:
    action = "manual_review"
else:
    action = "fallback_to_manual"
```

---

### 6.2 Confidence Computation

**Per-Segment Confidence**:
```python
segment_confidence = (
    dimension_detection_confidence * 0.4 +
    structure_completeness * 0.3 +
    geometric_consistency * 0.3
)
```

**Overall Confidence**:
```python
overall_confidence = (
    mean(segment_confidences) * 0.6 +
    axis_detection_confidence * 0.2 +
    structure_validity * 0.2
)
```

**Confidence Factors**:
- **Dimension Detection** (0.0-1.0): How confident we are in extracted dimension values
- **Structure Completeness** (0.0-1.0): Percentage of required dimensions present
- **Geometric Consistency** (0.0-1.0): OD > ID, segments non-overlapping, etc.
- **Axis Detection** (0.0-1.0): Confidence in detected revolution axis

---

### 6.3 Fallback Scenarios

#### Scenario 1: Low Overall Confidence
- **Condition**: `overall_confidence < 0.60`
- **Action**: Redirect to Mode A with pre-filled values
- **Data**: `fallback_data.stack_input` sent to frontend

#### Scenario 2: Critical Validation Errors
- **Condition**: Structure has critical errors (e.g., OD < ID, overlapping segments)
- **Action**: Fallback to Mode A (even if confidence is high)
- **Data**: Show errors in Mode A form

#### Scenario 3: No Section View Detected
- **Condition**: `section_views_detected == 0`
- **Action**: Immediate fallback to Mode A
- **Data**: No pre-filled data

#### Scenario 4: User Chooses Fallback
- **Condition**: User clicks "Switch to Manual" button
- **Action**: Redirect to Mode A with pre-filled values
- **Data**: Current inferred structure converted to Mode A format

---

### 6.4 Fallback Data Format

**Stack Input Format** (for Mode A):
```json
{
  "units": "in",
  "segments": [
    {
      "z_start": 0.0,
      "z_end": 3.27,
      "od_diameter": 1.63,
      "id_diameter": 1.13
    }
  ]
}
```

**Profile2D Format** (for Mode A):
```json
{
  "primitives": [
    {
      "type": "line",
      "start": {"x": 0.565, "y": 0.0},
      "end": {"x": 0.565, "y": 3.27}
    }
    // ... more primitives
  ],
  "axis_point": {"x": 0.0, "y": 0.0}
}
```

---

## 7. Definition of Done (Phases)

### Phase 1: Foundation (MVP)

**Goal**: Basic PDF analysis with simple heuristics.

**Backend Tasks**:
- [ ] `PDFAnalyzer`: Extract text from PDF (using PyPDF2 or pdfplumber)
- [ ] `SectionViewDetector`: Simple heuristic (look for section line indicators)
- [ ] `DimensionExtractor`: Regex-based dimension text extraction (numbers + units)
- [ ] `AxisDetector`: Simple heuristic (detect centerlines, symmetry)
- [ ] `StructureInferencer`: Basic grouping and inference
- [ ] `ConfidenceScorer`: Simple confidence computation
- [ ] `PDFService.auto_convert()`: End-to-end integration
- [ ] API endpoint: `POST /jobs/{id}/auto-convert`

**Frontend Tasks**:
- [ ] Mode selection screen
- [ ] Analysis progress screen
- [ ] Results display screen (read-only)
- [ ] Fallback redirect to Mode A

**Outputs**:
- [ ] `pdf_analysis.json` generated
- [ ] `inferred_structure.json` generated
- [ ] `confidence_report.json` generated

**Acceptance Criteria**:
- Can detect at least one section view in test PDF
- Can extract at least 50% of dimension text
- Can infer structure with confidence > 0.40
- Fallback works when confidence < 0.60

---

### Phase 2: Enhanced Detection

**Goal**: Improved detection accuracy and manual review UI.

**Backend Tasks**:
- [ ] `DimensionExtractor`: Associate text with geometry (dimension lines)
- [ ] `StructureInferencer`: Multi-segment detection (step changes)
- [ ] `ConfidenceScorer`: Enhanced confidence (geometric consistency)
- [ ] `FallbackHandler`: Convert to Profile2D format
- [ ] API endpoint: `POST /jobs/{id}/accept-inferred`

**Frontend Tasks**:
- [ ] Editable structure table
- [ ] Confidence indicators per field
- [ ] Manual review screen
- [ ] Correction validation

**Outputs**:
- [ ] `accepted_structure.json` generated
- [ ] Pipeline integration working

**Acceptance Criteria**:
- Can detect 70%+ of dimensions
- Can infer structure with confidence > 0.60
- Manual review UI allows corrections
- Accepted structure feeds pipeline successfully

---

### Phase 3: Advanced (Future)

**Goal**: ML-based detection and learning system.

**Backend Tasks**:
- [ ] ML model for section view detection
- [ ] OCR with geometric context
- [ ] Multi-view correlation
- [ ] Learning from user corrections

**Frontend Tasks**:
- [ ] 3D preview of inferred structure
- [ ] Export/import inferred structures

**Acceptance Criteria**:
- ML model achieves >85% accuracy on test set
- Can handle multi-page drawings
- Learning system improves over time

---

## 8. Error Handling

### 8.1 PDF Parsing Errors
- **Error**: PDF cannot be parsed
- **Action**: Immediate fallback to Mode A
- **Message**: "PDF parsing failed. Please use manual entry."

### 8.2 No Section View Detected
- **Error**: `section_views_detected == 0`
- **Action**: Fallback to Mode A
- **Message**: "No section view detected. Please use manual entry."

### 8.3 Low Confidence
- **Error**: `overall_confidence < 0.60`
- **Action**: Fallback to Mode A with pre-filled values
- **Message**: "Low confidence in detected structure. Please review and correct."

### 8.4 Validation Errors
- **Error**: Structure has validation errors (OD < ID, etc.)
- **Action**: Show errors in review UI, allow corrections
- **Message**: "Structure has validation errors. Please correct."

### 8.5 Pipeline Errors
- **Error**: Pipeline fails after accepting inferred structure
- **Action**: Show error, allow re-correction
- **Message**: "Processing failed. Please review structure and try again."

---

## 9. Testing Strategy

### 9.1 Unit Tests
- Each module tested independently
- Mock PDF analysis results
- Test confidence computation
- Test fallback conversion

### 9.2 Integration Tests
- End-to-end Mode B flow
- Test with known PDFs
- Verify pipeline integration
- Test fallback scenarios

### 9.3 Regression Tests
- Ensure Mode A still works
- Ensure shared pipeline unchanged
- Test baseline part with both modes

---

## 10. Summary

**Key Points**:
1. **Separation**: Mode A and Mode B are independent input methods
2. **Convergence**: Both feed same shared pipeline
3. **Incremental**: Phase 1 MVP, then enhancements
4. **Debuggable**: All intermediate results stored
5. **Graceful**: Fallback to Mode A when confidence low
6. **No Breaking Changes**: Mode A remains unchanged

**Implementation Order**:
1. Phase 1: Basic PDF analysis + fallback
2. Phase 2: Enhanced detection + review UI
3. Phase 3: ML-based detection (future)








