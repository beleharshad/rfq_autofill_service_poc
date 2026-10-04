"""Explicit document associations; never inferred from filenames."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    part_number: str = Field(min_length=1, max_length=200)
    revision: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["finished_drawing", "process_drawing", "cad", "quote"]
    manufacturing_state: Literal["finished", "raw", "intermediate", "unresolved"]

    @model_validator(mode="after")
    def check_state(self):
        if self.role == "finished_drawing" and self.manufacturing_state != "finished":
            raise ValueError("A finished drawing must have finished manufacturing state")
        return self


class AssociationRequest(DocumentAssociation):
    expected_version: int = Field(ge=0)
