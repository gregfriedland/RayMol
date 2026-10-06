"""Derived OpenMM protonation with preserved deposited atoms and explicit origins."""

from typing import ClassVar

from .protocol import BaseModelNoExtra, MolecularSnapshot


class ReceptorProtonation(BaseModelNoExtra):
    snapshot: MolecularSnapshot
    parents: ClassVar[dict[str, str]] = {
        "HID": "HIS", "HIE": "HIS", "HIP": "HIS", "ASH": "ASP",
        "GLH": "GLU", "LYN": "LYS", "CYX": "CYS", "CYM": "CYS",
    }

    @classmethod
    def base_name(cls, name, forcefield):
        if len(name) == 4 and name[0] in {"N", "C"} and name in forcefield._templates:
            name = name[1:]
        return name

    def prepare(self, cancelled):
        import importlib.metadata
        import numpy as np
        import openmm as mm
        from openmm import app, unit
        from .prepare import Preparation

        Preparation.check_cancel(cancelled)
        snapshot = self.snapshot
        forcefield = app.ForceField("amber14/protein.ff14SB.xml")
        app.PDBFile._loadNameReplacementTables()
        topology, chains, residues, source_atoms, positions = app.Topology(), {}, {}, [], []
        labels, originals, choices, origins, requested = {}, {}, {}, {}, {}
        neighbors = {i: [] for i in range(len(snapshot.receptor))}
        for a, b in snapshot.receptor_bonds:
            neighbors[a].append(b); neighbors[b].append(a)
        for index, atom in enumerate(snapshot.receptor):
            identity = atom.residue
            if identity not in residues:
                chain = chains.get(atom.chain)
                if chain is None:
                    chain = topology.addChain(atom.chain); chains[atom.chain] = chain
                base = self.base_name(atom.resname, forcefield)
                residues[identity] = topology.addResidue(self.parents.get(base, base), chain, atom.resi)
                labels[identity] = f"{atom.chain or '-'}/{atom.resname}{atom.resi}"
                originals[identity] = {}
            residue = residues[identity]
            replacements = app.PDBFile._atomNameReplacements.get(residue.name, {})
            name = replacements.get(atom.name, atom.name)
            if name in originals[identity]: raise ValueError(f"Duplicate atom {name} at {labels[identity]}")
            originals[identity][name] = atom
            source_atoms.append(topology.addAtom(name, app.element.get_by_symbol(atom.element), residue))
            positions.append(atom.xyz_nm)
        for a, b in snapshot.receptor_bonds: topology.addBond(source_atoms[a], source_atoms[b])
        variants = [None] * len(residues)
        for identity, residue in residues.items():
            atoms = originals[identity]
            first = next(iter(atoms.values()))
            source_variant = self.base_name(first.resname, forcefield)
            override = snapshot.protein_overrides.get(identity)
            if override is not None and override not in forcefield._templates:
                raise ValueError(f"Unsupported ff14SB override {override} at {labels[identity]}")
            nterm = any(a.name == "N" and not any(snapshot.receptor[j].residue != identity for j in neighbors[i]) for i, a in enumerate(snapshot.receptor) if a.residue == identity)
            cterm = any(a.name == "C" and not any(snapshot.receptor[j].residue != identity for j in neighbors[i]) for i, a in enumerate(snapshot.receptor) if a.residue == identity)
            prefix = "N" if nterm else "C" if cterm else ""
            if residue.name in {"ACE", "NME"}: prefix = ""
            choice = self.base_name(override, forcefield) if override else (source_variant if source_variant in self.parents else None)
            origin = "override" if override else "explicit residue name" if choice else "OpenMM pH estimate"
            if choice is None:
                attached = {}
                for i, atom in enumerate(snapshot.receptor):
                    if atom.residue != identity or atom.element == "H": continue
                    attached[atom.name] = [snapshot.receptor[j] for j in neighbors[i] if snapshot.receptor[j].element == "H"]
                if residue.name == "HIS" and (attached.get("ND1") or attached.get("NE2")):
                    choice = "HIP" if attached.get("ND1") and attached.get("NE2") else "HID" if attached.get("ND1") else "HIE"
                elif residue.name == "ASP" and (attached.get("OD1") or attached.get("OD2")): choice = "ASH"
                elif residue.name == "GLU" and (attached.get("OE1") or attached.get("OE2")): choice = "GLH"
                elif residue.name == "CYS" and attached.get("SG"): choice = "CYS"
                elif residue.name == "LYS" and len(attached.get("NZ", [])) == 3: choice = "LYS"
                if choice: origin = "deposited polar hydrogens"
            validation_name = override or snapshot.protein_templates.get(identity) or source_variant
            if validation_name in {"HIS", ""}: validation_name = "HID"
            if prefix and len(validation_name) == 3: validation_name = prefix + validation_name
            if validation_name not in forcefield._templates:
                raise ValueError(f"Unsupported receptor residue {first.resname} at {labels[identity]}")
            replacements = app.PDBFile._atomNameReplacements.get(residue.name, {})
            required = {replacements.get(a.name, a.name) for a in forcefield._templates[validation_name].atoms if a.element.symbol != "H"}
            present = {name for name, atom in atoms.items() if atom.element != "H"}
            if present != required:
                raise ValueError(f"Heavy atoms do not match ff14SB at {labels[identity]}: missing {sorted(required-present)}, extra {sorted(present-required)}")
            if choice is not None:
                variants[residue.index] = choice
                full_choice = override or prefix + choice
                # CYM is supported by ff14SB but not by OpenMM's named H variants.
                if choice == "CYM" or choice not in {"HID", "HIE", "HIP", "ASH", "ASP", "GLH", "GLU", "LYN", "LYS", "CYX", "CYS"}:
                    template = forcefield._templates[full_choice]
                    variants[residue.index] = []
                    for h, atom in enumerate(template.atoms):
                        if atom.element.symbol != "H": continue
                        parent = next(j if i == h else i for i, j in template.bonds if h in (i, j))
                        variants[residue.index].append((replacements.get(atom.name, atom.name), replacements.get(template.atoms[parent].name, template.atoms[parent].name)))
                choices[identity] = choice
                template = forcefield._templates[full_choice]
                allowed_h = {replacements.get(a.name, a.name) for a in template.atoms if a.element.symbol == "H"}
                conflict = sorted(name for name, atom in atoms.items() if atom.element == "H" and name not in allowed_h)
                if conflict: raise ValueError(f"Protonation choice conflicts with deposited hydrogens at {labels[identity]}: {conflict}")
            origins[identity] = origin
            requested[identity] = override
        modeller = app.Modeller(topology, np.asarray(positions) * unit.nanometer)
        try:
            actual = modeller.addHydrogens(forcefield, pH=snapshot.preparation_ph, variants=variants,
                                           platform=mm.Platform.getPlatformByName("CPU"))
            matched = forcefield.getMatchingTemplates(modeller.topology)
        except ValueError as error:
            raise ValueError(f"Receptor protonation failed: {error}") from error
        Preparation.check_cancel(cancelled)
        xyz = np.asarray(modeller.positions.value_in_unit(unit.nanometer))
        if not np.isfinite(xyz).all(): raise ValueError("OpenMM generated non-finite receptor coordinates")
        assigned, hydrogen_positions, assignments = {}, {}, []
        for identity, residue, template, variant in zip(residues, modeller.topology.residues(), matched, actual):
            name = template.name
            base = self.base_name(name, forcefield)
            choice = choices.get(identity) or (variant if isinstance(variant, str) else None)
            if choice is not None and base != choice:
                raise ValueError(f"OpenMM variant and ff14SB template disagree at {labels[identity]}: {choice} vs {name}")
            override = requested[identity]
            if override and (self.base_name(override, forcefield) != base or (len(override) == 4 and override != name)):
                raise ValueError(f"Override and ff14SB template disagree at {labels[identity]}: {override} vs {name}")
            current = {atom.name: atom for atom in residue.atoms()}
            for atom_name, source in originals[identity].items():
                atom = current.get(atom_name)
                if atom is None or atom.element.symbol != source.element or not np.array_equal(xyz[atom.index], source.xyz_nm):
                    raise ValueError(f"OpenMM changed a deposited atom at {labels[identity]}/{atom_name}")
            assigned[identity] = name
            replacements = app.PDBFile._atomNameReplacements.get(residue.name, {})
            for atom in template.atoms:
                if atom.element.symbol == "H":
                    hydrogen_positions[(identity, atom.name)] = xyz[current[replacements.get(atom.name, atom.name)].index].tolist()
            charge = sum(atom.parameters["charge"] for atom in template.atoms)
            if not np.isfinite(charge) or abs(charge-round(charge)) > 1e-6:
                raise ValueError(f"Invalid ff14SB residue charge at {labels[identity]}")
            assignments.append({"residue": identity, "label": labels[identity], "template": name, "origin": origins[identity],
                                "charge_e": charge, "requested_variant": choices.get(identity),
                                "openmm_variant": variant if isinstance(variant, str) else None})
        protonation = {"pH": snapshot.preparation_ph, "method": "OpenMM Modeller.addHydrogens with ff14SB; receptor-only hydrogen-bond environment",
                       "openmm_version": importlib.metadata.version("openmm"), "assumption": "Estimated protonation, not an experimental assignment; supplied explicit states and polar hydrogens preserved",
                       "assignments": assignments}
        return snapshot.model_copy(update={"protein_templates": assigned}), hydrogen_positions, protonation
