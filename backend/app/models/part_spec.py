"""Conservative, additive contract for the generic part pipeline (v0.1)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.models.document_registry import DocumentAssociation


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceDocument(ContractModel):
    document_id: str
    path: str
    sha256: str
    role: Literal["unclassified", "legacy_summary"] = "unclassified"
    revision: str | None = None
    association: DocumentAssociation | None = None


class Evidence(ContractModel):
    document_id: str
    json_pointer: str
    method: Literal["legacy_adapter"] = "legacy_adapter"


class DimensionCandidate(ContractModel):
    name: Literal["maximum_outer_diameter", "axial_length"]
    value: float = Field(gt=0)
    unit: Literal["mm"] = "mm"
    source_value: float = Field(gt=0)
    source_unit: Literal["in", "mm"]
    meaning: Literal["legacy_model_extent"] = "legacy_model_extent"
    acceptance: Literal["unverified"] = "unverified"
    evidence: list[Evidence] = Field(min_length=1)


class Readiness(ContractModel):
    facts: Literal["unresolved", "needs_review"] = "unresolved"
    measurements: Literal["unresolved", "needs_review"] = "unresolved"
    geometry: Literal["not_validated"] = "not_validated"
    quote: Literal["not_validated"] = "not_validated"


class ReviewIssue(ContractModel):
    code: str
    message: str


class PartSpec(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    adapter_version: Literal["legacy-summary-v1"] = "legacy-summary-v1"
    job_id: str
    # A job may contain many parts. Never invent a part/revision/body association.
    part_id: str | None = None
    revision: str | None = None
    manufacturing_state: Literal["unresolved"] = "unresolved"
    selected_body_ids: list[str] = Field(default_factory=list)
    # Content identity of this response, not a persisted correction-history version.
    snapshot_id: str = ""
    registry_version: int = 0
    sources: list[SourceDocument] = Field(default_factory=list)
    dimensions: list[DimensionCandidate] = Field(default_factory=list)
    readiness: Readiness = Field(default_factory=Readiness)
    issues: list[ReviewIssue] = Field(default_factory=list)
