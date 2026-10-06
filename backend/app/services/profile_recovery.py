"""Focused visual recovery of incomplete turned profiles; no part-name rules."""
import io
import math
from PIL import Image
from app.services.turned_profile_validation import validate_turned_profile

PROMPT = '''Reconstruct the rotational geometry from the ORIGINAL drawing images.
The previous extraction failed; do not assume its OD/ID assignments are correct.
Images are full pages followed by overlapping enlarged tiles of the same pages.
Tiles are NOT different parts or extra repeated features.
First trace the exterior silhouette in the longitudinal view, then trace any
internal surfaces. Associate each dimension's witness lines with those surfaces.
Concentric circles in an end view can represent external shoulders. A diameter
limit pair is ONE toleranced surface; 2X means two occurrences, not two holes.
Positive evidence for a bore must come from internal edges, section hatching,
hidden lines or an explicit bore/hole callout, not simply a small diameter.
Do not treat missing evidence as proof of solidity: use unknown if ambiguous.
Do not use drawing scale, pixels, title/name heuristics, or raw stock to invent
finished dimensions. Derive shoulder positions only from dimension chains.
Return JSON:
{"bore_type":"solid|through|blind|stepped|unknown", "length_in":number,
 "od_in":number, "id_in":number_or_null, "max_id_in":number_or_null,
 "axial_profile":[{"z_start":number,"z_end":number,
 "od_diameter":number,"id_diameter":number}],
 "evidence":["Explain each axial interval, dimension callout, and external/internal classification"],
 "unresolved":["Any ambiguity"]}
Use inches, z=0 at one end, contiguous intervals to overall length, zero ID for
solid intervals. Use the midpoint of diameter limits for the preview, retaining
the original limits in evidence. Preserve blind-hole bottoms and all shoulders.
Return an empty profile and unresolved reasons if a complete profile is not
supported. No markdown. Do not add fillets or chamfers as guessed segments.
'''


def detail_images(images):
    result = []
    # Bound image count and size. Original context precedes its four detail tiles.
    for raw in images[:3]:
        with Image.open(io.BytesIO(raw)) as original:
            page = original.convert('RGB')
            w, h = page.size
            boxes = [(0, 0, w, h)]
            boxes += [(int(x*w), int(y*h), int((x+.6)*w), int((y+.6)*h))
                      for y in (0, .4) for x in (0, .4)]
            for box in boxes:
                tile = page.crop(box)
                tile.thumbnail((1800, 1800))
                buffer = io.BytesIO()
                tile.save(buffer, format='PNG')
                result.append(buffer.getvalue())
    return result


def recover_profile(extracted, images, generate, parse):
    """Return a checked candidate separately; never mutate the original on failure."""
    if not images or len(images) > 3:
        return None
    raw = generate(PROMPT, detail_images(images), temperature=0.0, max_output_tokens=4096)
    candidate = parse(raw)
    if not isinstance(candidate, dict) or candidate.get('unresolved') != []:
        return None
    if any(k not in candidate for k in ('bore_type', 'length_in', 'od_in', 'id_in', 'max_id_in', 'axial_profile')):
        return None
    if candidate.get('bore_type') not in ('solid', 'through', 'blind', 'stepped'):
        return None
    evidence = candidate.get('evidence')
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) and e.strip() for e in evidence):
        return None
    if not candidate.get('axial_profile') or validate_turned_profile(candidate):
        return None
    segments = candidate['axial_profile']
    max_od = max(s['od_diameter'] for s in segments)
    if not isinstance(candidate.get('od_in'), (int, float)) or not math.isfinite(candidate['od_in']) or abs(max_od - candidate['od_in']) > .001:
        return None
    bores = [s['id_diameter'] for s in segments if s['id_diameter'] > 0]
    if candidate['bore_type'] != 'solid':
        if not bores:
            return None
        for field, expected in (('id_in', min(bores)), ('max_id_in', max(bores))):
            value = candidate[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value - expected) > .001:
                return None
    return candidate
