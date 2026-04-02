"""Single-call LLM pipeline for PDF engineering drawing analysis.

Pipeline steps
--------------
1. Extract raw text from the uploaded PDF (pdfplumber → PyMuPDF fallback).
2. Merged Agent – one LLM call that both extracts structured specs (OD, ID,
   length, material, part number, quantity, tolerances) AND self-validates
   them with per-field confidence scores and a ACCEPT/REVIEW/REJECT verdict.
3. Code-level validation: sanity checks (OD > ID, positives, plausibility)
   applied after the agent call.  Final ``valid`` flag is the AND of the
   agent's ACCEPT recommendation + zero code issues.

Using a single merged call (instead of two sequential agents) means the
pipeline consumes exactly ONE quota slot per run, which matters on free-tier
Gemini where the limit is 1 RPM.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
import os
from typing import Any

from app.services import llm_service

logger = logging.getLogger(__name__)

# Configurable thresholds (override via environment variables or backend/.env)
GEOM_CONF_THRESHOLD: float = float(os.getenv("PDF_GEOM_CONF_THRESHOLD", "0.8"))
GEOM_SEG_SCORE_MIN: float = float(os.getenv("PDF_GEOM_SEG_SCORE_MIN", "0.01"))
ID_OD_RATIO_MAX: float = float(os.getenv("PDF_ID_OD_RATIO_MAX", "0.98"))
# Server-side segment cleaning controls
PDF_GEOM_MERGE_SHORT: bool = os.getenv("PDF_GEOM_MERGE_SHORT", "1") in ("1", "true", "True")
PDF_GEOM_MIN_SEG_SPAN: float = float(os.getenv("PDF_GEOM_MIN_SEG_SPAN", "0.02"))

# Finish extraction regex patterns (broadened)
_FINISH_RE_1 = re.compile(r"(?:surface\s*finish|finish|surf(?:ace)?\s*fin(?:ish)?)\s*[:\-]?\s*([^\n,;]{1,60})", re.IGNORECASE)
_FINISH_RE_RA = re.compile(r"\b(Ra\s*\d+(?:\.\d+)?)\b", re.IGNORECASE)
_FINISH_RE_KEYWORD = re.compile(r"((?:polish|grit|machined|ground|electropolish|mirror finish|bright)[^\n,;]{0,40})", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACTOR_SYSTEM = """\
You are an expert CNC machining engineer reading OCR-extracted text from an engineering drawing.
The drawing may come from ANY company, use ANY labeling convention, and may have NO explicit
"FINISH OD" label anywhere. You must reason geometrically and by manufacturing domain knowledge.

Your THREE priority outputs:
  od_in      -> Finish OD     : the MAXIMUM outer diameter of the finished machined part
  id_in      -> Finish ID     : the PRIMARY bore / inner diameter of the finished part
  length_in  -> Finish Length : the OVERALL end-to-end length of the finished part

Return ONLY a valid JSON object with these exact keys (null if genuinely not found):
{
  "part_number":      "<string or null>",
  "part_name":        "<string or null>",
  "material":         "<string or null>",
  "quantity":         <integer or null>,
  "od_in":            <float -- MAX finished OD of the part, inches, PRIORITY>,
  "max_od_in":        <float or null -- Raw bar stock / RM OD, inches>,
  "id_in":            <float or null -- primary bore diameter, inches, PRIORITY>,
  "max_id_in":        <float or null -- largest bore step / counter-bore, inches>,
  "length_in":        <float -- overall finished length end-to-end, inches, PRIORITY>,
  "max_length_in":    <float or null -- raw cutoff / RM length, inches>,
  "tolerance_od":     "<string or null>",
  "tolerance_id":     "<string or null>",
  "tolerance_length": "<string or null>",
  "finish":           "<surface finish spec or null>",
  "revision":         "<string or null>",
  "features": [
    {
      "type":        "<chamfer|groove|fillet|thread|counterbore|hole>",
      "confidence":  "<0.0-1.0>",
      "z_pos":       "<float or null -- axial centre of this feature in inches from near end>",
      "z_start":     "<float or null>",
      "z_end":       "<float or null>",
      "od_diameter": "<float or null>",
      "id_diameter": "<float or null>",
      "angle_deg":   "<float or null>",
      "size_in":     "<float or null -- chamfer leg, fillet radius, or hole diameter>",
      "count":       "<integer or null -- number of identical instances (e.g. 2X holes = 2)>",
      "face":        "<od|id|both or null>",
      "spec_text":   "<string or null>"
    }
  ]
}


=== PART SHAPE CLASSIFICATION (do this FIRST before assigning any dimension) ===

Before reading any number off the drawing, identify what kind of part you are looking at:

  DISC / RING / BUSHING / WASHER / SHORT SLEEVE:
    Visual: the part looks wider than it is tall/long in the main profile view.
    Characteristic: od_in is LARGER than length_in. This is normal and correct.
    Examples: spacer (OD=1.38", L=0.50"), bushing (OD=1.25", L=0.75"), ring.
    TRAP: do NOT assume "largest number = length_in". For a disc/ring part, the
    largest Phi-prefixed number is od_in -- it exceeds the length.

  SHAFT / BARREL / LONG PART:
    Visual: the part is noticeably longer than it is wide.
    Characteristic: length_in is LARGER than od_in. The typical case.

  PHI SYMBOL RULE (universal -- applies to ALL part shapes):
    ANY callout preceded by Ø / Phi / circle-O / "/O" / "Dia" / "D=" = a DIAMETER.
    DIAMETER callouts are NEVER length_in, regardless of their magnitude.
    OCR renders Phi as: "Ø", "/O", "O", "Dia", "D" -- check carefully for this prefix.
    If the LARGEST number on the drawing has a Phi prefix → it is od_in, not length_in.

  MATERIAL CONTEXT HINT:
    If the material field contains "FLAT", "FLAT BAR", "CD FLAT", "PLATE", "SHEET",
    the part may be a disc, ring, bracket, or spacer cut from flat stock.
    od_in still represents the largest outer cross-sectional dimension of the finished part.
    length_in is the overall axial thickness/length.
    If the material/title block contains a stock size like "4.00 DIA 1018 CRS",
    "3.5 OD TUBE", or "2.00 BAR", that stock-size diameter belongs to max_od_in
    (raw material), NOT od_in, unless the drawing explicitly shows the finished profile
    also remains at that exact diameter. Do NOT copy the stock/material diameter into
    od_in just because it is the largest diameter-like number in the title block.

  GENERIC PROFILE-vs-STOCK RULE:
    Finished dimensions come from the FINISHED PART PROFILE / SECTION VIEW.
    Stock dimensions come from material notes, title blocks, RM tables, or purchase notes.
    If a title/material note gives a diameter and the drawing profile shows a smaller finished
    diameter with its own tolerance/callout, use the smaller profile value as od_in and the
    title/material note value as max_od_in.
    Rule of precedence: finished profile callout > section/profile geometry > title/material note.


=== FUNDAMENTAL CONCEPT: WHAT EACH FIELD MEANS ===

od_in  (Finish OD) -- THE MAXIMUM OUTER DIAMETER OF THE FINISHED PART:
  A turned part has a profile with potentially many steps, shoulders, and flanges.
  The Finish OD is the LARGEST diameter anywhere on that finished profile.
  It is the outer bounding dimension of the part -- the widest point.
  On engineering drawings this is often shown as the OVERALL HEIGHT of the part silhouette,
  indicated by two horizontal parallel witness/extension lines that span the full width.
  Among ALL diameter (Phi / O / Dia) callouts visible on the part views and profile,
  the LARGEST value is the Finish OD (od_in).

  Example: if you see Phi1.240, Phi1.006-1.008, Phi0.443, Phi0.628, Phi0.94 on the part --
  od_in = 1.240  (it is the largest, defining the outer envelope of the part)

max_od_in  (Raw Material OD / RM OD):
  This is the bar stock or tube stock you ORDER from the supplier BEFORE machining.
  It is ALWAYS larger than od_in because you machine the OD down to finish size.
  It is typically found in a SEPARATE table, title block note, or RM/STOCK section --
  NOT as a dimension on the main part profile views.
  Labels: RM OD, Raw OD, Stock OD, Bar Dia, Material OD, Blank OD, Purchase OD.
  Very common pattern: material note like "4.00 DIA 1018 CRS" or "2.50 DIA ALUM".
  In that case 4.00 / 2.50 is the STOCK diameter → max_od_in, not the finished od_in.
  If not explicitly stated, it can be inferred: od_in + machining allowance (usually +0.06" to +0.25").

id_in  (Finish ID / Primary Bore):
  The primary bore DIAMETER drilled/bored into the finished part.
  CRITICAL DISTINCTION — bore DIAMETER vs bore DEPTH:
    Bore DIAMETER = the width of the hole (perpendicular to the axis). THIS is id_in.
    Bore DEPTH    = how far the hole goes along the axis. This is NOT id_in.
    Example: a Phi0.430 bore that goes 0.500" deep → id_in = 0.430 (NOT 0.500).
  ORIFICE / PORT HOLE TRAP (most common error on pistons and hydraulic parts):
    Parts may have BOTH a large MAIN BORE (the primary hollow cavity) AND small orifice/passage
    holes (tiny ports, cross-drilled passages, vent holes). These are COMPLETELY DIFFERENT:
      Main bore: large hollow cavity defining the interior of the part. THIS is id_in.
      Orifice/port: tiny holes (e.g., Phi0.125, Phi0.171), often labeled "2X" or "4X",
        used for fluid flow or assembly clearance. These are NOT id_in.
    id_in = the MAIN BORE diameter. If id_in / od_in < 0.15 for a hollow part, re-examine:
      you likely returned an orifice hole, not the main bore.
  For a part with a single through-bore: that bore diameter is id_in.
  For a part with true COUNTER-BORE STEPS (concentric, coaxial bore reductions):
    id_in = SMALLEST (innermost / deepest / narrowest) bore diameter.
    max_id_in = LARGEST bore diameter (counter-bore entrance).
  For a part with main bore + orifice holes: id_in = main bore (the large one).
  It must always be LESS THAN od_in.
  A solid shaft with no bore: id_in = null.
  ENUMERATION PROTOCOL:
    1. List EVERY bore/ID callout (all Phi or ID values inside the part).
    2. Classify each: main bore, counter-bore step, orifice/port, pilot hole.
    3. Select: single bore → that diameter; true counter-bore steps → smallest;
       main bore + orifices → main bore diameter (NOT the tiny orifice).
    4. Verify: id_in must be a DIAMETER (Phi/circle symbol or 'ID' label),
       NOT a depth, NOT a shoulder distance, NOT a tolerance band value.

max_id_in  (Largest Bore / Max ID):
  The LARGEST bore diameter on the part (e.g., a counter-bore or stepped bore entrance).
  >= id_in always. Found on the part profile, NOT in an RM table.

length_in  (Finish Length / OAL):
  The OVERALL end-to-end finished length of the part along its rotational axis.
  This is the total axial span from one end-face to the other end-face after machining.

  MANDATORY EXTRACTION STRATEGY (try in order until you find a value):
  1. Look for a single dimension line that spans the FULL axial extent of the part profile
     (from leftmost witness line to rightmost witness line). That value = length_in.
     Labels: OAL, OVERALL, TOTAL LENGTH, or no label at all.
     STOP HERE: if you find this full-span dimension, do NOT add any other partial dims to it.
     A partial step dimension that starts at the same end-face is INSIDE this OAL, not beyond it.
  2. No full-span line? SUM all sequential axial partial dimensions that together cover the
     full axial extent from one end-face to the other. Their sum = length_in.
     (Confidence 0.80 when using chain sum.)
     SEGMENT OVERLAP TRAP (most common chain-sum error):
       Drawings often show BOTH a full-span OAL dimension AND shorter step dimensions
       that start from the same end-face and go part-way. These shorter dims are WITHIN
       the OAL, not additive beyond it. Example: OAL=4.13 with a step of 2.63 measured
       from the same face → length_in = 4.13, NOT 4.13 + 2.63 = 6.76.
       Rule: only sum dimensions whose SEGMENTS are non-overlapping and together cover
       the full axis end-to-end without duplication. If any two dimensions share the same
       start or end face, the LARGER one already contains the smaller one — do not add them.
  3. No partial dims either? Estimate axially from the part silhouette using od_in as scale.
      (Confidence 0.60 when estimating.)
    IMPORTANT: If you find a length value in a raw-material (RM) table, title block, or
      stock/cutoff notes that conflicts with the chain-sum or silhouette estimate, DO NOT
      return the RM/table value as `length_in`. Prefer the chain-sum (0.80 confidence)
      or silhouette estimate (0.60 confidence) for `length_in` and annotate the source.
    SYMMETRY / HALF-VIEW TRAP:
      Some drawings dimension only from the CENTERLINE or mid-plane to one face on a
      symmetric part (cap, flange, hub, spacer, plug, ring, collar, spool, etc.). That centerline-to-face distance
      is HALF the overall length. If the profile is mirrored about a vertical center plane
      and you only see 1.44 or 1.45 from centerline to one end, OAL is about 2.88 or 2.90,
      not 1.45. Always determine whether an axial dimension is full-face-to-face or only
      centerline-to-face before assigning length_in.
  4. NEVER return null for length_in on a turned part. Null prevents quoting.

  NOT a partial feature (not a step height, groove depth, bore depth, or shoulder distance).

  BLIND-BORE TRAP (very common error for pistons and cups):
  For parts with a BLIND bore (bore that does not go all the way through),
  the bore DEPTH is NOT the same as the part OAL:
    OAL = bore depth + closed bottom wall thickness
  Example: bore depth = 2.72", closed-end wall = 0.28" → OAL (length_in) = 3.00"
  ALWAYS check if the bore depth (a note like "2.720 DEEP" or the z_end of bore features)
  equals all your partial axial dimensions. If the sum of all face-to-bore-bottom dims < OAL,
  there is a wall section at one end — account for it to get the true OAL.

