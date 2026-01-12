"""Utility for generating human-readable explanations of turned part stacks."""

from typing import List, Dict, Optional


def generate_stack_explanation(
    segments: List[Dict],
    units: str = "in",
    overall_confidence: Optional[float] = None
) -> str:
    """Generate a human-readable explanation of the turned part stack.
    
    Args:
        segments: List of segment dictionaries with z_start, z_end, od_diameter, id_diameter, etc.
        units: Units string (default "in")
        overall_confidence: Optional overall confidence score
        
    Returns:
        Human-readable explanation string
    """
    if not segments:
        return "No segments found in this part."
    
    num_segments = len(segments)
    
    # Find longest section
    longest_length = 0
    longest_segment_idx = 0
    for i, seg in enumerate(segments):
        length = seg.get('z_end', 0) - seg.get('z_start', 0)
        if length > longest_length:
            longest_length = length
            longest_segment_idx = i
    
    # Detect thin-wall areas (wall thickness < 0.05 inches)
    thin_wall_segments = []
    for i, seg in enumerate(segments):
        wall_thickness = seg.get('wall_thickness')
        if wall_thickness is None:
            # Calculate from OD and ID
            od = seg.get('od_diameter', 0)
            id_dia = seg.get('id_diameter', 0)
            wall_thickness = (od - id_dia) / 2.0
        
        if wall_thickness > 0 and wall_thickness < 0.05:
            thin_wall_segments.append(i + 1)  # 1-indexed for user display
    
    # Detect internal bore presence
    bore_segments = []
    for i, seg in enumerate(segments):
        id_dia = seg.get('id_diameter', 0)
        if id_dia > 0:
            bore_segments.append(i + 1)  # 1-indexed
    
    # Detect low confidence segments
    low_confidence_segments = []
    if overall_confidence is not None:
        for i, seg in enumerate(segments):
            seg_conf = seg.get('confidence')
            if seg_conf is not None and seg_conf < 0.65:
                low_confidence_segments.append(i + 1)
    
    # Detect flagged segments
    flagged_segments = {}
    for i, seg in enumerate(segments):
        flags = seg.get('flags', [])
        if flags:
            for flag in flags:
                if flag not in flagged_segments:
                    flagged_segments[flag] = []
                flagged_segments[flag].append(i + 1)
    
    # Build explanation
    parts = []
    
    # Basic info
    segment_word = "section" if num_segments == 1 else "sections"
    parts.append(f"This part has {num_segments} turned {segment_word}.")
    
    # Longest section
    if longest_length > 0:
        parts.append(f"The longest section is {longest_length:.2f} {units} (section {longest_segment_idx + 1}).")
    
    # Thin-wall areas
    if thin_wall_segments:
        if len(thin_wall_segments) == 1:
            parts.append(f"One thin-wall area was detected in section {thin_wall_segments[0]} and needs review.")
        else:
            segment_list = format_segment_list(thin_wall_segments)
            parts.append(f"{len(thin_wall_segments)} thin-wall areas were detected in sections {segment_list} and need review.")
    
    # Internal bore
    if bore_segments:
        if len(bore_segments) == num_segments:
            parts.append("Internal bore is present throughout all sections.")
        elif len(bore_segments) == 1:
            parts.append(f"Internal bore appears only in section {bore_segments[0]}.")
        else:
            segment_list = format_segment_list(bore_segments)
            parts.append(f"Internal bore appears in sections {segment_list}.")
    else:
        parts.append("No internal bore detected (solid part).")
    
    # Low confidence
    if low_confidence_segments:
        if len(low_confidence_segments) == 1:
            parts.append(f"Section {low_confidence_segments[0]} has low confidence and may need manual review.")
        else:
            segment_list = format_segment_list(low_confidence_segments)
            parts.append(f"Sections {segment_list} have low confidence and may need manual review.")
    
    # Flags
    if flagged_segments:
        flag_descriptions = {
            'auto_merged': 'were automatically merged',
            'id_assumed_solid': 'had ID assumed as solid',
            'thin_wall': 'have thin walls',
            'short_segment': 'are short segments',
            'low_confidence': 'have low confidence',
            'boundary_snapped': 'had boundaries snapped'
        }
        
        for flag, seg_indices in flagged_segments.items():
            if len(seg_indices) == 1:
                desc = flag_descriptions.get(flag, flag.replace('_', ' '))
                # Use singular verb for single section
                if desc.startswith('have'):
                    desc = desc.replace('have', 'has', 1)
                parts.append(f"Section {seg_indices[0]} {desc}.")
            else:
                desc = flag_descriptions.get(flag, flag.replace('_', ' '))
                segment_list = format_segment_list(seg_indices)
                parts.append(f"Sections {segment_list} {desc}.")
    
    # Overall confidence
    if overall_confidence is not None:
        if overall_confidence >= 0.85:
            conf_level = "high"
        elif overall_confidence >= 0.65:
            conf_level = "medium"
        else:
            conf_level = "low"
        parts.append(f"Overall detection confidence is {conf_level} ({(overall_confidence * 100):.0f}%).")
    
    return " ".join(parts)


def format_segment_list(segments: List[int]) -> str:
    """Format a list of segment indices as a human-readable range.
    
    Examples:
        [1, 2, 3] -> "1-3"
        [1, 3, 5] -> "1, 3, and 5"
        [1, 2, 3, 5, 6] -> "1-3, 5-6"
    """
    if not segments:
        return ""
    
    if len(segments) == 1:
        return str(segments[0])
    
    # Sort segments
    sorted_segments = sorted(segments)
    
    # Group consecutive segments
    ranges = []
    start = sorted_segments[0]
    end = sorted_segments[0]
    
    for i in range(1, len(sorted_segments)):
        if sorted_segments[i] == end + 1:
            # Consecutive, extend range
            end = sorted_segments[i]
        else:
            # Gap found, save current range
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = sorted_segments[i]
            end = sorted_segments[i]
    
    # Add final range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")
    
    # Format with "and" before last item if multiple ranges
    if len(ranges) == 1:
        return ranges[0]
    elif len(ranges) == 2:
        return f"{ranges[0]} and {ranges[1]}"
    else:
        return ", ".join(ranges[:-1]) + f", and {ranges[-1]}"

