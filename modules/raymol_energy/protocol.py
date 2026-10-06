"""Versioned, bounded wire messages shared by helper operations."""

import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_MESSAGE_BYTES = 262144


class BaseModelNoExtra(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class SceneAtom(BaseModelNoExtra):
    source_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=32)
    element: str = Field(min_length=1, max_length=2)
    residue: str = Field(min_length=1, max_length=160)
    resname: str = Field(min_length=1, max_length=8)
    chain: str = Field(max_length=32)
    resi: str = Field(min_length=1, max_length=32)
    xyz_nm: tuple[float, float, float]

    @field_validator("xyz_nm")
    @classmethod
    def finite_coordinates(cls, value):
        if not all(math.isfinite(x) for x in value):
            raise ValueError("Non-finite source coordinates")
        return value


class MolecularSnapshot(BaseModelNoExtra):
    version: Literal[1] = 1
    analysis_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    scene_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: int = Field(ge=1, strict=True)
    receptor: list[SceneAtom] = Field(min_length=1, max_length=10000)
    receptor_bonds: list[tuple[int, int]]
    receptor_bond_orders: list[int]
    protein_templates: dict[str, str]
    ligand: list[SceneAtom] = Field(min_length=1, max_length=100)
    ligand_sdf: str = Field(min_length=1, max_length=200000)
    # SDF atom index for every source ligand atom; covers all heavy atoms.
    ligand_map: list[int]
    exclusions: list[str]
    full_receptor_confirmed: Literal[True]
    chemistry_confirmed: Literal[True]
    preparation_recipe: Literal["template-h-v1", "openmm-ph-v1"] = "template-h-v1"
    preparation_ph: float | None = Field(default=None, ge=0, le=14, allow_inf_nan=False)
    protein_overrides: dict[str, str] = Field(default_factory=dict)
    ligand_smiles: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("full_receptor_confirmed", "chemistry_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True: raise ValueError("Explicit boolean confirmation required")
        return value

    @model_validator(mode="after")
    def check_identity(self):
        atoms = self.receptor + self.ligand
        ids = [a.source_id for a in atoms]
        if len(ids) != len(set(ids)):
            raise ValueError("Source atom IDs must be unique across partners")
        residues = {a.residue for a in self.receptor}
        if set(self.protein_templates) != residues:
            raise ValueError("Explicit ff14SB template required for every receptor residue")
        if not set(self.protein_overrides).issubset(residues):
            raise ValueError("Residue override does not belong to receptor")
        if self.preparation_recipe == "openmm-ph-v1" and self.preparation_ph is None:
            raise ValueError("Automatic protonation requires an explicit pH")
        if len(self.ligand_map) != len(self.ligand) or len(set(self.ligand_map)) != len(self.ligand):
            raise ValueError("Ligand source-to-SDF atom mapping must be a bijection")
        bonds = [tuple(sorted(b)) for b in self.receptor_bonds]
        if len(self.receptor_bond_orders) != len(bonds) or any(order not in {1, 2, 3, 4} for order in self.receptor_bond_orders):
            raise ValueError("Explicit supported receptor bond orders required")
        if len(set(bonds)) != len(bonds) or any(a < 0 or b >= len(self.receptor) or a == b for a, b in bonds):
            raise ValueError("Invalid or duplicate receptor bond")
        return self


class Message(BaseModelNoExtra):
    version: Literal[1] = 1
    generation: int = Field(gt=0, le=2**53 - 1, strict=True)
    request: int = Field(gt=0, le=2**53 - 1, strict=True)
    kind: Literal["run", "cancel", "started", "stage", "result", "cancelled", "error"]
    operation: Literal["inspect", "charge", "minimize", "tiles", "analyze", "release", "ligand_input"] | None = None
    payload: dict = Field(default_factory=dict)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Protocol version must be integer 1")
        return value

    @model_validator(mode="after")
    def check_operation(self):
        if (self.kind == "run") != (self.operation is not None):
            raise ValueError("Only run messages require an operation")
        return self