max_length_in  (Raw Cutoff Length / RM Length):
  The length of bar stock cut before machining. Always >= length_in.
  Found in RM table, notes, or labeled as cutoff/blank/stock length.


=== HOW TO FIND FINISH OD (od_in) ON ANY DRAWING ===

Step 1: Collect ALL diameter callouts from the entire drawing text.
  Diameter callouts are preceded by: Phi, O (circle symbol), Dia, D=, R (if given as radius --
  multiply by 2), or appear in tables under columns labeled OD / Diameter / D.
  OCR often renders the Phi symbol as "O", "0", "/O", "(O", or omits it entirely.

Step 2: Separate part-profile diameters from raw-material diameters.
  Part-profile diameters appear on: front view, section view, detail view, call-out balloons.
  Raw-material diameters appear in: RM table, STOCK block, MATERIAL REQUIRED section,
  title block notes, or labeled explicitly as RM/Raw/Stock/Bar.

Step 3: Among the part-profile diameters, the LARGEST value = od_in.
  This represents the maximum outer envelope of the finished part.

Step 4: The largest of the part diameters might be a step, flange, or collar -- that is fine.
  It is still the Finish OD (the outer machining boundary the shop must not exceed).


=== HOW TO FIND FINISH ID (id_in) ON ANY DRAWING ===

Step 1 — ENUMERATE ALL BORE CALLOUTS:
  Scan the entire drawing for every diameter callout that refers to an interior feature.
  These appear:
    • In a cross-section/section view with hatching (cross-hatched material shows bore cut)
    • On leader lines pointing INWARD toward the axis / center-line
    • Labeled as ID, BORE, Phi followed by a number that is clearly smaller than the OD
    • In detail views or enlarged callout circles zooming into the bore region
  Write down EVERY such diameter value you find before choosing one.

Step 2 — CLASSIFY EACH BORE CALLOUT:
  For each bore diameter found, determine:
    TYPE A — Single through-bore:  one Phi callout, runs the full axial length.
    TYPE B — Stepped bore (counter-bore): two or more Phi callouts at different depths.
      The LARGER entrance step opening = counter-bore = max_id_in.
      The SMALLER deeper bore = primary bore = id_in.
    TYPE C — Pilot bore or partial depth bore: a shallow bore, usually with a depth callout.
      May still be id_in if it is the only bore, or the primary functional bore.

Step 3 — DISTINGUISH PRIMARY BORE FROM ORIFICE/PASSAGE HOLES (CRITICAL):
  Not all small-diameter holes on a part are counter-bore steps of the main bore.
  COUNTER-BORE STEPS: concentric, coaxial, progressively smaller bores that share the SAME
    centerline. Each step is a machined diameter reduction inward along the part axis.
    In this case: id_in = smallest (deepest) bore. max_id_in = largest (entrance) bore.
  ORIFICE / PASSAGE / PORT HOLES: small cross-holes, through-holes, or fluid passage holes
    that are separate from the main bore. They may appear as:
      - Small Phi callouts in a DIFFERENT location (off-axis, cross-drilled, at the side wall)
      - Labeled as PORT, ORIFICE, PASSAGE, VENT, or with a note like "2X" or "4X" indicating
        multiple identical small holes
      - Much smaller than the obvious main bore (e.g., Phi0.88 main bore + Phi0.125 ports)
    In this case: orifice holes are NOT counter-bores. id_in = the main bore. max_id_in = null
    (unless there is a true counter-bore). Orifice holes go in the features[] array as
    separate entries, NOT as the id_in value.
  KEY SIGNAL: if the drawing shows a LARGE bore (e.g., Phi0.88) and separately shows several
    TINY bores (e.g., Phi0.125 ports), the large bore is id_in and the tiny bores are orifices.
    Do NOT set id_in = 0.125 (smallest tiny hole). Set id_in = 0.88 (main bore).

Step 4 — SELECT id_in:
  Single bore (TYPE A): id_in = that diameter.
  Stepped bores (TYPE B, true counter-bore): id_in = the SMALLEST (deepest, tightest) bore.
  Main bore + separate orifice/passage holes: id_in = the MAIN bore (largest functional bore).
  Multiple bores of same type: id_in = the bore at the primary functional location
    (usually center of part, longest bore, or the one with the tightest tolerance).

Step 5 — DISTINGUISH DIAMETER FROM DEPTH (CRITICAL):
  A bore callout has TWO numbers: the Phi (diameter) and sometimes a depth.
  Example: Phi0.430 THRU or Phi0.430 x 0.500 DEEP
    → id_in = 0.430  (the DIAMETER after Phi)
    → 0.500 is the bore DEPTH along the axis — it is NOT id_in.
  If you see a dimension WITHOUT a Phi/Dia/ID prefix that is smaller than od_in,
    check whether it is an axial length (step height, shoulder width) rather than a diameter.
    Axial lengths are NOT id_in.

Step 6 — VERIFY:
  a) id_in < od_in. Required. If violated, you picked a diameter that is too large.
  b) id_in > 0. Required. If zero, you picked a null bore (solid part) — set id_in = null.
  c) Your id_in value should correspond to a visually INWARD dimension on the drawing,
     not a dimension that spans the outer profile.
  d) Tolerance ranges: if bore callout is "0.430 / 0.432" → id_in = 0.430 (lower bound)
     or the nominal; do NOT use 0.432 - 0.430 = 0.002 (that is the tolerance, not the bore).
  e) SIZE PLAUSIBILITY: id_in / od_in should typically be 0.15–0.90 for hollow parts.
     If id_in / od_in < 0.10 (a tiny bore relative to the OD), double-check:
       → Did you accidentally use an orifice/port hole as id_in?
       → Does the drawing show a LARGER main bore you missed?
     If id_in / od_in > 0.95, double-check that you have the right OD.

Step 7 — SOLID SHAFT CASE:
  If NO bore callouts exist anywhere and the cross-section shows a solid profile
  (fully hatched with no hollow center), then id_in = null. This is correct.


=== HOW TO FIND FINISH LENGTH (length_in) ON ANY DRAWING ===

Step 1 — Look for a single dimension line that spans the ENTIRE axial extent of the part
  (from one end-face witness line to the other end-face witness line, parallel to the axis).
  This is usually the LARGEST linear dimension on the part profile.
  Labels: OAL, OVERALL, TOTAL LENGTH, or simply the longest parallel-axis dimension.
  STOP HERE if found: do NOT add any partial step dimensions to it.
  A step or shoulder dimension starting from the same end-face is a sub-section WITHIN
  this OAL — adding it to the OAL would double-count it.

Step 2 — If NO single span-the-full-extent line exists, use the CHAIN SUM fallback:
  List every sequential partial axial dimension (step A, step B, step C, …) that together
  cover the full axial span from one end-face to the other.
  SUM them: length_in = A + B + C + …
  This is the correct OAL when a drawing uses chain dimensioning instead of one overall callout.
  Set confidence to 0.80 when using this fallback.
  SEGMENT OVERLAP TRAP (critical — the most common chain-sum mistake):
    Only include segments that are NON-OVERLAPPING and tile the axis from end to end.
    If dimension X starts at the left face (0) and goes to 2.63, and dimension Y starts
    at the left face (0) and goes to 4.13, X is INSIDE Y. Sum = 4.13, NOT 4.13 + 2.63.
    Correct chain sum: find segments A, B, C… where end of A = start of B, end of B = start of C,
    and the first starts at one end-face and the last ends at the opposite end-face.

Step 3 — If even partial dimensions are absent, ESTIMATE from the part profile silhouette:
  Measure the visual end-to-end axial extent of the part outline relative to a known diameter
  (od_in). Use the od_in dimension tick marks as a scale reference.
  Set confidence to 0.60 when using this estimation.

Step 4 — NEVER return null for length_in on a turned part unless the drawing shows
  ONLY a single view with no axial dimension whatsoever.
  Always prefer a low-confidence estimate over null.

Step 5: length_in < max_length_in (raw cutoff is always slightly longer).
  If max_length_in is absent or equal, max_length_in = null (do not invent it).

Step 6 — CHAIN-SUM VERIFICATION (mandatory cross-check):
  After choosing length_in, verify it by enumerating ALL partial axial dimensions visible
  on the part profile (step widths, shoulder lengths, etc.) and summing them.
  Their sum should approximate length_in within ~5%.
  If the sum is significantly different from your length_in candidate:
    → Either your length_in is a partial/incorrect dim — revisit.
    → Or some partial dims were missed — identify and add them.
  This cross-check catches the most common error: picking a long step instead of OAL.

Step 7 — SCALE SANITY CHECK:
  For typical CNC turned parts, (length_in / od_in) is usually in the range 0.2 to 12.
  If your length_in / od_in > 15, you may have an error (extremely long slender shaft is unusual).
  If your length_in / od_in < 0.1, you may have picked a step width instead of OAL.
  These are soft checks — complex parts can still fall outside range — but if violated, re-examine.

Step 9 — COMMON ERRORS TO AVOID:
  DO NOT use: bore/hole depth as length_in (a depth callout like "2.720 DEEP" or the max
              z_end of bore features is the bore depth, NOT the OAL of the part).
  DO NOT use: a groove width or step height as length_in.
  DO NOT use: a raw/cutoff length from the RM table as length_in.
  DO NOT use: a dimension from an unrelated view (section detail, local callout).
  DO use: the dimension that spans from the LEFTMOST end-face witness line to the
          RIGHTMOST end-face witness line on the main part profile view.
  BLIND BORE CHECK: if the part has a blind bore (closed at one end, like a cup or piston),
    the OAL = bore depth + bottom wall thickness. Verify the OAL dimension is the
    FULL end-to-end span, not just the bore depth.
  BORE DEPTH CHAIN-IN TRAP (very common error on capped/bored parts):
    A bore/ID depth measured INWARD from an end-face is NOT an OD axial step that
    extends BEYOND that face. Do NOT add a bore depth to the OAL chain sum.
    If your chain sum is: (OD steps) + (bore depth) = total, the bore depth term is
    an interior dimension — the OAL = the OD steps alone.
    Example: OD stepped profile spans 4.13"; bore 0.88" dia is 0.75" deep from the right face.
    OAL = 4.13", NOT 4.13 + 0.75 = 4.88". The bore lives INSIDE the 4.13" envelope.
    Quick check: if removing the last chain-sum term gives a value that matches an
    explicit dimension callout on the drawing, that shorter value is the true OAL.


=== OCR NOISE HANDLING ===
  - Phi/diameter symbol may appear as: "O", "0", "/O", "(O", "Dia", "D"
  - Spaces inside numbers: "1 .24" -> 1.24,  "0 .63" -> 0.63
  - Character swaps: O<->0, l<->1, S<->5, B<->8
  - Bracket notation: "1.006-1.008 [25.553-25.603]" means the first range is inches,
    the bracketed range is millimeters -- use the inch value.
  - Tolerance notation "+0.000 / -0.001" belongs to the preceding dimension.
  - Table columns labeled "(MM)" or "[MM]" are metric -- convert to inches (/ 25.4).
  - Ignore rows containing KGS, LBS, WEIGHT, COST -- those are not dimensions.


=== MACHINED FEATURES (features array) ===
Include ONLY features that have an explicit dimensional callout on the drawing.
NEVER infer a feature from part silhouette shape alone.
Confidence thresholds: 0.90+ callout clearly legible; 0.70-0.89 partially obscured; <0.70 DO NOT include.

