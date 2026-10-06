"""One immutable parameter authority; derived systems never re-perceive chemistry."""

import json
from pathlib import Path
import sysconfig
from typing import ClassVar
from types import MappingProxyType
from collections.abc import Mapping

from .artifacts import Artifacts
from .protocol import BaseModelNoExtra


class SolventProfile(BaseModelNoExtra):
    path: ClassVar[Path] = Path(__file__).with_name("scientific-profile-v1.json")

    @classmethod
    def load(cls):
        profile = json.loads(cls.path.read_text())
        site = Path(sysconfig.get_path("purelib"))
        for path, expected in profile["assets"].items():
            if Artifacts.digest((site / path).read_bytes()) != expected:
                raise ValueError(f"Scientific asset changed: {path}")
        for source in profile["sources"]:
            if Artifacts.digest((site / source["path"]).read_bytes()) != source["sha256"]:
                raise ValueError(f"Scientific implementation changed: {source['path']}")
        return profile

    @classmethod
    def parameters(cls, topology):
        profile = cls.load()
        neighbors = {atom: [] for atom in topology.atoms()}
        for a, b in topology.bonds():
            neighbors[a].append(b)
            neighbors[b].append(a)
        result = []
        for atom in topology.atoms():
            symbol = atom.element.symbol
            if symbol not in profile["elements"]:
                raise ValueError(f"Unsupported OBC2 element {symbol}")
            rule = profile["elements"][symbol]
            radius = rule["radius_angstrom"]
            if symbol == "H":
                if len(neighbors[atom]) != 1:
                    raise ValueError("Hydrogen must have one bonded parent")
                if neighbors[atom][0].element.symbol == "N":
                    radius = rule["bonded_to_N_radius_angstrom"]
            result.append((radius / 10, rule["screen"]))
        return result

    @classmethod
    def assert_system(cls, topology, gb):
        profile = cls.load()
        parameters = cls.parameters(topology)
        if gb.getNumParticles() != len(parameters):
            raise ValueError("Solvent particle count mismatch")
        for index, (radius, screen) in enumerate(parameters):
            q, observed_radius, observed_screen = gb.getParticleParameters(index)
            expected = [q, radius - profile["model"]["offset_nm"], screen * (radius - profile["model"]["offset_nm"])]
            if [q, observed_radius, observed_screen] != expected:
                raise ValueError(f"Solvent profile mismatch at particle {index}")
        return {"id": profile["id"], "version": profile["version"], "sha256": Artifacts.digest(cls.path.read_bytes()), "explicit_particles_checked": len(parameters)}


