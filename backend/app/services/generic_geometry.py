"""Deterministic CAD construction from accepted, allowlisted recipes.

Run in the isolated geometry worker environment, never evaluate generated code.
"""
import hashlib
import json
import math
from pathlib import Path

from app.models.accepted_part import AcceptedPartRequest


def build_geometry(spec, registry, output: Path):
    import cadquery as cq
    import trimesh
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    payload = spec["payload"]
    recipe = AcceptedPartRequest(**payload, expected_version=spec["version"],
                                 expected_registry_version=spec["registry_version"])
    base = recipe.base
    if base.kind == "cylinder":
        shape = cq.Solid.makeCylinder(base.diameter/2, base.length)
    elif base.kind == "box":
        shape = cq.Solid.makeBox(base.width, base.height, base.length,
                                cq.Vector(-base.width/2, -base.height/2, 0))
    elif base.kind == "revolve":
        wire = cq.Wire.makePolygon([(r, 0, z) for r, z in base.profile], close=True)
        shape = cq.Solid.revolve(wire, [], 360, (0, 0, 0), (0, 0, 1))
    else:
        document = next(d for d in registry.read()["documents"] if d["document_id"] == base.document_id)
        source = registry.root / "blobs" / document["sha256"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError("Retained STEP source failed its integrity check")
        solids = cq.importers.importStep(str(source)).solids().vals()
        if base.body_index >= len(solids):
            raise ValueError(f"STEP body index is out of range; source has {len(solids)} solids")
        shape = solids[base.body_index]

    def validate(s):
        if not s.isValid() or len(s.Solids()) != 1 or s.Volume() <= 1e-9:
            raise ValueError("Construction did not produce one valid positive-volume solid")
        return s.Solids()[0]

    shape = validate(shape)
    feature_results = []
    warnings = list(recipe.unresolved)
    eps = 1e-4
    for feature in recipe.features:
        before = shape.Volume()
        location = cq.Plane(origin=feature.origin, normal=feature.axis).location
        if feature.kind == "hole":
            if feature.termination == "through":
                entry = tuple(feature.origin[i] - eps*feature.axis[i] for i in range(3))
                exit_ = tuple(feature.origin[i] + (feature.depth+eps)*feature.axis[i] for i in range(3))
                if shape.isInside(entry) or shape.isInside(exit_):
                    raise ValueError(f"{feature.feature_id}: through-hole endpoints must clear the part")
            cutter = cq.Solid.makeCylinder(feature.diameter/2, feature.depth+eps,
                                           cq.Vector(0, 0, -eps)).moved(location)
        elif feature.kind == "pocket":
            cutter = cq.Solid.makeBox(feature.width, feature.height, feature.depth+eps,
                                     cq.Vector(-feature.width/2, -feature.height/2, -eps)).moved(location)
        elif feature.kind == "cone_cut":
            cutter = cq.Solid.makeCone(feature.entry_diameter/2, feature.end_diameter/2,
                                      feature.depth).moved(location)
        else:
            # Cut an explicit triangular groove. This is not an assertion of fit-class compliance.
            radius = feature.minor_diameter/2 if feature.side == "internal" else feature.major_diameter/2
            outer = feature.major_diameter/2 if feature.side == "internal" else feature.minor_diameter/2
            root = radius-eps if feature.side == "internal" else radius+eps
            profile = cq.Wire.makePolygon([(root, 0, -feature.groove_width/2),
                                           (outer, 0, 0), (root, 0, feature.groove_width/2)], close=True)
            helix = cq.Wire.makeHelix(feature.pitch, feature.length, radius,
                                      lefthand=feature.handedness == "left")
            cutter = cq.Solid.sweep(profile, [], helix, makeSolid=True, isFrenet=True)
            cutter = cutter.intersect(cq.Solid.makeCylinder(feature.major_diameter/2+eps*2, feature.length))
            if feature.side == "internal":
                # The bore must already exist. Threading never guesses its depth/termination.
                for z in (eps, feature.length/2, feature.length-eps):
                    point = tuple(feature.origin[i]+z*feature.axis[i] for i in range(3))
                    if shape.isInside(point):
                        raise ValueError(f"{feature.feature_id}: model the bore before its internal thread")
            cutter = cutter.moved(location)
            warnings.append(f"{feature.feature_id}: representative helical groove for {feature.callout}; fit limits and runout are not certified")
        shape = validate(shape.cut(cutter))
        removed = before-shape.Volume()
        if removed <= max(1e-8, before*1e-12):
            raise ValueError(f"{feature.feature_id}: feature removed no material; check placement and dimensions")
        if feature.kind == "hole":
            for z in (eps, feature.depth/2, feature.depth-eps):
                point = tuple(feature.origin[i]+z*feature.axis[i] for i in range(3))
                if shape.isInside(point):
                    raise ValueError(f"{feature.feature_id}: bore connectivity check failed")
            if feature.termination == "blind":
                bottom = tuple(feature.origin[i]+(feature.depth+eps)*feature.axis[i] for i in range(3))
                if not shape.isInside(bottom):
                    raise ValueError(f"{feature.feature_id}: blind hole has no material behind its bottom")
        feature_results.append({"feature_id": feature.feature_id, "kind": feature.kind,
                                "removed_volume_mm3": removed, "status": "constructed"})

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    od = None
    bore_stations = []
    has_internal_non_cylindrical_surface = False
    for face in shape.Faces():
        if face.geomType() not in ("CYLINDER", "CONE"):
            continue
        u0, u1, v0, v1 = face._uvBounds()
        normal, point = face.normalAt((u0+u1)/2, (v0+v1)/2)
        if point.x*normal.x+point.y*normal.y >= 0:
            continue
        if face.geomType() == "CONE":
            # Tapered internal minima need a separate conical measurement contract.
            has_internal_non_cylindrical_surface = True
            continue
        cylinder = face._geomAdaptor().Cylinder()
        axis = cylinder.Axis()
        if abs(axis.Direction().Z()) < 1-1e-8 or math.hypot(axis.Location().X(), axis.Location().Y()) > 1e-6:
            continue
        bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(face.wrapped, bounds, False, False)
        extents = bounds.Get()
        bore_stations.append({"diameter_mm": cylinder.Radius()*2, "z_start_mm": extents[2], "z_end_mm": extents[5]})
    through_id = None
    if bore_stations and not has_internal_non_cylindrical_surface:
        axis_line = cq.Edge.makeLine((0,0,zmin-eps),(0,0,zmax+eps))
        obstruction = shape.intersect(axis_line)
        intervals = sorted((s["z_start_mm"],s["z_end_mm"]) for s in bore_stations)
        end = zmin
        for start, stop in intervals:
            if start > end+1e-6:
                break
            end = max(end,stop)
        if not obstruction.Edges() and end >= zmax-1e-6:
            through_id = min(s["diameter_mm"] for s in bore_stations)
    if base.kind in ("cylinder", "revolve"):
        diameters = []
        for face in shape.Faces():
            if face.geomType() != "CYLINDER":
                continue
            cylinder = face._geomAdaptor().Cylinder()
            axis = cylinder.Axis()
            if abs(axis.Direction().Z()) < 1-1e-8 or math.hypot(axis.Location().X(), axis.Location().Y()) > 1e-6:
                continue
            u0, u1, v0, v1 = face._uvBounds()
            normal, point = face.normalAt((u0+u1)/2, (v0+v1)/2)
            if point.x*normal.x + point.y*normal.y > 0:
                diameters.append(cylinder.Radius()*2)
        od = max(diameters) if diameters else None
    measurements = {"envelope_width_mm": xmax-xmin, "envelope_height_mm": ymax-ymin,
                    "axial_length_mm": zmax-zmin, "maximum_outer_cylindrical_diameter_mm": od,
                    "minimum_coaxial_through_bore_mm": through_id, "coaxial_bore_stations": bore_stations,
                    "volume_mm3": shape.Volume(), "frame": "accepted source XYZ; axial direction Z",
                    "method": "BRepBndLib.AddOptimal without triangulation; outward coaxial cylinder surfaces for OD"}
    for check in recipe.dimension_checks:
        value = measurements.get(check['metric'])
        if value is None or not check['lower']-1e-5 <= value <= check['upper']+1e-5:
            raise ValueError(f"Drawing dimension check failed: {check['metric']} measured {value}, expected [{check['lower']}, {check['upper']}]")
    if recipe.acceptance_origin != 'human_review':
        warnings.append('Automatically constructed; no human engineering review or thread fit certification')
    if recipe.acceptance_origin == 'source_cad':
        warnings.append('Source CAD identity and manufacturing state are not independently verified; XYZ frame is retained')
    output.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape, str(output / "model.step"))
    vertices, triangles = shape.tessellate(0.02, 0.1)
    mesh = trimesh.Trimesh(vertices=[(v.x/1000, v.y/1000, v.z/1000) for v in vertices], faces=triangles, process=False)
    mesh.export(str(output / "model.glb"))  # glTF metres; measurement values above remain mm.
    manifest = {"spec_id": spec["spec_id"], "part_key": spec["part_key"], "version": spec["version"],
                "registry_version": spec["registry_version"], "engine": f"cadquery-{cq.__version__}",
                "status": "constructed_from_reviewed_recipe" if recipe.acceptance_origin == "human_review" else "constructed_automatically",
                "acceptance_origin": recipe.acceptance_origin, "dimension_checks": recipe.dimension_checks, "completeness": recipe.completeness,
                "independent_drawing_validation": "not_performed", "measurements": measurements,
                "features": feature_results, "warnings": warnings, "mesh_unit": "m",
                "mesh_linear_tolerance_mm": 0.02, "quote_status": "dimensions_only"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    export_review_workbook(output / "rfq_review.xlsx", spec, manifest)
    manifest["artifacts"] = {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
                             for name in ("model.step", "model.glb", "rfq_review.xlsx")}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def export_review_workbook(path, spec, manifest):
    from openpyxl import Workbook
    wb = Workbook()
    sheet = wb.active
    sheet.title = "RFQ dimensions"
    p = spec["payload"]
    for row in [("Field", "Value", "Unit / meaning"), ("Part number", p["part_number"], "Reviewed identity"),
                ("Revision", p["revision"], "Governing source revision"),
                ("Specification", spec["spec_id"], "Versioned specification ID"),
                ("Completeness", p["completeness"], "Recipe declaration"),
                ("Acceptance origin", p.get("acceptance_origin", "human_review"), "Automatic does not imply human review"),
                ("Material", p.get("material"), "Reviewed text; pricing not calculated")]:
        sheet.append(row)
    for key, value in manifest["measurements"].items():
        if key.endswith("_mm"):
            sheet.append((key, value, "mm; nominal model geometry"))
            sheet.append((key.replace("_mm", "_in"), value/25.4 if value is not None else None, "in; nominal model geometry"))
    sheet.append(("volume_mm3", manifest["measurements"]["volume_mm3"], "Nominal volume; partial models exclude unresolved geometry"))
    notes = wb.create_sheet("Evidence and limitations")
    notes.append(("Document ID", "Page", "Locator"))
    for evidence in p["evidence"]:
        notes.append((evidence["document_id"], evidence.get("page"), evidence["locator"]))
    notes.append(("Review note", p["review_note"]))
    notes.append(("Validation", "Constructed from versioned recipe; independent engineering validation not performed"))
    for warning in manifest["warnings"]:
        notes.append(("Limitation", warning))
    notes.append(("Costing", "No price, material density or manufacturing allowance inferred"))
    for ws in wb:
        ws.freeze_panes = "A2"
        for col in ("A", "B", "C"):
            ws.column_dimensions[col].width = 42
        # Untrusted drawing text must never become an Excel formula.
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    wb.save(path)