Feature types and required fields:
  groove:      Rectangular OD relief cut. Needs width + depth or groove OD callout.
               Fields: z_start, z_end (axial extent in inches), od_diameter (groove bottom OD).
  chamfer:     Bevel at a step or end face. Needs "C×" or "× × 45" annotation.
               Fields: z_pos, size_in (leg length in inches), angle_deg (default 45), face (od/id/both).
  thread:      Screw thread region. Extract ANY thread callout on the drawing, including:
               - Unified thread: "1/4-20 UNC-2A", "3/4-10 UNC", "1-8 UNC-2B"
               - Pipe thread: "1-1/2 NPT", "1-11.5 NPT", "3/4 NPT", "2 NPT THRD"
               - Metric thread: "M24×2.0", "M16×1.5-6g"
               - Any callout with "THD", "THRD", "THREAD", "TPI", "UNC", "UNF", "NPT", "NPTF",
                 "BSP", "RSP", or a dash-number pattern like "1-1/2-11.5" adjacent to the OD.
               Also extract if the drawing profile clearly shows thread crest lines covering a
               zone of the OD and a pitch/TPI is visible anywhere (e.g. "11.5 TPI").
               Fields: z_start, z_end (axial extent in inches from near end — estimate from
               drawing proportions if not labelled), od_diameter (thread major dia), spec_text
               (full callout text, e.g. "1-1/2 NPT"), face ('od' for external, 'id' for internal).
  fillet:      Concave radius between surfaces. Needs "R×" callout.
               Fields: z_pos, size_in (radius in inches), face (od/id/both).
  counterbore: Stepped bore entrance. Needs distinct Phi + depth callout separate from main bore.
               Fields: z_start, z_end, id_diameter (counterbore Phi).
  hole:        Drilled cross-hole or axial hole (orifice, port, vent, lube hole, etc.).
               Use when a Phi callout is explicitly separate from the main bore (not id_in).
               Also use when you see "NX" multiplier callouts (e.g. "12X", "4X") associated
               with small hole-like features on the OD or end face — these are almost always
               drilled holes (not merely fillets or chamfers).
               Fields: z_pos (axial centre in inches from near end), size_in (hole diameter),
                       count (integer from the "NX" prefix, e.g. 12 for "12X"),
                       face ('od'=cross-drilled radial, 'id'=axial/end-face, 'both'=through).
               IMPORTANT — "NX" PATTERN RULE:
                 Any callout with "NX" (e.g. "12X Ø0.125", "12X R.06 X .09", "4X Ø.171 DEEP .22")
                 on the OD or side wall strongly indicates drilled holes. Extract as type='hole'.
                 - size_in: use the Phi value if present; if only R (radius) is given, size_in = R * 2
                   (approximate hole diameter assuming the radius is at the hole mouth).
                 - count: the integer before X (12X → count=12, 4X → count=4).
                 - z_pos: the axial position where the holes are located on the part profile.
                   If the callout is near a specific OD feature or groove, use that axial centre.
                 - spec_text: copy the full annotation text (e.g. "12X R.06 X .09").
                 - Also add a companion fillet feature (type='fillet', same z_pos) if the callout
                   includes an R value, to mark the fillet at the hole entrance.

Set "features": [] if no qualifying features are found.


=== FINAL SANITY CHECK BEFORE RETURNING ===
  1. od_in is the LARGEST finished diameter on the part profile (not the raw stock).
  2. id_in < od_in (bore must fit inside the part). If not: re-examine.
  3. max_od_in > od_in (raw stock is larger than finish). If not: swap or set null.
  4. max_length_in >= length_in. If not: swap or set null.
  5. All numeric values are positive and in INCHES.
  6. length_in MUST NOT be null for any turned part.
     If you have not yet found it, apply the chain-sum fallback (sum all sequential axial
     partial dimensions) or the silhouette-estimate fallback before returning.
     Returning null for length_in is only acceptable if the drawing literally contains
     zero axial dimension information whatsoever.

Return ONLY the JSON object. No markdown, no explanation, no extra text."""

_VALIDATOR_SYSTEM = """\
You are a senior CNC machining QC engineer performing an independent review.
You will receive:
  1. Raw OCR text from an engineering drawing (may be noisy, partial, or garbled).
  2. A JSON of specs extracted by Agent 1.

Return ONLY this JSON object (no markdown, no extra text):
{
  "fields": {
    "<field_name>": {
      "value":      <Agent 1 value, or your corrected value if wrong>,
      "confidence": <0.0-1.0>,
      "issue":      "<problem description, or null>"
    }
  },
  "cross_checks": ["<issue strings -- empty list [] if none>"],
  "overall_confidence": <0.0-1.0>,
  "recommendation": "<ACCEPT | REVIEW | REJECT>"
}


=== WHAT EACH FIELD MEANS (your reference for validation) ===

  od_in      = the MAXIMUM outer diameter of the FINISHED part profile.
               This is NOT the raw stock size. It is the largest Phi callout
               visible on the part's outline in the drawing views.
               Raw stock OD (max_od_in) is always LARGER than od_in.

  max_od_in  = the raw bar stock / RM OD ordered from the supplier.
               Always > od_in. Usually found in an RM table or notes section,
               NOT as a dimension on the part profile.

  id_in      = the primary bore / inner diameter of the FINISHED part.
               Must be < od_in. For stepped bores, this is the smallest bore
               (the deepest, tightest feature). Null for solid shafts.

  max_id_in  = the largest bore step / counter-bore on the finished part.
               Found on the part profile. >= id_in.

  length_in  = the OVERALL end-to-end finished length of the part.
               The total span across all features. NOT a partial feature length.

  max_length_in = raw cutoff / RM length. Always >= length_in.


=== PRIORITY VALIDATION: od_in (Finish OD) ===

This is the most commonly mis-extracted field. Validate with extra care:

  a) Independently scan the OCR text for ALL diameter callouts on the part profile.
     Collect them. The LARGEST one is the correct od_in.

  b) Common extraction errors by Agent 1:
       ERROR 1: Agent 1 picks a SMALLER diameter (e.g., main shaft OD) and misses
                the larger flange/collar/shoulder that is the actual maximum OD.
                --> If Agent 1's od_in is NOT the largest part-profile diameter, flag it.
       ERROR 2: Agent 1 picks the RAW STOCK OD (max_od_in) thinking it is the finish OD.
                --> Raw stock is in an RM table/notes, not in the part profile view.
                --> If Agent 1's od_in > all visible part-profile diameters, flag it.
       ERROR 3: Agent 1 picks a bore diameter (id) as the OD.
                --> od_in must always be larger than id_in.

  c) Confidence guide:
       0.90-1.00 : od_in matches the clearly largest diameter on the part profile.
       0.70-0.89 : largest diameter inferred (OCR noisy, some values uncertain).
       0.50-0.69 : multiple similarly-sized diameters, ambiguous which is largest.
       < 0.50    : cannot determine from available text.


=== PRIORITY VALIDATION: id_in (Finish ID) ===

  FUNDAMENTAL CHECK — Is it really a bore DIAMETER, not something else?
  a) Independently collect ALL bore callouts from the OCR text:
       - Phi/O symbol followed by a number SMALLER than od_in
       - Lines labeled "ID", "BORE", or "INNER DIA"
       - Callouts in cross-section views pointing inward toward the center-line
     List them all before choosing.
  b) Eliminate non-bore candidates:
       - Axial dimensions WITHOUT a Phi prefix → step heights, bore DEPTHS — NOT id_in.
       - Tolerance ranges like "+0.000 / -0.001" → not a diameter value.
       - Dimensions > od_in → impossible for a bore; discard.
  c) CRITICAL: Distinguish TRUE counter-bore steps from orifice/passage/port holes:
       COUNTER-BORE STEPS: concentric, coaxial bores that share the same main axis.
         Each is a machined step reduction. For these: id_in = SMALLEST bore diameter.
       ORIFICE / PASSAGE / PORT HOLES: small cross-drilled or off-axis holes that are
         separate features from the main bore. These appear as:
           - Small Phi callouts labeled separately (e.g., "2X Ø.125 PORTS")
           - Much smaller than the obvious main bore (ratio < 0.30 of main bore)
           - Not concentric / at different axial locations, not on the main axis
         For these: the tiny holes are NOT id_in. id_in = the main bore diameter.
         EXAMPLE: if drawing shows Ø.880 main bore + Ø.171 orifice holes → id_in = 0.880.
           If Agent 1 returned id_in = 0.171, that is WRONG. Override to 0.880.
       If uncertain whether a small bore is a step or an orifice, prefer the larger bore
       as id_in (it is the primary functional bore) and flag ambiguity in cross_checks.
  d) Select the primary bore:
       Single bore: use that diameter.
       Multi-step bore (true counter-bore): id_in = SMALLEST bore; max_id_in = LARGEST bore.
       Main bore + orifice/passage holes: id_in = MAIN bore (largest functional bore).
       No bore at all (solid shaft): id_in = null.
  e) id_in must be < od_in. If Agent 1's id_in >= od_in -> REJECT immediately.
     Flag the error with: issue: "id_in ({value}) >= od_in ({od}) — Agent 1 likely picked
     an OD step or bore depth rather than the bore diameter".
  f) Plausibility checks:
       - For a sleeve/bushing: id_in is typically 40–90% of od_in.
       - For a thick-walled part: id_in may be 20–60% of od_in.
       - For a piston or cup: id_in may be 40–75% of od_in (large main bore).
       - id_in / od_in < 0.10 for a part labeled PISTON, SLEEVE, or BUSHING is WRONG.
         These parts have large bores. Investigate whether Agent 1 returned an orifice
         hole diameter instead of the main bore. Flag and correct if so.
       - If id_in is suspiciously small or zero for a hollow part, flag it.
  g) Bore depth vs bore diameter:
       If the OCR shows e.g. "Phi0.430 x 0.500 DEEP", id_in = 0.430 only.
       0.500 is the bore DEPTH — if Agent 1 returned 0.500 as id_in, flag and correct it.
  h) Null is valid ONLY for solid shafts. If the part appears hollow in the drawing
     section view (center region is NOT cross-hatched), null is an error.


=== PRIORITY VALIDATION: length_in (Finish Length) ===

  a) length_in = the end-to-end OVERALL length of the finished part along its rotational axis.
     It is the LARGEST axial dimension on the finished part profile — no partial feature
     can be equal to or larger than the OAL.

  b) CHAIN-SUM CROSS-CHECK (perform this independently of Agent 1's answer):
       i.  Collect every partial axial dimension visible on the part profile
           (step widths, shoulder lengths, bore depths if they equal part sections).
       ii. Sum them: partial_sum = A + B + C + ...
       iii.Compare partial_sum to Agent 1's length_in:
             match (within 5%): confirms the value. Accept.
             partial_sum > Agent 1's length_in: Agent 1 may have used a fraction — flag & correct.
             partial_sum < Agent 1's length_in: check if Agent 1 uses a RM length. Flag.
             partial_sum roughly equals Agent 1's value: use partial_sum as the confirmed length.

  c) COMMON ERRORS MADE BY Agent 1 — check for each:
       ERROR A — Bore DEPTH confused with OAL:
         If Agent 1's length_in matches a bore depth callout (e.g. "0.500 DEEP") and the part
         is clearly longer than a bore, flag error. OAL = total part span, not bore depth.
       ERROR B — Step height or groove width used as OAL:
         Any dimension that is clearly just one section of the part, not the full span.
         All step heights + step widths should SUM UP TO the OAL, not equal it.
       ERROR C — RM/stock cutoff length used as OAL:
         If Agent 1's length_in = max_length_in (both same value), Agent 1 likely read the
         raw cutoff instead of the finish length. Flag: length_in should < max_length_in.
       ERROR D — null returned for a clearly dimensioned part:
         Re-examine. Apply chain-sum. Apply silhouette estimate. Return a value with
         confidence 0.60-0.80 and note: issue = "OAL not explicit — estimated from chain sum"

  d) If Agent 1 returned null for length_in:
       i.  Single-span dimension? Use it directly.
       ii. No single span? List ALL axial partial dims, sum them → use as length_in, conf 0.80.
       iii.No partial dims? Estimate from silhouette using od_in as scale → conf 0.60.
       Flag: issue = "Overall length not found explicitly; value estimated."

  e) Scale sanity:
       length_in / od_in is normally 0.2 to 12. Outside this range → flag for review.
       length_in < id_in is physically impossible — flag and reject if so.

  f) length_in < max_length_in (raw cutoff is longer). If reversed, flag it.
  g) A null length_in with no correction attempted must be flagged: REJECT recommendation.


=== LOGICAL RULES (check all, any violation changes recommendation) ===
  od_in > id_in              (when both non-null) -- violation -> REJECT
  max_od_in > od_in          (when both non-null) -- violation -> REVIEW (may be missing RM data)
  max_id_in >= id_in         (when both non-null) -- violation -> REVIEW
  max_length_in >= length_in (when both non-null) -- violation -> REVIEW
  length_in is null          -- always flag issue; attempt chain-sum correction -> REVIEW
  All present dimension values > 0                -- any zero/negative -> REJECT
  material is a recognizable engineering material
  quantity is a positive integer


=== RECOMMENDATION ===
  ACCEPT : od_in is the largest part-profile diameter, id_in < od_in, length_in is
           overall length, confidence >= 0.85 for all three, no logical violations.
  REVIEW : any priority dim has confidence 0.60-0.84, or minor ambiguity exists,
           or RM dims (max_od_in, max_length_in) could not be confirmed.
  REJECT : od_in <= id_in, any dim is zero/negative, or a clear extraction error
           cannot be corrected from the available text.

