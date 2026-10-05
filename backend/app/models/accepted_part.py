"""Allowlisted, source-reviewed geometry recipes. All coordinates are millimetres."""
import math
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0, le=10000)]
Coordinate = Annotated[float, Field(ge=-10000, le=10000)]
Vector = tuple[Coordinate, Coordinate, Coordinate]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Cylinder(StrictModel):
    kind: Literal["cylinder"]
    diameter: Positive
    length: Positive


class Box(StrictModel):
    kind: Literal["box"]
    width: Positive
    height: Positive
    length: Positive


class Revolve(StrictModel):
    kind: Literal["revolve"]
    # Closed radial/axial section, in its drawing coordinate frame.
    profile: list[tuple[Annotated[float, Field(ge=0, le=10000)], Coordinate]] = Field(min_length=3, max_length=200)


class ImportedStep(StrictModel):
    kind: Literal["step"]
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_index: int = Field(ge=0, le=1000)


Base = Annotated[Union[Cylinder, Box, Revolve, ImportedStep], Field(discriminator="kind")]


class Positioned(StrictModel):
    feature_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    origin: Vector
    axis: Vector = (0, 0, 1)

    @model_validator(mode="after")
    def unit_axis(self):
        if not math.isclose(sum(x*x for x in self.axis), 1, abs_tol=1e-6):
            raise ValueError("Feature axis must be a unit vector")
        return self


class Hole(Positioned):
    kind: Literal["hole"]
    diameter: Positive
    depth: Positive
    termination: Literal["through", "blind"]


class Pocket(Positioned):
    kind: Literal["pocket"]
    width: Positive
    height: Positive
    depth: Positive


class ConeCut(Positioned):
    kind: Literal["cone_cut"]
    entry_diameter: Positive
    end_diameter: Positive
    depth: Positive


class Thread(Positioned):
    kind: Literal["thread"]
    side: Literal["internal", "external"]
    major_diameter: Positive
    minor_diameter: Positive
    pitch: Positive
    length: Positive
    handedness: Literal["right", "left"] = "right"
    callout: str = Field(min_length=1, max_length=100)
    # Explicit representative groove width. No invented standards table/fit claim.
    groove_width: Positive

    @model_validator(mode="after")
    def valid_profile(self):
        if self.minor_diameter >= self.major_diameter:
            raise ValueError("Thread minor diameter must be below major diameter")
        if self.groove_width >= self.pitch:
            raise ValueError("Groove width must be smaller than pitch")
        if self.length / self.pitch > 100:
            raise ValueError("Detailed thread is limited to 100 turns")
        return self


Feature = Annotated[Union[Hole, Pocket, ConeCut, Thread], Field(discriminator="kind")]


class EvidenceNote(StrictModel):
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int | None = Field(default=None, ge=1)
    locator: str = Field(min_length=1, max_length=500)


class AcceptedPartRequest(StrictModel):
    expected_version: int = Field(ge=0)
    expected_registry_version: int = Field(ge=0)
    part_number: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=100)
    governing_document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: list[EvidenceNote] = Field(min_length=1, max_length=200)
    base: Base
    features: list[Feature] = Field(default_factory=list, max_length=100)
    review_note: str = Field(min_length=1, max_length=2000)
    completeness: Literal["complete", "partial"]
    unresolved: list[str] = Field(default_factory=list, max_length=100)
    material: str | None = Field(default=None, max_length=200)
    acceptance_origin: Literal["human_review", "automatic_drawing", "source_cad"] = "human_review"
    dimension_checks: list[dict] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def review_is_consistent(self):
        from app.models.automatic import DimensionCheck
        for check in self.dimension_checks:
            parsed = DimensionCheck(**check)
            if parsed.lower > parsed.upper:
                raise ValueError("Dimension limits are reversed")
        if self.acceptance_origin == "source_cad" and self.base.kind != "step":
            raise ValueError("Source CAD acceptance requires an imported STEP solid")
        if self.acceptance_origin == "automatic_drawing" and (not self.dimension_checks or self.base.kind == "step"):
            raise ValueError("Automatic drawings require measured dimension checks and cannot import STEP")
        ids = [f.feature_id for f in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("Feature IDs must be unique")
        if self.completeness == "partial" and not self.unresolved:
            raise ValueError("Partial geometry must describe unresolved features")
        if self.completeness == "complete" and self.unresolved:
            raise ValueError("Unresolved features prevent a complete declaration")
        if not any(e.document_id == self.governing_document_id for e in self.evidence):
            raise ValueError("Evidence must include the governing source")
        if self.base.kind == "step" and self.base.document_id != self.governing_document_id:
            raise ValueError("Imported STEP must be the governing source")
        return self