class PreparedModel(BaseModelNoExtra):
    model_config = {"extra": "forbid", "arbitrary_types_allowed": True, "frozen": True}
    system_xml: str
    particles: tuple[dict, ...]
    terms: tuple[dict, ...]
    bonds: tuple[dict, ...]
    nprotein: int
    key: str
    provenance: dict

    @staticmethod
    def freeze(value):
        if isinstance(value, Mapping): return MappingProxyType({k: PreparedModel.freeze(v) for k, v in value.items()})
        if isinstance(value, (tuple, list)): return tuple(PreparedModel.freeze(v) for v in value)
        return value

    def model_post_init(self, context):
        for field in ("particles", "terms", "bonds", "provenance"):
            object.__setattr__(self, field, self.freeze(getattr(self, field)))

    @classmethod
    def extract(cls, system, topology, particles, nprotein, labels, provenance, bonds):
        import numpy as np
        import openmm as mm
        from openmm import unit
        if system.getNumConstraints() or any(system.isVirtualSite(i) for i in range(system.getNumParticles())):
            raise ValueError("Constraints/virtual sites outside profile")
        terms, records = [], [dict(p) for p in particles]
        kinds = {mm.HarmonicBondForce: ("Bond", 2), mm.HarmonicAngleForce: ("Angle", 3), mm.PeriodicTorsionForce: ("Torsion", 4)}
        inventory = []
        for force_index, force in enumerate(system.getForces()):
            inventory.append(type(force).__name__)
            if isinstance(force, mm.NonbondedForce):
                if force.getNonbondedMethod() != force.NoCutoff or force.getUseSwitchingFunction() or force.getUseDispersionCorrection() or force.getNumParticleParameterOffsets() or force.getNumExceptionParameterOffsets():
                    raise ValueError("Nonbonded settings outside profile")
                for i in range(force.getNumParticles()):
                    q, sigma, epsilon = force.getParticleParameters(i)
                    records[i].update(q=q.value_in_unit(unit.elementary_charge), sigma_nm=sigma.value_in_unit(unit.nanometer), epsilon_kj=epsilon.value_in_unit(unit.kilojoule_per_mole), mass_da=system.getParticleMass(i).value_in_unit(unit.dalton))
                for i in range(force.getNumExceptions()):
                    a, b, q, sigma, epsilon = force.getExceptionParameters(i)
                    if (a < nprotein) != (b < nprotein):
                        raise ValueError("Cross-partner nonbonded exception")
                    terms.append({"kind": "exception", "force": force_index, "term": i, "atoms": [a, b], "parameters": [q.value_in_unit(unit.elementary_charge**2), sigma.value_in_unit(unit.nanometer), epsilon.value_in_unit(unit.kilojoule_per_mole)]})
            elif isinstance(force, mm.CustomGBForce):
                if force.getNonbondedMethod() != force.NoCutoff or force.getNumExclusions() or force.getNumTabulatedFunctions():
                    raise ValueError("Solvent settings outside profile")
                SolventProfile.assert_system(topology, force)
                for i in range(force.getNumParticles()):
                    records[i]["gb"] = list(force.getParticleParameters(i))
            elif type(force) in kinds:
                kind, arity = kinds[type(force)]
                for i in range(getattr(force, "getNum" + kind + "s")()):
                    values = getattr(force, "get" + kind + "Parameters")(i)
                    atoms = list(values[:arity])
                    if len({a < nprotein for a in atoms}) != 1:
                        raise ValueError("Cross-partner bonded term")
                    parameters = [v.value_in_unit_system(unit.md_unit_system) if unit.is_quantity(v) else v for v in values[arity:]]
                    record = {"kind": kind.lower(), "force": force_index, "term": i, "atoms": atoms, "parameters": parameters}
                    if min(atoms) >= nprotein:
                        local = tuple(a - nprotein for a in atoms)
                        families = {"Bond": ["Bonds"], "Angle": ["Angles"], "Torsion": ["ProperTorsions", "ImproperTorsions"]}[kind]
                        matches = []
                        for family in families:
                            for key, parameter in labels[family].items():
                                match = local == key or local[::-1] == key
                                if family == "ImproperTorsions":
                                    match = local[0] == key[1] and set(local) == set(key)
                                if match:
                                    matches.append((family, list(key), parameter if isinstance(parameter, str) else parameter.id))
                        if len(matches) != 1:
                            raise ValueError(f"Missing/ambiguous assignment metadata: {local}")
                        family, assignment, identifier = matches[0]
                        record.update(family=family, parameter_id=identifier, assignment_atoms=[a + nprotein for a in assignment], improper_center=assignment[1] + nprotein if family == "ImproperTorsions" else None)
                    terms.append(record)
            else:
                raise ValueError(f"Unsupported force {type(force).__name__}")
        if sorted(inventory) != sorted(["HarmonicBondForce", "HarmonicAngleForce", "PeriodicTorsionForce", "NonbondedForce", "CustomGBForce"]):
            raise ValueError("Unexpected force inventory")
        numeric = [[p[k] for k in ("q", "sigma_nm", "epsilon_kj", "mass_da")] + p["gb"] for p in records]
        if not np.isfinite(numeric).all() or any(p["mass_da"] <= 0 for p in records):
            raise ValueError("Invalid particle parameters/masses")
        xml = mm.XmlSerializer.serialize(system)
        return cls(system_xml=xml, particles=tuple(records), terms=tuple(terms), bonds=tuple(bonds), nprotein=nprotein, key=Artifacts.digest(Artifacts.canonical({"system": xml, "particles": records, "terms": terms, "bonds": bonds, "provenance": provenance})), provenance=provenance)

    def subset(self, indices, solvent=False):
        import openmm as mm
        source = mm.XmlSerializer.deserialize(self.system_xml)
        indices = list(indices)
        mapping = {original: local for local, original in enumerate(indices)}
        result = mm.System()
        for i in indices:
            result.addParticle(source.getParticleMass(i))
        for force in source.getForces():
            if isinstance(force, mm.CustomGBForce):
                if not solvent:
                    continue
                # CustomGBForce has no removeParticle API: copy definitions, then selected particles.
                selected = mm.CustomGBForce()
                for i in range(force.getNumPerParticleParameters()): selected.addPerParticleParameter(force.getPerParticleParameterName(i))
                for i in range(force.getNumGlobalParameters()): selected.addGlobalParameter(force.getGlobalParameterName(i), force.getGlobalParameterDefaultValue(i))
                for i in range(force.getNumComputedValues()): selected.addComputedValue(*force.getComputedValueParameters(i))
                for i in range(force.getNumEnergyTerms()): selected.addEnergyTerm(*force.getEnergyTermParameters(i))
                for i in indices: selected.addParticle(force.getParticleParameters(i))
            elif solvent:
                continue
            elif isinstance(force, mm.NonbondedForce):
                selected = mm.NonbondedForce()
                selected.setNonbondedMethod(selected.NoCutoff)
                selected.setUseDispersionCorrection(False)
                for i in indices: selected.addParticle(*force.getParticleParameters(i))
                for i in range(force.getNumExceptions()):
                    a, b, *parameters = force.getExceptionParameters(i)
                    if a in mapping and b in mapping: selected.addException(mapping[a], mapping[b], *parameters)
            else:
                kind, arity = {"HarmonicBondForce": ("Bond", 2), "HarmonicAngleForce": ("Angle", 3), "PeriodicTorsionForce": ("Torsion", 4)}[type(force).__name__]
                selected = type(force)()
                for i in range(getattr(force, "getNum" + kind + "s")()):
                    values = getattr(force, "get" + kind + "Parameters")(i)
                    if all(a in mapping for a in values[:arity]):
                        getattr(selected, "add" + kind)(*[mapping[a] for a in values[:arity]], *values[arity:])
            result.addForce(selected)
        return result