Return ONLY the JSON object."""


# ---------------------------------------------------------------------------
# PDF text extraction helper
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_path: Path | str) -> str:
    """Extract raw text from a PDF.

    Strategy (each step only runs if the previous returned empty):
    1. pdfplumber  – works for text-layer PDFs
    2. PyMuPDF     – second text-layer attempt
    3. OCR via pre-rendered page images (outputs/pdf_pages/page_N.png)
    4. OCR via fitz-rendered pixmap (works for vector-only / CAD drawings)
    """
    pdf_path = Path(pdf_path)
    text_parts: list[str] = []

    # --- 1. pdfplumber ---
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    text_parts.append(t)
        if text_parts:
            logger.info("_extract_pdf_text: used pdfplumber (%d parts)", len(text_parts))
            return _normalize_drawing_text("\n".join(text_parts))
    except Exception as exc:
        logger.debug("pdfplumber failed (%s)", exc)

    # --- 2. PyMuPDF text layer ---
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        for page in doc:
            t = page.get_text("text") or ""
            if t.strip():
                text_parts.append(t)
        doc.close()
        if text_parts:
            logger.info("_extract_pdf_text: used PyMuPDF text layer (%d parts)", len(text_parts))
            return _normalize_drawing_text("\n".join(text_parts))
    except Exception as exc:
        logger.debug("PyMuPDF text layer failed (%s)", exc)

    # --- 3 & 4. OCR fallback (vector / scanned PDFs) ---
    # Try pre-rendered images first; if absent, render via fitz.
    try:
        import io
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        ocr_images: list[Image.Image] = []

        # 3a. Pre-rendered page images (produced by upload_and_render_pdf)
        rendered_dir = pdf_path.parent.parent / "outputs" / "pdf_pages"
        if rendered_dir.is_dir():
            for img_path in sorted(rendered_dir.glob("page_*.png")):
                try:
                    ocr_images.append(Image.open(img_path).convert("RGB"))
                except Exception:
                    pass

        # 3b. Render on-the-fly via fitz (300 DPI) if no pre-rendered images
        if not ocr_images:
            import fitz  # type: ignore
            doc = fitz.open(str(pdf_path))
            mat = fitz.Matrix(300 / 72, 300 / 72)
            for page in doc:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                ocr_images.append(img)
            doc.close()

        for img in ocr_images:
            t = pytesseract.image_to_string(img, config="--psm 6") or ""
            if t.strip():
                text_parts.append(t)

        if text_parts:
            logger.info("_extract_pdf_text: used OCR on %d image(s)", len(ocr_images))
            return _normalize_drawing_text("\n".join(text_parts))
    except Exception as exc:
        logger.warning("OCR fallback failed (%s)", exc)

    raise RuntimeError(f"Could not extract text from PDF: {pdf_path}")


def _normalize_drawing_text(text: str) -> str:
    """Repair common encoding mojibake found in CNC engineering drawing PDFs.

    The most frequent issue: the PDF text layer was encoded as UTF-8 but the
    extraction library decoded each byte as Latin-1, turning multi-byte
    sequences like \\xc2\\xb1 (±) into two Latin-1 characters (Â±).
    We attempt to reverse that by re-encoding as Latin-1 and decoding as UTF-8.
    If that produces fewer high-codepoint characters the repair is accepted.
    """
    # 1. Attempt mojibake repair (utf-8 bytes decoded as latin-1)
    try:
        repaired = text.encode("latin-1").decode("utf-8")
        orig_high = sum(1 for c in text     if ord(c) > 127)
        rep_high  = sum(1 for c in repaired if ord(c) > 127)
        if rep_high < orig_high:
            text = repaired
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

    # 2. Remove null bytes that appear in some CAD PDF text layers
    text = text.replace("\x00", "")
    return text


def _collect_page_images(pdf_path: Path) -> list[bytes]:
    """Return raw PNG bytes for each page of *pdf_path*.

    Tries pre-rendered images first (from upload pipeline).
    Falls back to rendering via PyMuPDF at 200 DPI.
    Returns an empty list if neither method is available.
    """
    images: list[bytes] = []

    # 1. Pre-rendered PNGs produced by upload_and_render_pdf
    rendered_dir = pdf_path.parent.parent / "outputs" / "pdf_pages"
    if rendered_dir.is_dir():
        for img_path in sorted(rendered_dir.glob("page_*.png")):
            try:
                images.append(img_path.read_bytes())
            except Exception:
                pass
        if images:
            logger.info("_collect_page_images: loaded %d pre-rendered images", len(images))
            return images

    # 2. Render on-the-fly via PyMuPDF at 200 DPI (good quality, reasonable size)
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        mat = fitz.Matrix(200 / 72, 200 / 72)
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            images.append(pix.tobytes("png"))
        doc.close()
        logger.info("_collect_page_images: rendered %d pages via PyMuPDF", len(images))
    except Exception as exc:
        logger.warning("_collect_page_images: PyMuPDF render failed (%s)", exc)

    return images


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _recover_truncated_json(cleaned: str) -> dict[str, Any]:
    """Recover as many fields as possible from a token-truncated JSON string.

    Scans character-by-character, tracking the depth stack and string state.
    Rewinds to the last position where all open structures were complete,
    strips any trailing comma, closes all open brackets, then parses.
    """
    # Phase 1: scan to find the last "safe" position (after a complete value,
    # before or at the comma that follows it).
    in_str = False
    esc = False
    depth: list[str] = []           # track open { [
    last_safe = 0                    # byte offset after last complete field

    for i, ch in enumerate(cleaned):
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        # --- not in string ---
        if ch == '"':
            in_str = True
        elif ch in "{": 
            depth.append("{")
        elif ch == "[":
            depth.append("[")
        elif ch == "}":
            if depth and depth[-1] == "{":
                depth.pop()
            last_safe = i + 1        # a complete object just closed
        elif ch == "]":
            if depth and depth[-1] == "[":
                depth.pop()
            last_safe = i + 1
        elif ch == "," and not depth == []:
            # After a comma a field is complete — record position just before comma
            last_safe = i

    if last_safe == 0:
        raise ValueError("Cannot recover partial JSON: no complete field boundary found")

    partial = cleaned[:last_safe].rstrip().rstrip(",")

    # Phase 2: re-scan the partial to get the accurate open depth stack.
    open_stack: list[str] = []
    in_str2 = False
    esc2 = False
    for ch in partial:
        if esc2:
            esc2 = False
            continue
        if in_str2:
            if ch == "\\":
                esc2 = True
            elif ch == '"':
                in_str2 = False
            continue
        if ch == '"':
            in_str2 = True
        elif ch in "{[":
            open_stack.append(ch)
        elif ch in "}]":
            if open_stack:
                open_stack.pop()

    closing = "".join("}" if c == "{" else "]" for c in reversed(open_stack))
    recovered = partial + "\n" + closing
    return json.loads(recovered)


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Best-effort JSON parse: strips markdown fences if present.

    Handles complete fences, unclosed/truncated fences, and JSON responses
    that were cut off mid-string by the model's output token limit.
    """
    cleaned = raw.strip()

    # Complete fence: ```[json] ... ``` — extract content between the fences.
    fence_match = _JSON_FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    elif cleaned.startswith("```"):
        # Truncated / unclosed fence — strip the opening fence line only and
        # attempt to parse the remaining content (the JSON may still be valid
        # even if the closing ``` was cut off by a token limit).
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1).strip()
        # Also remove a trailing partial ``` if present (e.g. `` ` `` fragments)
        cleaned = re.sub(r"\s*`+\s*$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_exc:
        # The response may be token-truncated mid-string.  Attempt recovery by
        # closing all open brackets at the last valid field boundary.
        logger.warning(
            "[LLM] JSON parse failed (%s) — attempting truncation recovery", first_exc
        )
        try:
            result = _recover_truncated_json(cleaned)
            logger.info("[LLM] Partial JSON recovery succeeded (fields: %s)", list(result.keys()))
            return result
        except Exception as recovery_exc:
            logger.warning("[LLM] Recovery also failed: %s", recovery_exc)
        raise ValueError(f"LLM returned non-JSON response: {first_exc}\n---\n{raw[:400]}") from first_exc


# ---------------------------------------------------------------------------
# Merged single-call agent (replaces Agent 1 + Agent 2 + inter-agent delay)
# ---------------------------------------------------------------------------

_MERGED_SYSTEM = _EXTRACTOR_SYSTEM + "\n\n" + """\
After extracting all fields, immediately perform a self-validation pass.
Return a SINGLE JSON object with this exact top-level structure:

{
  "extracted": {
    <all extraction fields from above>
  },
  "validation": {
    "fields": {
      "<field_name>": {"value": <value>, "confidence": <0.0-1.0>, "issue": "<or null>"}
    },
    "cross_checks": ["<issue strings -- empty list [] if none>"],
    "overall_confidence": <0.0-1.0>,
    "recommendation": "<ACCEPT | REVIEW | REJECT>"
  }
}

For the validation:
- ACCEPT  : od_in is the largest part-profile diameter, id_in < od_in, length_in is
            overall length, confidence >= 0.85 for all three, no logical violations.
- REVIEW  : any priority dim has confidence 0.60-0.84, minor ambiguity, or RM dims
            (max_od_in, max_length_in) could not be confirmed, or there is any sign that
            a title/material stock note may have been used as a finish dimension, or a
            centerline-to-face half-dimension may have been used as full OAL.
- REJECT  : od_in <= id_in, any dim is zero/negative, or a clear extraction error
            cannot be corrected from available text.

Return ONLY the JSON object. No markdown, no explanation, no extra text."""

_MERGED_VISION_PROMPT = """\
{system}

You are looking at the actual engineering drawing image(s) above.

STEP 1 -- UNDERSTAND THE DRAWING ORIENTATION
  Engineering drawings can present the part in ANY orientation:
    - Horizontal (part axis runs left-right)
    - Vertical   (part axis runs top-bottom)
    - Angled or isometric view
  Do NOT assume horizontal. First, identify the part's axis of symmetry
  (typically shown by a center-line: long-dash short-dash pattern).
  The part profile is drawn symmetrically about that center-line.

STEP 1B -- CLASSIFY PART SHAPE (before assigning any number to any field)
  Look at the overall outline of the part in the main view and decide:
    DISC / RING shape: the part outline is wider than it is long along the axis.
      od_in CAN be larger than length_in — this is correct for bushings, spacers, rings.
      Do NOT default "largest number = length_in" for these parts.
    SHAFT shape: the part is longer along its axis than it is wide.
      length_in will be larger than od_in — the typical case.
  Write down your shape classification before reading any dimensions.

STEP 2 -- FIND THE OUTER BOUNDING ENVELOPE (od_in)
  The Finish OD is the MAXIMUM outer diameter of the finished part.
  How to find it visually, regardless of orientation:
    a) Locate the part silhouette / profile outline in the main view.
    b) The OUTERMOST parallel witness/extension lines that span the FULL
       width of the part profile (perpendicular to the part axis) define
       the outer envelope.
    c) The diameter callout linked to those outermost lines = od_in.
    d) Cross-check: among ALL Phi callouts on the part profile, the
       LARGEST value is od_in.
    e) Raw/RM dimensions appear in a SEPARATE table or notes block --
       do NOT use those as od_in.
     f) If the title block / material note gives a stock size (e.g. N.NN DIA, BAR, TUBE,
       BLANK OD) and the finished profile shows a smaller OD with its own profile callout,
       the smaller profile OD is od_in and the title/material note value is max_od_in.
  PHI SYMBOL RULE (critical — prevents OD/length swap on disc/ring parts):
    ANY callout preceded by Ø / Phi / circle-O / "/O" / "Dia" = a DIAMETER, not a length.
    Even if the Phi-prefixed number is the LARGEST number on the drawing —
    a Phi prefix means it is od_in or id_in, NEVER length_in.
    OCR renders Phi as: "Ø", "/O", "O", "Dia", "D" — check for it before every number.

STEP 3 -- FIND THE BORE / INNER DIAMETER (id_in)
  The bore diameter is the WIDTH of the hole inside the part — NOT how deep it goes.

  ORIFICE vs MAIN BORE WARNING:
    Complex parts (pistons, hydraulic bodies, cylinders) can have BOTH:
      A) A large MAIN bore (the primary hollow cavity, e.g., Phi0.880)
      B) Small ORIFICE / PASSAGE HOLES (tiny ports or cross-drilled holes, e.g., Phi0.125)
    The MAIN BORE is id_in. The orifice holes are separate features — NOT id_in.
    Orifice holes are visually tiny relative to the main bore, often labeled "2X" or "4X",
    and are located at specific axial positions rather than spanning the main bore depth.
    If you see a range of bore sizes (e.g., 0.88, 0.265, 0.145, 0.124), the LARGEST one
    that represents the main hollow cavity is id_in. The tiny ones are orifices.

  ENUMERATION PROTOCOL — do all sub-steps before choosing:
  a) Find the section view (cross-hatched region showing material cut away by the bore).
     The hollow center region (UN-hatched space inside the hatching boundary) IS the bore.
  b) Collect EVERY bore-related callout:
       • Phi / circle-O / Dia symbol followed by a number that points INWARD
       • Lines labeled "ID", "BORE", "INNER DIA"
       • Any callout that refers to the interior hollow space
       • In a table under a column labeled "ID" or "Bore Dia"
  c) Write out ALL bore diameters found with their visual context:
       Is each bore a main cavity bore or a small orifice/port hole?
       Main bore: large diameter, shown in section view, defines the hollow cavity.
       Orifice: tiny diameter, often multiple ("2X", "4X"), separate from the main cavity.
  d) CLASSIFY each bore callout:
       True counter-bore steps (concentric, coaxial): id_in = smallest; max_id_in = largest.
       Main bore + orifice holes: id_in = main bore (largest functional cavity). Orifices go
         in features[] only — NOT as id_in.
       Single bore: that diameter is id_in.
  e) SELECT id_in = the primary functional bore (the main hollow cavity diameter).
  f) CRITICAL — bore DIAMETER vs bore DEPTH:
       A callout reading "Phi0.430 THRU" or "Phi0.430 x 0.500 DEEP":
         id_in = 0.430 (the Phi number = diameter)
         0.500 is the bore DEPTH along the axis — it is NOT id_in.
       If you see a small number WITHOUT a Phi/Dia prefix at the bore region,
       verify it is a diameter (measured perpendicular to axis) vs a depth (parallel to axis).
  g) VERIFY: id_in < od_in. If not, you picked the wrong dimension — re-examine.
     VERIFY: id_in / od_in > 0.10 for hollow parts (piston, sleeve, bushing).
     If id_in / od_in < 0.10, you likely picked an orifice hole — find the main bore.
  h) Tolerance: if bore is "0.430 / 0.432", id_in = nominal (lower bound) = 0.430.
     The tolerance band width (0.002) is not id_in.
  i) Solid shaft with no bore: id_in = null (only when NO hollow interior exists).

STEP 4 -- FIND THE OVERALL LENGTH (length_in)
  Overall length = end-to-end finished span PARALLEL to the part's axis. It is NOT a diameter.

  DIMENSION DIRECTION RULE — always apply this before assigning length_in:
    LENGTH dimensions: witness lines run PARALLEL to the part axis. NO Phi/Ø prefix.
    DIAMETER dimensions: witness lines run PERPENDICULAR to the axis. ALWAYS Phi-prefixed.
    For a DISC / RING part (where od_in > length_in), the OD witness lines may span the
    visually "widest" extent of the drawing — but they measure width perpendicular to axis
    and carry a Phi prefix. That Phi-prefixed number is od_in, NOT length_in.
    The length witness lines span the SHORT axial thickness and have NO Phi prefix.
    Rule: NEVER assign a Phi-prefixed callout to length_in, regardless of its magnitude.

  BLIND-BORE CRITICAL WARNING (pistons, cups, cylinders, receivers):
    If the part is a BLIND-BORE part (open on one end, closed wall on the other):
    → The bore DEPTH (the distance from the open face to the bottom of the bore) is NOT the OAL.
    → OAL = bore depth + closed-end bottom wall thickness.
    → Example: bore depth = 2.720", wall thickness = 0.280" → OAL = 3.000".
    → The CORRECT OAL dimension runs from the OPEN FACE to the CLOSED BOTTOM FACE of the part.
    → This is always LARGER than any bore depth dimension callout.
    → Cross-check: if your chain sum of bore-depth dims < visual part length, there's a wall.

  COMMON ERRORS (avoid these):
    ✗ Bore DEPTH ("2.720 DEEP") — this is the interior depth, NOT the OAL for blind-bore parts.
    ✗ Step HEIGHT or SHOULDER WIDTH — one segment of the part, not the full span.
    ✗ RM / cutoff length from the stock table — that is max_length_in, always > length_in.
    ✗ A diameter callout (Phi numbers) — length is measured along the axis, not width.
    ✗ Centerline-to-face half-dimension on a symmetric part — double it to get full OAL.
    ✗ Title-block or material-note stock dimension mistaken for a finished profile dimension.

  EXTRACTION PROTOCOL:
  a) PRIMARY — find the SINGLE dimension witness line spanning the FULL axial extent:
       It runs parallel (or near-parallel) to the part axis from the leftmost end-face
       to the rightmost end-face. Usually labeled OAL, OVERALL, TOTAL LENGTH, or unlabeled.
       It is typically the LARGEST linear dimension on the part profile.
       → If found and clearly spans the full extent: use it. Confidence 0.90+.
       → STOP: do NOT add partial step dimensions to it. Any step dimension that starts
         from the same end-face is a sub-section WITHIN this OAL, not additive beyond it.
       → If the drawing is symmetric about a center plane and the shown dimension only runs
         from centerline to one face, DOUBLE it to get the full OAL.
  b) CHAIN-SUM FALLBACK — if no single full-span line:
       i.  List EVERY sequential partial axial dimension you can find on the part profile
           (each step width, each shoulder length, each sub-section). Write them all out.
       ii. Only include NON-OVERLAPPING segments that tile the axis end-to-end:
           end of segment A = start of segment B, end of B = start of C, etc.
           The first segment must start at one end-face; the last must end at the other.
       iii.SEGMENT OVERLAP TRAP: if two dimensions both start from the same end-face,
           they OVERLAP — do NOT sum them. Use only the larger one (or the one that
           correctly spans to a known shoulder) and continue from there.
           Example: dims 2.63 and 4.13 both anchor from face 0 → 4.13 contains 2.63;
           use 4.13 as the OAL, do NOT compute 4.13 + 2.63 = 6.76.
       iv. BORE DEPTH CHAIN-IN TRAP: before summing, check each term — is it a bore/ID depth
           (depth of a hole or bore measured from an end-face inward) rather than an OD step?
           A bore depth is an INTERIOR dimension — it does NOT extend the part beyond the OD envelope.
           Remove any bore/ID depth terms from the OAL chain sum.
           Example: OD profile spans 4.13"; bore 0.88" dia is 0.75" deep from the right face.
           OAL = 4.13", NOT 4.13 + 0.75 = 4.88". The 0.75" bore depth is inside the 4.13" span.
           Quick check: if the chain sum minus the last term matches an explicit dimension on the
           drawing, that shorter value is likely the true OAL.
       v.  SUM the remaining OD-step terms: length_in = A + B + C … Confidence = 0.80.
       vi. Verify the sum makes visual sense against the part silhouette.
  c) SILHOUETTE ESTIMATE — if even partial dims are absent:
       Visually compare the axial extent of the part outline vs the od_in witness marks.
       Estimate based on the apparent aspect ratio. Confidence = 0.60.
  d) CHAIN-SUM VERIFICATION (always do this even when using method a):
       After choosing your length_in candidate,
       enumerate all visible partial axial dims and sum them.
       If partial_sum ≈ length_in (within ~5%): confirmed. Good.
       If partial_sum > length_in: your candidate is too small — likely a step, not OAL.
       If partial_sum << length_in: possibly missed some dims, or candidate is too large.
  e) NEVER return null for length_in on a turned part. Always use the best available method.
  f) Do NOT use a bore depth as length_in, even if it is the "longest" single number visible.
     Bore depths are interior axial extents — they equal the length of the hollow, not the part.
  g) Raw/cutoff length (max_length_in) is LONGER than length_in. If you see two similar long
     dimensions, the SMALLER one is typically the finish length, the LARGER the raw cutoff.

STEP 5 -- EXTRACT SUPPORTING FIELDS
  From title block, revision table, and notes:
  part_number, part_name, material, quantity, revision, finish,
  tolerance_od, tolerance_id, tolerance_length, max_od_in, max_id_in, max_length_in.

STEP 6 -- DETECT MACHINED FEATURES (only explicitly dimensioned features)
  Only include features you can directly observe with confidence >= 0.70.
  Do NOT infer a feature from part shape alone -- it must have a dimensional callout.
  groove:      Rectangular OD relief cut. Fields: z_start, z_end (inches), od_diameter (groove bottom OD).
  chamfer:     Bevel at edge. Needs "C×" or "× × 45" annotation. Fields: z_pos, size_in, angle_deg, face.
  thread:      Screw thread. Needs thread note (e.g. "1/4-20 UNC"). Fields: z_start, z_end, od_diameter, spec_text.
  fillet:      Radius transition. Needs "R×" callout. Fields: z_pos, size_in (radius), face.
  counterbore: Stepped bore entrance. Fields: z_start, z_end, id_diameter.
  Confidence: 0.90+ clearly legible callout; 0.70-0.89 partially obscured; <0.70 DO NOT include.
  Set features: [] if no qualifying features are found.

STEP 7 -- SELF-VALIDATE and return the combined JSON.
  Check all of the following before returning:
  1. od_in > id_in (bore must fit inside part). Violation → REJECT.
  2. max_od_in > od_in if present. Violation → REVIEW.
  3. max_length_in >= length_in if present. Violation → REVIEW.
  4. ORIFICE CHECK: id_in / od_in < 0.10 for a non-solid part → LIKELY WRONG.
     You may have returned an orifice/port hole as id_in instead of the main bore.
     Re-examine: is there a LARGER bore (the main cavity) that you missed?
     If so, correct id_in and note the issue. Set recommendation = REVIEW.
  5. BLIND-BORE LENGTH CHECK: if the part is a blind-bore type (piston, cup, cylinder),
     check that your length_in > max bore depth callout in the features array.
     If length_in equals a bore depth (z_end in features), you likely missed the closed wall.
     Estimate the wall thickness (typically 10–20% of OD) and correct length_in.
     Set confidence = 0.75 and note: "length_in corrected for blind-bore wall thickness".
  6. length_in null check: if null after all steps, apply chain-sum or silhouette estimate.
     A null length_in means the quoting system CANNOT price this part.
  Assign per-field confidence based on how clearly each value is identified.
  Set overall recommendation: ACCEPT / REVIEW / REJECT.

CRITICAL OUTPUT REQUIREMENT: Your response MUST be a single JSON object with BOTH of these
top-level keys: "extracted" (all spec fields) AND "validation" (recommendation,
overall_confidence, fields map, cross_checks list). Omitting "validation" breaks downstream
processing. Never return just the "extracted" block alone.

Return ONLY the combined JSON object as described. No markdown, no explanation.\
"""

_MERGED_TEXT_PROMPT = (
    "{system}\n\n"
    "=== ENGINEERING DRAWING TEXT (OCR) ===\n{text}\n"
    "=== END TEXT ===\n\n"
    "Extract the three priority dimensions (od_in, id_in, length_in) first, "
    "then fill the remaining fields, then self-validate.\n"
    "CRITICAL: Your response MUST be a single JSON object with BOTH top-level keys:\n"
    '  \'extracted\' — all spec fields\n'
    '  \'validation\' — recommendation, overall_confidence, fields, cross_checks\n'
    "Omitting the \'validation\' key will break downstream processing. Return the combined JSON now:"
)


class MergedAgent:
    """Single-call agent: extracts specs AND self-validates in one LLM request.

    Replaces the original Agent 1 + 62 s sleep + Agent 2 pattern, reducing
    quota consumption from 2 RPM slots to 1 per pipeline run.
    """

    MAX_TEXT_CHARS = 12_000
    MAX_IMAGE_BYTES = 4 * 1024 * 1024

    def run(
        self,
        pdf_text: str,
        page_images: list[bytes] | None = None,
        **llm_kwargs: Any,
    ) -> dict[str, Any]:
        temperature   = llm_kwargs.get("temperature", 0.0)
        max_tokens    = llm_kwargs.get("max_output_tokens", 8192)  # max — single merged call

        if page_images:
            safe_images = [img for img in page_images if len(img) <= self.MAX_IMAGE_BYTES]
            if not safe_images:
                safe_images = page_images[:1]

            prompt = _MERGED_VISION_PROMPT.format(system=_MERGED_SYSTEM)
            logger.info("[MergedAgent] Vision mode: %d page image(s)", len(safe_images))
            try:
                raw = llm_service.generate_with_image(
                    prompt,
                    safe_images,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
            except RuntimeError as exc:
                logger.warning("[MergedAgent] Vision failed (%s) — falling back to text", exc)
                page_images = None

        if not page_images:
            logger.info("[MergedAgent] Text-only mode")
            truncated = pdf_text[: self.MAX_TEXT_CHARS]
            prompt = _MERGED_TEXT_PROMPT.format(system=_MERGED_SYSTEM, text=truncated)
            raw = llm_service.generate_text(
                prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

        result = _parse_json_response(raw)

        # Normalise: if the LLM returned the old flat format (no "extracted" key),
        # wrap it so the rest of the pipeline still works.
        if "extracted" not in result:
            logger.warning("[MergedAgent] LLM returned flat format — wrapping into extracted/validation")
            extracted_fields = {
                k: result.get(k) for k in (
                    "part_number", "part_name", "material", "quantity",
                    "od_in", "max_od_in", "id_in", "max_id_in",
                    "length_in", "max_length_in",
                    "tolerance_od", "tolerance_id", "tolerance_length",
                    "finish", "revision", "features",
                )
            }
            # Ensure features is always a list (LLM may omit the key)
            if not isinstance(extracted_fields.get("features"), list):
                extracted_fields["features"] = []
            validation_fields = {
                k: result.get(k) for k in ("fields", "cross_checks", "overall_confidence", "recommendation")
            }
            result = {
                "extracted":  extracted_fields,
                "validation": {
                    "recommendation":    validation_fields.get("recommendation", "REVIEW"),
                    "overall_confidence": validation_fields.get("overall_confidence", 0.5),
                    "fields":            validation_fields.get("fields", {}),
                    "cross_checks":      validation_fields.get("cross_checks", []),
                },
            }

        # Synthesise validation if LLM returned extracted but omitted validation.
        # This is the most common reason for the "Validation section missing" warning.
        if "extracted" in result and "validation" not in result:
            logger.warning(
                "[MergedAgent] LLM omitted validation block — synthesising smart fallback"
            )
            ext  = result.get("extracted") or {}
            od   = float(ext.get("od_in")     or 0)
            id_  = float(ext.get("id_in")     or 0)
            ln   = float(ext.get("length_in") or 0)
            name = str(ext.get("part_name") or "").upper()
            material = str(ext.get("material") or "").upper()
            max_od = float(ext.get("max_od_in") or 0)

            hard_errors:  list[str] = []  # → REJECT
            soft_warnings: list[str] = []  # → REVIEW

            # Hard errors — geometry impossible
            if od > 0 and id_ > 0 and id_ >= od:
                hard_errors.append(f"id_in ({id_}) >= od_in ({od}) — impossible geometry")
            if od <= 0:
                hard_errors.append("od_in missing or zero — cannot price")
            if ln <= 0:
                hard_errors.append("length_in missing or zero — cannot price")

            # Soft warnings — suspicious but not impossible
            if od > 0 and id_ > 0:
                ratio = id_ / od
                hollow_names = ("PISTON", "SLEEVE", "BUSHING", "CYLINDER", "BORE", "LINER", "CUP")
                is_hollow_name = any(w in name for w in hollow_names)
                if ratio < 0.10 and is_hollow_name:
                    soft_warnings.append(
                        f"id_in/od_in = {ratio:.3f} < 0.10 for hollow part '{name}' "
                        f"— possible orifice-hole misidentification"
                    )
                elif ratio < 0.05:
                    soft_warnings.append(
                        f"id_in/od_in = {ratio:.3f} < 0.05 — unusually small bore relative to OD"
                    )
            if od > 0 and ln > 0:
                aspect = ln / od
                if aspect > 15:
                    soft_warnings.append(f"length/OD = {aspect:.1f} > 15 — unusually long part")
                elif aspect < 0.1:
                    soft_warnings.append(f"length/OD = {aspect:.2f} < 0.10 — unusually flat part")
            if od > 0 and max_od > 0 and material and "DIA" in material:
              if abs(max_od - od) <= 0.01:
                soft_warnings.append(
                  f"od_in ({od}) is nearly identical to max_od_in ({max_od}) while material contains 'DIA' "
                  f"— possible raw-stock diameter copied into Finish OD"
                )

            if hard_errors:
                rec  = "REJECT"
                conf = 0.30
                cross = hard_errors + soft_warnings
            elif soft_warnings:
                rec  = "REVIEW"
                conf = 0.65
                cross = soft_warnings + ["Validation auto-generated — LLM output budget exhausted"]
            else:
                # All three core dims present and sane — can auto-accept
                rec  = "ACCEPT"
                conf = 0.85
                cross = ["Validation auto-generated — extracted dims passed all sanity checks"]

            logger.info("[MergedAgent] Auto-validation: %s (conf=%.2f) — %s", rec, conf, cross)
            result["validation"] = {
                "recommendation":     rec,
                "overall_confidence": conf,
                "fields":             {},
                "cross_checks":       cross,
            }

        # Ensure extracted.features is always a list regardless of LLM format
        extracted = result.get("extracted")
        if isinstance(extracted, dict) and not isinstance(extracted.get("features"), list):
            extracted["features"] = []

        return result


# ---------------------------------------------------------------------------
# Agent 1 -- Extractor (kept for reference; no longer called by run_pipeline)
# ---------------------------------------------------------------------------

# Vision prompt: LLM literally sees the drawing image -- geometric reasoning
_EXTRACTOR_VISION_PROMPT = """\
{system}

You are looking at the actual engineering drawing image(s) above.

STEP 1 -- UNDERSTAND THE DRAWING ORIENTATION
  Engineering drawings can present the part in ANY orientation:
    - Horizontal (part axis runs left-right)
    - Vertical   (part axis runs top-bottom)
    - Angled or isometric view
  Do NOT assume horizontal. First, identify the part's axis of symmetry
  (typically shown by a center-line: long-dash short-dash pattern).
  The part profile is drawn symmetrically about that center-line.

STEP 1B -- CLASSIFY PART SHAPE (before assigning any number to any field)
  Look at the overall outline of the part in the main view and decide:
    DISC / RING shape: the part outline is wider than it is long along the axis.
      od_in CAN be larger than length_in — correct for bushings, spacers, rings, washers.
      Do NOT default "largest number = length_in" for these parts.
    SHAFT shape: the part is longer along its axis than it is wide.
      length_in will be larger than od_in — the typical case.
  Write down your shape classification before reading any dimensions.

STEP 2 -- FIND THE OUTER BOUNDING ENVELOPE (od_in)
  The Finish OD is the MAXIMUM outer diameter of the finished part.
  How to find it visually, regardless of orientation:
    a) Locate the part silhouette / profile outline in the main view.
    b) The OUTERMOST parallel witness/extension lines that span the FULL
       width of the part profile (perpendicular to the part axis) define
       the outer envelope. These lines may run:
         - Vertically   (when part axis is horizontal)
         - Horizontally (when part axis is vertical)
         - At any angle matching the drawing orientation
    c) The diameter callout (Phi symbol / O / circle symbol + number)
       linked to those outermost lines = od_in.
    d) Cross-check: among ALL Phi callouts on the part profile, the
       LARGEST value is od_in. Steps, shoulders, and flanges are all
       LESS than od_in unless they define the outermost extent.
    e) Raw/RM dimensions appear in a SEPARATE table or notes block away
       from the part profile -- do NOT use those as od_in.
     f) Generic stock-note trap: if a title/material note gives a stock size (DIA / BAR / TUBE /
       BLANK OD) and the actual finished profile view shows a smaller OD with profile callouts,
       the profile OD is od_in and the stock note belongs to max_od_in.
  PHI SYMBOL RULE (critical — prevents OD/length swap on disc/ring parts):
    ANY callout preceded by Ø / Phi / circle-O / "/O" / "Dia" = a DIAMETER, not a length.
    Even if the Phi-prefixed number is the LARGEST number on the drawing —
    a Phi prefix means it is od_in or id_in, NEVER length_in.
    OCR renders Phi as: "Ø", "/O", "O", "Dia", "D" — always check for this prefix.

STEP 3 -- FIND THE BORE / INNER DIAMETER (id_in)
  id_in is the primary finished bore inside the part.
  ORIFICE vs MAIN BORE: complex parts (pistons, hydraulic bodies) can have BOTH a large
  main bore (id_in) AND small orifice/passage/port holes. The tiny port holes are NOT id_in.
  id_in = the MAIN HOLLOW CAVITY bore (the largest functional bore, not a tiny port hole).
  Only use the "smallest bore" rule for true COUNTER-BORE STEPS (concentric coaxial steps).
  To find it:
    a) Look for the section view (hatched/cross-hatched area showing the
       interior). Section views may appear in any orientation.
    b) Bore callouts point INWARD toward the center-line.
    c) If the part has multiple CONCENTRIC counter-bore steps, id_in is the
       SMALLEST (deepest / innermost) bore diameter. Separate orifice holes
       are NOT counter-bore steps — ignore them for id_in selection.
    d) id_in must always be less than od_in -- if your candidate is not,
       re-examine.
    e) id_in / od_in < 0.10 for a hollow part (piston, sleeve): likely wrong.
       Check if a larger main bore was missed.
    f) If no bore exists (solid shaft), set id_in = null.

STEP 4 -- FIND THE OVERALL LENGTH (length_in)
  length_in is the end-to-end finished span of the entire part:

  DIMENSION DIRECTION RULE — apply before assigning length_in:
    LENGTH: witness lines PARALLEL to the axis, NO Phi/Ø prefix.
    DIAMETER: witness lines PERPENDICULAR to the axis, ALWAYS Phi-prefixed.
    For a DISC / RING (od_in > length_in), the OD witness lines span the widest visual
    extent but carry a Phi prefix — that number is od_in, NOT length_in.
    NEVER assign a Phi-prefixed callout to length_in regardless of its magnitude.

    a) It runs PARALLEL to the part's axis, regardless of drawing orientation.
    b) It is the end-to-end axial span. For disc/ring parts it may be SMALLER than od_in.
    c) PRIMARY: look for a SINGLE dimension spanning from one end-face to the other (OAL).
       If found — STOP. Do NOT add partial step dimensions to it. Any step dimension that
       starts from the same end-face is a sub-section WITHIN this OAL, not extra length.
       If the view is symmetric and the shown dimension runs only from the centerline or
       mid-plane to one end-face, DOUBLE it to recover the full OAL.
       SEGMENT OVERLAP TRAP (most common chain-sum error on stepped parts):
         If you see two axial dimensions that both anchor from the SAME end-face (e.g.,
         2.63\" and 4.13\" both measured from the left face), the larger one (4.13\") already
         CONTAINS the smaller one. OAL = 4.13\", NOT 4.13 + 2.63 = 6.76\".
         Only sum dimensions whose segments tile end-to-end without overlap:
         end of segment A = start of segment B, first starts at one face, last ends at the other.
    d) BLIND-BORE TRAP: if the part has a blind bore (open one end, closed other),
       the bore DEPTH is NOT the OAL. OAL = bore depth + closed-end wall thickness.
       Always look for a dimension spanning BOTH faces of the part, not just the bore depth.
    e) BORE DEPTH CHAIN-IN TRAP: a bore/ID depth measured INWARD from an end-face is NOT
       an OD axial step extending BEYOND that face. Do NOT add a bore depth to the OAL chain.
       If your chain sum is (OD steps) + (bore depth), remove the bore depth term — it is
       interior to the OD envelope.
       Example: OD steps total 4.13"; bore 0.88" dia runs 0.75" deep from the right face.
       OAL = 4.13", NOT 4.13 + 0.75 = 4.88".
       Quick check: if (chain sum − last term) matches an explicit callout, that is the true OAL.
    f) All partial OD-step dims should SUM to approximately length_in — used for VERIFICATION
       only. If sum > your OAL candidate, you likely picked a partial dim.
       If sum < your OAL, there is a wall/section not shown.
    g) Raw/cutoff length (max_length_in) is always slightly LONGER than
       length_in and is found in an RM table or stock notes, not on the
       part profile.

STEP 5 -- EXTRACT SUPPORTING FIELDS
  From title block, revision table, and notes:
  part_number, part_name, material, quantity, revision, finish (Ra / RMS spec),
  tolerance_od, tolerance_id, tolerance_length, max_od_in, max_id_in, max_length_in.
  Bracket notation [X.XXX] = millimeter equivalent -- prefer the inch value.

STEP 6 -- SANITY CHECK
  - od_in > id_in
  - max_od_in > od_in (if present)
  - max_length_in >= length_in (if present)
  - All values positive, in inches

Return ONLY the JSON object described in the system prompt. No markdown, no explanation.\
"""

# Text-only fallback prompt: used when no images are available
_EXTRACTOR_TEXT_PROMPT = (
    "{system}\n\n"
    "=== ENGINEERING DRAWING TEXT (OCR) ===\n{text}\n"
    "=== END TEXT ===\n\n"
    "Extract the three priority dimensions (od_in, id_in, length_in) first, "
    "then fill the remaining fields. Return the JSON now:"
)


class ExtractorAgent:
    """Agent 1: Extracts structured specs from the engineering drawing.

    Strategy (in priority order):
      1. VISION MODE  -- send the actual page image(s) to Gemini Vision so it
         can geometrically reason about the drawing (parallel lines, section
         views, largest diameter envelope, etc.).
      2. TEXT FALLBACK -- if no images available, use OCR text + text prompt.
    """

    MAX_TEXT_CHARS = 12_000
    MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB per image (Gemini inline limit)

    def run(
        self,
        pdf_text: str,
        page_images: list[bytes] | None = None,
        **llm_kwargs: Any,
    ) -> dict[str, Any]:
        temperature = llm_kwargs.get("temperature", 0.0)
        max_tokens  = llm_kwargs.get("max_output_tokens", 2048)

        if page_images:
            # --- Vision mode: LLM sees the actual drawing ---
            # Cap images to MAX_IMAGE_BYTES each to stay within Gemini limits
            safe_images = [
                img for img in page_images if len(img) <= self.MAX_IMAGE_BYTES
            ]
            if not safe_images:
                safe_images = page_images[:1]  # last resort: send even if large

            prompt = _EXTRACTOR_VISION_PROMPT.format(system=_EXTRACTOR_SYSTEM)
            logger.info(
                "[Agent 1] Vision mode: sending %d page image(s) to Gemini",
                len(safe_images),
            )
            try:
                raw = llm_service.generate_with_image(
                    prompt,
                    safe_images,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
            except RuntimeError as exc:
                logger.warning(
                    "[Agent 1] Vision call failed (%s) — falling back to text-only", exc
                )
                page_images = None  # trigger text fallback below

        if not page_images:
            # --- Text fallback: OCR text only ---
            logger.info("[Agent 1] Text-only mode (no images available)")
            truncated = pdf_text[: self.MAX_TEXT_CHARS]
            prompt = _EXTRACTOR_TEXT_PROMPT.format(
                system=_EXTRACTOR_SYSTEM, text=truncated
            )
            raw = llm_service.generate_text(
                prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

        return _parse_json_response(raw)


# ---------------------------------------------------------------------------
# Agent 2 -- Validator
# ---------------------------------------------------------------------------

# Vision prompt for validator
_VALIDATOR_VISION_PROMPT = """\
{system}

You are looking at the actual engineering drawing image(s) above.
You ALSO have the JSON extracted by Agent 1:

=== AGENT 1 EXTRACTED JSON ===
{extracted}
=== END JSON ===

Perform an INDEPENDENT visual verification of the three priority fields.
The part may be drawn in ANY orientation (horizontal, vertical, angled) --
reason about the part geometry, not about which direction lines run on the page.

1. VERIFY od_in (Finish OD):
   - Identify the part's center-line axis (regardless of drawing orientation).
   - Find the outermost witness lines perpendicular to that axis -- they define
     the outer bounding envelope.
   - The Phi callout on those outermost lines = correct od_in.
   - Is Agent 1's od_in the LARGEST Phi value on the part profile? If not, flag it.
   - Is Agent 1's od_in coming from an RM/STOCK table rather than the part profile?
     If yes, flag: "od_in appears to be Raw/RM OD, not Finish OD".

2. VERIFY id_in (Finish ID):
   - Find the section view (hatched interior) or center-line bore callouts.
   - Section views can appear in any orientation -- look for hatching patterns.
   - Is id_in the SMALLEST bore (innermost, deepest)? For stepped bores, the
     smaller diameter is id_in; the larger entry = max_id_in.
   - Is id_in < od_in? If not, reject.

3. VERIFY length_in (Finish Length):
   - The part's overall length runs along its axis of symmetry, regardless of
     whether the drawing is horizontal, vertical, or angled.
   - Is length_in the LARGEST linear dimension spanning the full part end-to-end?
   - Are partial feature dimensions visibly shorter than length_in?
   - Is max_length_in from the RM table (longer than length_in)?

4. VERIFY max_od_in / max_length_in:
   - These should come from an RM/STOCK table or notes block, NOT from the
     part profile view. If they appear as part-profile callouts, flag them.

Return ONLY the validation JSON object described in the system prompt.\
"""

# Text-only fallback for validator
_VALIDATOR_TEXT_PROMPT = (
    "{system}\n\n"
    "=== ORIGINAL PDF TEXT (truncated) ===\n{text}\n"
    "=== END TEXT ===\n\n"
    "=== AGENT 1 EXTRACTED JSON ===\n{extracted}\n"
    "=== END JSON ===\n\n"
    "Return your validation JSON now:"
)


class ValidatorAgent:
    """Agent 2: Validates Agent 1's extraction against the drawing.

    Uses vision when images are available; falls back to text-only.
    """

    MAX_TEXT_CHARS = 4_000
    MAX_IMAGE_BYTES = 4 * 1024 * 1024

    def run(
        self,
        pdf_text: str,
        extracted: dict[str, Any],
        page_images: list[bytes] | None = None,
        **llm_kwargs: Any,
    ) -> dict[str, Any]:
        temperature = llm_kwargs.get("temperature", 0.0)
        max_tokens  = llm_kwargs.get("max_output_tokens", 1024)
        extracted_json = json.dumps(extracted, indent=2)

        if page_images:
            safe_images = [
                img for img in page_images if len(img) <= self.MAX_IMAGE_BYTES
            ]
            if not safe_images:
                safe_images = page_images[:1]

            prompt = _VALIDATOR_VISION_PROMPT.format(
                system=_VALIDATOR_SYSTEM, extracted=extracted_json
            )
            logger.info(
                "[Agent 2] Vision mode: sending %d page image(s) to Gemini",
                len(safe_images),
            )
            try:
                raw = llm_service.generate_with_image(
                    prompt,
                    safe_images,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
            except RuntimeError as exc:
                logger.warning(
                    "[Agent 2] Vision call failed (%s) — falling back to text-only", exc
                )
                page_images = None  # trigger text fallback below

        if not page_images:
            logger.info("[Agent 2] Text-only mode (no images available)")
            truncated = pdf_text[: self.MAX_TEXT_CHARS]
            prompt = _VALIDATOR_TEXT_PROMPT.format(
                system=_VALIDATOR_SYSTEM,
                text=truncated,
                extracted=extracted_json,
            )
            raw = llm_service.generate_text(
                prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

        return _parse_json_response(raw)


# ---------------------------------------------------------------------------
# Code-level validation rules
# ---------------------------------------------------------------------------

def _positive(val: Any, label: str, issues: list[str]) -> float | None:
    """Coerce val to positive float, appending to issues on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f <= 0:
            issues.append(f"{label} must be positive (got {f})")
        return f
    except (TypeError, ValueError):
        issues.append(f"{label} is not a valid number (got {val!r})")
        return None


# Plausibility bounds for finish dimensions (inches)
_PLAUSIBILITY: dict[str, tuple[float, float]] = {
    "od_in":         (0.100, 24.000),
    "max_od_in":     (0.100, 30.000),
    "id_in":         (0.050, 20.000),
    "max_id_in":     (0.050, 20.000),
    "length_in":     (0.050, 120.000),
    "max_length_in": (0.050, 144.000),
}


def _code_validate(extracted: dict[str, Any]) -> list[str]:
    """Return list of rule violations found in *extracted*.

    Priority checks (Finish OD / Finish ID / Finish Length) run first.
    """
    issues: list[str] = []

    od      = _positive(extracted.get("od_in"),         "od_in",         issues)
    max_od  = _positive(extracted.get("max_od_in"),     "max_od_in",     issues)
    id_     = _positive(extracted.get("id_in"),         "id_in",         issues)
    max_id  = _positive(extracted.get("max_id_in"),     "max_id_in",     issues)
    length  = _positive(extracted.get("length_in"),     "length_in",     issues)
    max_len = _positive(extracted.get("max_length_in"), "max_length_in", issues)

    # --- Priority: OD > ID ---
    if od is not None and id_ is not None and od <= id_:
        issues.append(
            f"od_in ({od}) must be greater than id_in ({id_}) — "
            "Finish OD smaller than Finish ID is physically impossible"
        )

    # --- MAX dims must be >= finish dims ---
    if max_od is not None and od is not None and max_od < od:
        issues.append(
            f"max_od_in ({max_od}) must be >= od_in ({od}) — "
            "Raw Material OD cannot be smaller than Finish OD"
        )

    if max_id is not None and id_ is not None and max_id < id_:
        issues.append(
            f"max_id_in ({max_id}) must be >= id_in ({id_}) — "
            "Max ID cannot be smaller than Finish ID"
        )

    if max_len is not None and length is not None and max_len < length:
        issues.append(
            f"max_length_in ({max_len}) must be >= length_in ({length}) — "
            "Raw Material length cannot be shorter than Finish Length"
        )

    # --- MAX OD must still be > MAX ID ---
    if max_od is not None and max_id is not None and max_od <= max_id:
        issues.append(
            f"max_od_in ({max_od}) must be greater than max_id_in ({max_id})"
        )

    # --- Plausibility range checks ---
    vals = {
        "od_in": od, "max_od_in": max_od,
        "id_in": id_, "max_id_in": max_id,
        "length_in": length, "max_length_in": max_len,
    }
    for field, val in vals.items():
        if val is not None and val > 0:
            lo, hi = _PLAUSIBILITY[field]
            if not (lo <= val <= hi):
                issues.append(
                    f"{field} ({val}) is outside plausible range "
                    f"[{lo}, {hi}] inches — may be a unit conversion error"
                )

    # --- Quantity ---
    qty = extracted.get("quantity")
    try:
        if qty is not None and int(qty) <= 0:
            issues.append("quantity must be a positive integer")
    except (TypeError, ValueError):
        issues.append("quantity is not a valid integer")

    return issues


# ---------------------------------------------------------------------------
# Geometry segment cleaning (server-side merge of very short/flagged segments)
# ---------------------------------------------------------------------------
def _merge_two_segments(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two adjacent segments into one. Weighted averages by span*confidence."""
    a_start = float(a.get("z_start") or 0)
    a_end = float(a.get("z_end") or 0)
    b_start = float(b.get("z_start") or 0)
    b_end = float(b.get("z_end") or 0)
    new_start = min(a_start, b_start)
    new_end = max(a_end, b_end)
    span_a = max(0.0, a_end - a_start)
    span_b = max(0.0, b_end - b_start)
    conf_a = float(a.get("confidence") or 0)
    conf_b = float(b.get("confidence") or 0)

    def weighted(field: str) -> Any:
        va = a.get(field)
        vb = b.get(field)
        wa = span_a * max(conf_a, 0.01)
        wb = span_b * max(conf_b, 0.01)
        if va is None and vb is None:
            return None
        try:
            if va is None:
                return float(vb)
            if vb is None:
                return float(va)
            return (float(va) * wa + float(vb) * wb) / (wa + wb)
        except Exception:
            return va or vb

    merged = {
        "z_start": new_start,
        "z_end": new_end,
        "confidence": max(conf_a, conf_b),
        "od_diameter": weighted("od_diameter"),
        "id_diameter": weighted("id_diameter"),
        "spec_text": (a.get("spec_text") or "") + " | " + (b.get("spec_text") or ""),
    }
    return merged


def _clean_segments_list(segments: list[dict[str, Any]], min_span: float) -> list[dict[str, Any]]:
    """Return a new segments list where very short segments are merged into neighbors.

    Strategy:
      - Sort by z_start
      - For each short segment (span < min_span or flagged), merge into previous
        segment when available, otherwise merge into next.
    """
    if not segments:
        return segments
    segs = sorted(segments, key=lambda s: float(s.get("z_start") or 0))
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(segs):
        s = segs[i]
        z0 = float(s.get("z_start") or 0)
        z1 = float(s.get("z_end") or 0)
        span = max(0.0, z1 - z0)
        flagged = bool(s.get("flagged_short") or s.get("short") or False)
        if span >= min_span and not flagged:
            out.append(s)
            i += 1
            continue
        # merge short/flagged segment
        if out:
            # merge into previous
            prev = out.pop()
            merged = _merge_two_segments(prev, s)
            out.append(merged)
            i += 1
        else:
            # merge into next if possible
            if i + 1 < len(segs):
                merged = _merge_two_segments(s, segs[i + 1])
                segs[i + 1] = merged
            else:
                out.append(s)
            i += 1
    return out


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(pdf_path: Path | str) -> dict[str, Any]:
    """Run the single-call LLM pipeline on a PDF (extract + validate in one request).

    Returns
    -------
    dict with keys:
        pdf_text_length  -- number of chars extracted
        vision_mode      -- True when the agent used the drawing image (Gemini Vision)
        extracted        -- extracted specs (part dims, material, etc.)
        validation       -- self-validation report (recommendation, per-field confidence)
        code_issues      -- list of rule violations from code-level checks
        valid            -- True only when recommendation==ACCEPT and no code issues
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # --- Collect page images for Vision mode ---
    page_images = _collect_page_images(pdf_path)
    vision_mode = bool(page_images)
    logger.info(
        "[Pipeline] %s — vision_mode=%s (%d page image(s))",
        pdf_path.name, vision_mode, len(page_images),
    )

    # --- OCR text (always extracted; used as fallback and for context) ---
    logger.info("[Pipeline] Extracting OCR text from %s", pdf_path.name)
    try:
        pdf_text = _extract_pdf_text(pdf_path)
    except Exception as exc:
        logger.warning(
            "[Pipeline] Text extraction failed (%s) — proceeding in vision-only mode", exc
        )
        pdf_text = ""  # Vision mode will supply the context instead

    # --- Single merged agent: extract + validate in one LLM call ---
    logger.info(
        "[MergedAgent] Running — %d chars of text, vision=%s",
        len(pdf_text), vision_mode,
    )
    agent = MergedAgent()
    merged = agent.run(pdf_text, page_images=page_images or None)
    extracted  = merged.get("extracted", {})
    validation = merged.get("validation", {
        "recommendation": "REVIEW",
        "overall_confidence": 0.5,
        "fields": {},
        "cross_checks": ["Validation section missing from LLM response."],
    })
    logger.info(
        "[MergedAgent] Completed — recommendation=%s overall_confidence=%s",
        validation.get("recommendation"),
        validation.get("overall_confidence"),
    )

    # --- Geometry-derived override: prefer reliable geometry totals over noisy LLM length ---
    try:
      outputs_dir = pdf_path.parent.parent / "outputs"
      part_summary_file = outputs_dir / "part_summary.json"
      if part_summary_file.exists():
        try:
          ps_text = part_summary_file.read_text(encoding="utf-8")
          ps = json.loads(ps_text)
          # Optionally perform server-side cleaning/merge of short geometry segments
          try:
            if PDF_GEOM_MERGE_SHORT:
              segs_before = len(ps.get("segments") or [])
              cleaned = _clean_segments_list(ps.get("segments") or [], PDF_GEOM_MIN_SEG_SPAN)
              if cleaned and len(cleaned) != segs_before:
                ps["segments"] = cleaned
                # Recompute simple totals from cleaned segments
                try:
                  total_len = 0.0
                  max_od = 0.0
                  for sseg in cleaned:
                    z0 = float(sseg.get("z_start") or 0)
                    z1 = float(sseg.get("z_end") or 0)
                    total_len += max(0.0, z1 - z0)
                    odv = sseg.get("od_diameter")
                    if odv:
                      try:
                        max_od = max(max_od, float(odv))
                      except Exception:
                        pass
                  if "totals" not in ps:
                    ps["totals"] = {}
                  ps["totals"]["total_length_in"] = total_len
                  if max_od > 0:
                    ps["totals"]["max_od_in"] = max_od
                except Exception:
                  logger.debug("[Pipeline] recomputing totals after segment cleaning failed", exc_info=True)
                try:
                  part_summary_file.write_text(json.dumps(ps, indent=2), encoding="utf-8")
                  logger.info("[Pipeline] Wrote cleaned part_summary.json (merged short segments: %d -> %d)", segs_before, len(cleaned))
                except Exception:
                  logger.warning("[Pipeline] Failed to write cleaned part_summary.json", exc_info=True)
          except Exception:
            logger.debug("[Pipeline] segment cleaning failed", exc_info=True)
          geom_conf = ps.get("inference_metadata", {}).get("overall_confidence")
          if geom_conf is None:
            geom_conf = ps.get("scale_report", {}).get("confidence", 0)
          geom_len = None
          geom_max_od = None
          try:
            geom_len = ps.get("totals", {}).get("total_length_in")
          except Exception:
            geom_len = None
          try:
            geom_max_od = ps.get("totals", {}).get("max_od_in")
            if not geom_max_od:
              for _seg in ps.get("segments", []) or []:
                _od = _seg.get("od_diameter")
                if _od:
                  geom_max_od = max(float(geom_max_od or 0), float(_od))
          except Exception:
            geom_max_od = None

          # Narrow corrective override for a common LLM failure:
          # material/title stock DIA copied into finish OD.
          # Example: material="4.00 DIA 1018 CRS", LLM od_in=3.996, max_od_in=4.0,
          # but geometry profile shows true finish OD near 3.000.
          try:
            _material = str(extracted.get("material") or "").upper()
            _name = str(extracted.get("part_name") or "").upper()
            _tol_od = str(extracted.get("tolerance_od") or "")
            _llm_od = extracted.get("od_in")
            _llm_max_od = extracted.get("max_od_in")
            _stock_match = re.search(r'(\d+(?:\.\d+)?)\s*DIA\b', _material)
            _stock_dia = float(_stock_match.group(1)) if _stock_match else None
            _geom_conf = float(geom_conf or 0)
            if _geom_conf >= 0.75 and geom_max_od and _llm_od:
              _llm_od_f = float(_llm_od)
              _llm_max_od_f = float(_llm_max_od or 0)
              _geom_od_f = float(geom_max_od)
              _near_stock = (_stock_dia is not None and abs(_llm_od_f - _stock_dia) <= 0.02) or (
                _llm_max_od_f > 0 and abs(_llm_od_f - _llm_max_od_f) <= 0.02
              )
              _cap_like = any(tok in _name for tok in ("END CAP", "CAP", "PLUG", "FLANGE", "SPOOL"))
              _bar_like = "BAR" in _material or "COLD DRAWN" in _material or "CRS" in _material
              _tol_near_llm = False
              _m_tol = re.search(r'(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)', _tol_od)
              if _m_tol:
                try:
                  _tol_hi = max(float(_m_tol.group(1)), float(_m_tol.group(2)))
                  _tol_near_llm = abs(_tol_hi - _llm_od_f) <= 0.01
                except Exception:
                  _tol_near_llm = False
              _geom_smaller = _geom_od_f > 0 and (_llm_od_f - _geom_od_f) >= 0.20
              if (_near_stock or (_cap_like and _bar_like and _tol_near_llm)) and _geom_smaller:
                extracted["od_in"] = _geom_od_f
                if _stock_dia is not None and (_llm_max_od_f <= 0 or abs(_llm_max_od_f - _stock_dia) > 0.02):
                  extracted["max_od_in"] = _stock_dia
                cross = validation.get("cross_checks", []) or []
                cross.append(
                  f"od_in corrected from geometry/profile because LLM likely matched raw stock / title-block OD instead of finish profile: "
                  f"finish_od={_geom_od_f} in, raw_stock_od={float(extracted.get('max_od_in') or _llm_max_od_f or 0):.3f} in, geom_conf={_geom_conf:.2f}"
                )
                validation["cross_checks"] = cross
          except Exception:
            logger.debug("[Pipeline] stock-DIA -> od_in correction failed", exc_info=True)

          # Only fill length from geometry when LLM did NOT extract one.
          # Never override a value the LLM already found — geometry scale is unreliable.
          _llm_len = extracted.get("length_in")
          _llm_len_missing = not _llm_len or float(_llm_len) <= 0
          if geom_len and _llm_len_missing:
            logger.info(
              "[Pipeline] Filling missing length_in from geometry total_length_in=%s (geom_conf=%.2f)",
              geom_len, float(geom_conf or 0),
            )
            extracted["length_in"] = geom_len
            cross = validation.get("cross_checks", []) or []
            cross.append(
              f"length_in filled from geometry (LLM had no value): {geom_len} in, geom_conf={float(geom_conf or 0):.2f}"
            )
            validation["cross_checks"] = cross

          # Narrow corrective override for another common LLM failure:
          # centerline-to-face half-length returned as full OAL on symmetric parts.
          try:
            _llm_len = extracted.get("length_in")
            _name = str(extracted.get("part_name") or "").upper()
            _geom_conf = float(geom_conf or 0)
            if geom_len and _llm_len and _geom_conf >= 0.85:
              _llm_len_f = float(_llm_len)
              _geom_len_f = float(geom_len)
              _is_half = abs(_geom_len_f - (2.0 * _llm_len_f)) <= max(0.06, 0.03 * _geom_len_f)
              _symmetric_name = any(tok in _name for tok in ("CAP", "END CAP", "FLANGE", "PLUG", "SPOOL"))
              if _is_half and (_symmetric_name or _geom_len_f > _llm_len_f * 1.9):
                extracted["length_in"] = _geom_len_f
                cross = validation.get("cross_checks", []) or []
                cross.append(
                  f"length_in corrected from geometry/profile because LLM appears to have used centerline-to-face half-length: "
                  f"oal={_geom_len_f} in vs llm_half_length={_llm_len_f} in, geom_conf={_geom_conf:.2f}"
                )
                validation["cross_checks"] = cross
          except Exception:
            logger.debug("[Pipeline] half-length -> OAL correction failed", exc_info=True)

          # Only fill id_in from geometry when LLM did NOT extract one.
          try:
            if extracted.get("id_in") is None or float(extracted.get("id_in") or 0) <= 0:
              segs = ps.get("segments", [])
              best_seg = None
              best_score = 0.0
              for s in segs:
                id_d = s.get("id_diameter")
                if not id_d:
                  continue
                conf = float(s.get("confidence") or 0)
                span = float(s.get("z_end", 0) or 0) - float(s.get("z_start", 0) or 0)
                score = span * conf
                if score > best_score:
                  best_score = score
                  best_seg = s
              if best_seg and best_score > float(GEOM_SEG_SCORE_MIN):
                candidate_id = best_seg.get("id_diameter")
                try:
                  od = float(extracted.get("od_in") or 0)
                except Exception:
                  od = 0
                if candidate_id and (od == 0 or float(candidate_id) < od * float(ID_OD_RATIO_MAX)):
                  extracted["id_in"] = float(candidate_id)
                  cross = validation.get("cross_checks", []) or []
                  cross.append(
                    f"id_in filled from geometry segment (LLM had no value): id_diameter={candidate_id} in, seg_conf={best_seg.get('confidence')}"
                  )
                  validation["cross_checks"] = cross
          except Exception:
            logger.debug("[Pipeline] geometry->id_in fill failed", exc_info=True)

          # --- best-effort finish extraction from raw PDF text if missing ---
          try:
            if not extracted.get("finish"):
              # try broader finish patterns: explicit 'finish' labels, Ra values, or common keywords
              m = _FINISH_RE_1.search(pdf_text)
              if m:
                val = m.group(1).strip()
              else:
                m2 = _FINISH_RE_RA.search(pdf_text)
                if m2:
                  val = m2.group(1).strip()
                else:
                  m3 = _FINISH_RE_KEYWORD.search(pdf_text)
                  val = m3.group(1).strip() if m3 else None

              if val:
                extracted["finish"] = val
                cross = validation.get("cross_checks", []) or []
                cross.append(f"finish auto-filled from PDF text: {val}")
                validation["cross_checks"] = cross
          except Exception:
            logger.debug("[Pipeline] finish auto-fill failed", exc_info=True)
        except Exception as exc:
          logger.warning("[Pipeline] Failed to read part_summary for geometry override (%s)", exc)
    except Exception:
      # non-fatal: do not prevent pipeline return
      pass

    code_issues = _code_validate(extracted)
    try:
      _name = str(extracted.get("part_name") or "").upper()
      _material = str(extracted.get("material") or "").upper()
      _tol_od = str(extracted.get("tolerance_od") or "")
      _od = float(extracted.get("od_in") or 0)
      _max_od = float(extracted.get("max_od_in") or 0)
      _len = float(extracted.get("length_in") or 0)
      _cap_like = any(tok in _name for tok in ("END CAP", "CAP", "PLUG", "FLANGE", "SPOOL"))
      _bar_like = "BAR" in _material or "COLD DRAWN" in _material or "CRS" in _material
      _tol_hi = None
      _m_tol = re.search(r'(\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)', _tol_od)
      if _m_tol:
        try:
          _tol_hi = max(float(_m_tol.group(1)), float(_m_tol.group(2)))
        except Exception:
          _tol_hi = None
      _od_near_stock = _od > 0 and _max_od > 0 and abs(_od - _max_od) <= 0.02
      _tol_near_od = _tol_hi is not None and abs(_tol_hi - _od) <= 0.02
      _short_cap = _od > 0 and _len > 0 and (_len / _od) < 0.45
      if _cap_like and _bar_like and _od_near_stock and _tol_near_od and _short_cap:
        cross = validation.get("cross_checks", []) or []
        msg = (
          "Suspicious END CAP extraction: Finish OD matches stock/tolerance OD and length is unusually short "
          "for a cap-like part. Possible raw-stock OD confusion and/or centerline-to-face half-length error."
        )
        if msg not in cross:
          cross.append(msg)
        validation["cross_checks"] = cross
        validation["recommendation"] = "REVIEW"
        validation["overall_confidence"] = min(float(validation.get("overall_confidence") or 0.85), 0.45)
    except Exception:
      logger.debug("[Pipeline] suspicious END CAP review guard failed", exc_info=True)

    llm_recommendation = validation.get("recommendation", "REVIEW")
    valid = (llm_recommendation == "ACCEPT") and (len(code_issues) == 0)

    if code_issues:
        logger.warning("[Code-validate] Issues found: %s", code_issues)

    return {
        "pdf_text_length": len(pdf_text),
        "vision_mode": vision_mode,
        "extracted": extracted,
        "validation": validation,
        "code_issues": code_issues,
        "valid": valid,
    }
