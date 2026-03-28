"""
Test script for part number 050CE0004 dimension extraction.
Compares extracted values with expected values from the drawing.
"""

import sys
import os
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.services.pdf_spec_extractor import PDFSpecExtractor

def test_part_050ce0004():
    """Test extraction for part 050CE0004."""
    
    print("=" * 80)
    print("TESTING PART NUMBER 050CE0004 DIMENSION EXTRACTION")
    print("=" * 80)
    print()
    
    # Expected values from the drawing (based on image description)
    expected_values = {
        "part_no": "050CE0004",
        "finish_od_in": 1.240,  # Ø1.240 [31.496]
        "finish_id_in": 1.007,  # Ø1.006-1.008 [25.553-25.603] - using max value
        "finish_len_in": 0.1875,  # .185-.190 [4.699-4.826] - using average
    }
    
    extractor = PDFSpecExtractor()
    
    # Try to find PDF file
    pdf_paths = [
        Path("data/pdfs/050ce0004.pdf"),
        Path("data/pdfs/050CE0004.pdf"),
        Path("../data/pdfs/050ce0004.pdf"),
        Path("../data/pdfs/050CE0004.pdf"),
    ]
    
    pdf_path = None
    for path in pdf_paths:
        full_path = backend_dir / path
        if full_path.exists():
            pdf_path = str(full_path)
            break
    
    if not pdf_path:
        print("⚠️  PDF file not found. Please provide the path to the PDF file.")
        print("\nTried paths:")
        for path in pdf_paths:
            print(f"  - {backend_dir / path}")
        print("\nYou can test with a PDF path:")
        print("  python test_part_050ce0004.py <path_to_pdf>")
        return
    
    print(f"📄 PDF File: {pdf_path}")
    print()
    
    # Extract specifications
    print("🔍 Extracting specifications from PDF...")
    result = extractor.extract_from_file(pdf_path)
    
    if not result["success"]:
        print(f"❌ Extraction failed: {result.get('error', 'Unknown error')}")
        return
    
    specs = result.get("extracted_specs", {})
    
    print("\n" + "=" * 80)
    print("EXTRACTION RESULTS")
    print("=" * 80)
    print()
    
    # Compare extracted vs expected
    print("📊 Dimension Comparison:")
    print("-" * 80)
    
    comparisons = [
        ("Part Number", "part_no", None),
        ("Finish OD (in)", "finish_od_in", expected_values["finish_od_in"]),
        ("Finish ID (in)", "finish_id_in", expected_values["finish_id_in"]),
        ("Finish Length (in)", "finish_len_in", expected_values["finish_len_in"]),
    ]
    
    all_match = True
    for label, key, expected in comparisons:
        extracted = specs.get(key)
        if expected is None:
            # Just show extracted value
            status = "✅" if extracted else "❌"
            print(f"{status} {label:25s}: {extracted}")
        else:
            if extracted is None:
                status = "❌"
                match = False
                diff = "N/A"
            else:
                diff = abs(extracted - expected)
                tolerance = 0.001  # Allow 0.001 inch tolerance
                match = diff <= tolerance
                status = "✅" if match else "❌"
                diff = f"{diff:.4f}"
            
            if not match:
                all_match = False
            
            print(f"{status} {label:25s}: Expected={expected:.4f}, Extracted={extracted}, Diff={diff}")
    
    print("-" * 80)
    print()
    
    # Show all extracted specs
    print("📋 All Extracted Specifications:")
    print("-" * 80)
    for key, value in specs.items():
        if key != "confidence":
            print(f"  {key:25s}: {value}")
    
    print()
    print("📊 Confidence Scores:")
    print("-" * 80)
    confidence = specs.get("confidence", {})
    for key, value in confidence.items():
        print(f"  {key:25s}: {value:.2f}")
    
    print()
    print("=" * 80)
    if all_match:
        print("✅ ALL DIMENSIONS MATCH EXPECTED VALUES!")
    else:
        print("❌ SOME DIMENSIONS DO NOT MATCH - PATTERNS NEED REFINEMENT")
    print("=" * 80)
    
    # Show raw text preview for debugging
    if "raw_text_preview" in result:
        print("\n📝 Raw Text Preview (first 1000 chars):")
        print("-" * 80)
        print(result["raw_text_preview"][:1000])
        print("...")
        print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Use provided PDF path
        pdf_path = sys.argv[1]
        extractor = PDFSpecExtractor()
        result = extractor.extract_from_file(pdf_path)
        
        if result["success"]:
            specs = result.get("extracted_specs", {})
            print("\nExtracted Specifications:")
            for key, value in specs.items():
                print(f"  {key}: {value}")
        else:
            print(f"Error: {result.get('error')}")
    else:
        test_part_050ce0004()
