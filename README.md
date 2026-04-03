# RFQ Autofill — AI-Powered Manufacturing Cost Estimation

> **From engineering drawing to fully-priced RFQ in under 60 seconds.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://typescriptlang.org)

---

## The Problem We Solve

Manufacturing procurement teams spend **4–8 hours per RFQ** manually reading engineering drawings, transcribing dimensions into spreadsheets, computing raw-material weights, and recalculating cost breakdowns — a process that is:

- **Slow** — engineers context-switch between CAD viewers, PDFs, and Excel for every part
- **Error-prone** — manual transcription creates costly quoting mistakes
- **Unscalable** — a single RFQ batch of 50 parts can consume a full work-week
- **Currency-blind** — exchange rates are looked up ad-hoc and go stale

## Our Solution

**RFQ Autofill** is an AI-native web platform that eliminates that bottleneck. Upload a STEP file or a PDF drawing — our system does the rest:

```
Upload (STEP or PDF)
    ↓
AI Dimension Extraction  ←  Intelligent document understanding + OCR
    ↓
Geometry Analysis        ←  3D feature detection (holes, slots, turned profiles)
    ↓
Cost Computation         ←  RM weight, material cost, machining, markups
    ↓
Live Currency Conversion ←  Real-time FX rate (USD ↔ INR, cached 1h)
    ↓
Excel RFQ Export         ←  Vendor-ready spreadsheet, all formulas live
```

What used to take hours now takes **under 60 seconds**.

---

## Capabilities

| Capability | Detail |
|---|---|
| **STEP → 3D Geometry** | Parses solid models, extracts OD/ID/Length envelope, detects turned profile, holes, and slots |
| **PDF → AI Extraction** | Reads any engineering drawing — text-based or fully scanned — and extracts structured dimensions |
| **Intelligent Dimension Override** | AI-extracted values take precedence over geometry estimates, ensuring drawing intent is preserved |
| **Live Excel Export** | Exports vendor-ready `.xlsx` with full formula chain — change any cell and everything cascades |
| **Real-time FX Rate** | Live USD → INR conversion via external API, with 1-hour caching and graceful fallback |
| **Vendor Quote Mode** | Per-part cost breakdown: material, roughing, turning, VMC, special process, OH&P, rejection |
| **In-browser 3D Viewer** | Interactive GLB model rendered directly in the browser — no plugin required |
| **REST API** | Every feature is API-first — integrate with ERP, PLM, or procurement portals |

---

## How It Works

### Mode 1 — STEP File Upload

Upload any `.step` or `.stp` solid model. The system:

1. Parses the 3D geometry with a custom feature extraction engine
2. Detects the maximum OD, bore ID, overall length, holes, slots, and machined features
3. Renders an interactive 3D GLB model in the browser (no CAD software required)
4. Computes finish dimensions → raw material blank dimensions (OD + 0.1", length + 0.35")
5. Calculates RM weight using the exact steel density formula `((OD² − ID²) × L × 0.785 × 7.86) / 1,000,000`
6. Exports a fully-priced vendor-ready Excel RFQ

### Mode 2 — PDF Drawing Upload

Upload any engineering drawing — searchable or fully scanned. The system:

1. Extracts text from the document; falls back to OCR for image-based PDFs
2. Uses AI to understand and extract structured dimensions from the drawing
3. Returns `OD`, `ID`, `Length`, `Material`, `Quantity` with **confidence scores** per field
4. User reviews extracted values in the AI Analysis panel before committing
5. AI-extracted dimensions are passed as authoritative overrides — geometry estimates never shadow drawing intent
6. Same Excel export pipeline as Mode 1

---

## Technology Stack

| Layer | Technology | Why |
|---|---|---|
| **Backend API** | FastAPI + uvicorn | Async, production-grade Python web framework |
| **Geometry engine** | Custom 3D feature extractor | Handles real-world STEP files; extracts turned-part profiles |
| **OCR** | Dual-engine OCR pipeline | Redundancy for degraded scans |
| **AI Engine** | Proprietary AI pipeline | Provider-agnostic intelligent document understanding |
| **Excel export** | openpyxl (formula-injected) | Live formula chain; `fullCalcOnLoad` — any cell edit cascades |
| **Currency** | Live FX API + 1h cache | Fresh rates; graceful fallback prevents export failures |
| **Frontend** | React 18 + TypeScript + Vite | Type-safe, HMR dev experience, production build optimised |
| **3D viewer** | Three.js GLB renderer | In-browser; no plugin; works on every OS |

---

## The Excel Output — A Precision Instrument

The exported `.xlsx` is not a static report. Every calculation is encoded as a **live Excel formula** — vendors and engineers can adjust any input and the entire sheet recalculates instantly:

| Column | Formula |
|---|---|
| Finish OD / ID / Length (MM) | `= Inch × 25.4` |
| RM OD | `= ROUND(Finish_OD + 0.1, 3)` — standard stock allowance |
| RM ID | `= IF(ID > 0, ROUND(MAX(0, ID − 0.05), 3), 0)` |
| RM Stock Length | `= Finish_Length + 0.35` — facing + parting allowance |
| **RM Weight Kg** | `= ((OD×25.4)²×(L×25.4)×0.785×7.86)/1,000,000 − bore` |
| Material Cost | `= RM Rate × RM Weight` |
| Sub Total | `= Σ(Material + Roughing + Turning + VMC + Special + Others + Inspection)` |
| P&F | `= Sub Total × 3%` |
| OH & Profit | `= Sub Total × 15%` |
| Rejection Provision | `= Sub Total × 2%` |
| **Price / Each (INR)** | `= Sub Total + all markups` |
| **Price / Each (Currency)** | `= INR Price ÷ Live Exchange Rate` |
| MOQ Cost | `= Price/Each × Qty/MOQ` |
| Annual Potential | `= Price/Each × Annual Qty` |

Change the RM Rate, tweak the Qty, update the exchange rate — every downstream cell updates automatically.

---

## Quick Start

Open the live app at **https://quotemycad.com/**

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health check |
| `POST` | `/api/v1/jobs` | Create job, upload STEP or PDF |
| `GET` | `/api/v1/jobs/{id}` | Job status + metadata |
| `GET` | `/api/v1/jobs/{id}/3d-preview` | Stream GLB model |
| `POST` | `/api/v1/rfq/autofill` | Autofill from geometry |
| `POST` | `/api/v1/rfq/export_xlsx` | Export filled RFQ Excel |
| `GET` | `/api/v1/rfq/exchange_rate` | Live USD → INR rate |

---

## Differentiation — Why This Is Hard to Replicate

1. **Dual-input AI pipeline** — same output regardless of whether the source is a 3D model or a scanned PDF, with intelligent override priority
2. **Formula-native export** — the Excel file is not a snapshot; it is a live calculation engine that vendors can work with directly
3. **Geometry-aware RM sizing** — RM OD, ID, and stock length are computed from the actual turned-profile envelope, not generic rules
4. **Human-in-the-loop confidence gating** — every AI inference includes per-field confidence scores; low-confidence outputs are flagged before any export is committed

---

## Roadmap

- [ ] Multi-part batch processing (RFQ with 50+ line items in one upload)
- [ ] ERP / PLM webhook integration (SAP, Oracle, Odoo)
- [ ] Supplier comparison mode (parallel quotes from multiple vendors)
- [ ] Historical RFQ learning (improve cost accuracy over time)
- [ ] Cloud deployment

---

## License

Proprietary — All rights reserved.
