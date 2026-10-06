"""Validated template construction, one charge cache, and explicit hydrogen endpoints."""

import importlib.metadata
import json
from pathlib import Path
import sysconfig
from typing import Any

from .artifacts import Artifacts
from .model import PreparedModel, SolventProfile
from .protocol import BaseModelNoExtra, MolecularSnapshot
from .runtime import Runtime


class Preparation(BaseModelNoExtra):
    directory: Path
    current: Any = None
    current_signature: str | None = None

    @staticmethod
    def check_cancel(cancelled):
        if cancelled.is_set():
            raise InterruptedError("Analysis cancelled")

    @staticmethod
    def protein(snapshot, forcefield, hydrogen_positions=None):
        import numpy as np
        from openmm import app
        app.PDBFile._loadNameReplacementTables()
        topology, chains, residues, by_source, particles, positions = app.Topology(), {}, {}, {}, [], []
        for atom in snapshot.receptor:
            if atom.residue not in residues:
                chain = chains.setdefault(atom.chain, topology.addChain(atom.chain)) if atom.chain not in chains else chains[atom.chain]
                residues[atom.residue] = topology.addResidue(atom.resname, chain, atom.resi)
        template_map, template_atoms, source_atoms = {}, {}, {}
        for identity, residue in residues.items():
            name = snapshot.protein_templates[identity]
            if name not in forcefield._templates:
                raise ValueError(f"Unsupported ff14SB template {name} for {identity}")
            if name.endswith("HIS") or name == "HIS":
                raise ValueError("Histidine requires HID, HIE or HIP")
            template = forcefield._templates[name]
            replacements = app.PDBFile._atomNameReplacements.get(residue.name, {})
            normalized = {replacements.get(a.name, a.name): i for i, a in enumerate(template.atoms)}
            if len(normalized) != len(template.atoms):
                raise ValueError("Ambiguous template atom names")
            input_atoms = [a for a in snapshot.receptor if a.residue == identity]
            matching = {}
            for atom in input_atoms:
                key = replacements.get(atom.name, atom.name)
                if key not in normalized or normalized[key] in matching:
                    raise ValueError(f"Atom {atom.name} does not uniquely match declared {name}")
                index = normalized[key]
                if template.atoms[index].element.symbol != atom.element:
                    raise ValueError("Template element mismatch")
                matching[index] = atom
            required = {i for i, a in enumerate(template.atoms) if a.element.symbol != "H"}
            if not required.issubset(matching):
                raise ValueError(f"Missing heavy atoms in {identity}: {[template.atoms[i].name for i in sorted(required - matching.keys())]}")
            template_map[residue] = name
            template_atoms[identity], source_atoms[identity] = template, matching
            for i, template_atom in enumerate(template.atoms):
                source = matching.get(i)
                new_atom = topology.addAtom(template_atom.name, template_atom.element, residue)
                if source:
                    xyz = source.xyz_nm
                    source_id = source.source_id
                    by_source[source_id] = new_atom
                elif hydrogen_positions is not None:
                    key = (identity, template_atom.name)
                    if key not in hydrogen_positions: raise ValueError(f"OpenMM hydrogen missing at {residue.chain.id}/{residue.name}{residue.id}/{template_atom.name}")
                    xyz = hydrogen_positions[key]
                    source_id = None
                else:
                    bonded = [b if a == i else a for a, b in template.bonds if a == i or b == i]
                    if len(bonded) != 1 or template.atoms[bonded[0]].element.symbol == "H":
                        raise ValueError("Generated H must have one heavy parent")
                    parent = bonded[0]
                    heavy_neighbors = [b if a == parent else a for a, b in template.bonds if a == parent or b == parent]
                    heavy_neighbors = [j for j in heavy_neighbors if j in matching and template.atoms[j].element.symbol != "H"]
                    origin = np.asarray(matching[parent].xyz_nm)
                    vectors = [np.asarray(matching[j].xyz_nm) - origin for j in heavy_neighbors]
                    if any(np.linalg.norm(v) <= 1e-8 for v in vectors):
                        raise ValueError("Overlapping bonded heavy atoms")
                    away = -sum((v / np.linalg.norm(v) for v in vectors), np.zeros(3))
                    seed = int(Artifacts.digest(f"{identity}/{template_atom.name}".encode())[:16], 16)
                    direction = away + np.random.default_rng(seed).normal(size=3) * 0.7
                    if np.linalg.norm(direction) <= 1e-8:
                        raise ValueError("Degenerate H initialization")
                    length = {"C": 0.109, "N": 0.101, "O": 0.096, "S": 0.134}[template.atoms[parent].element.symbol]
                    xyz = origin + direction / np.linalg.norm(direction) * length
                    source_id = None
                positions.append(list(xyz))
                particles.append({"source_id": source_id, "prepared_id": f"P/{identity}/{template_atom.name}", "element": template_atom.element.symbol, "name": template_atom.name, "residue": identity, "resname": residue.name, "chain": residue.chain.id, "resi": residue.id, "parent": None})
        # Verify the deposited heavy graph, including explicitly confirmed disulfides.
        expected_internal = set()
        actual_internal, external = set(), []
        for identity, template in template_atoms.items():
            atoms = list(residues[identity].atoms())
            matching = source_atoms[identity]
            for a, b in template.bonds:
                topology.addBond(atoms[a], atoms[b])
                if a in matching and b in matching and template.atoms[a].element.symbol != "H" and template.atoms[b].element.symbol != "H":
                    expected_internal.add(tuple(sorted((matching[a].source_id, matching[b].source_id))))
        for a, b in snapshot.receptor_bonds:
            left, right = snapshot.receptor[a], snapshot.receptor[b]
            if left.residue == right.residue:
                if left.element != "H" and right.element != "H":
                    actual_internal.add(tuple(sorted((left.source_id, right.source_id))))
            else:
                if left.element == "H" or right.element == "H":
                    raise ValueError("Cross-residue hydrogen bond in covalent graph")
                topology.addBond(by_source[left.source_id], by_source[right.source_id])
                external.extend([by_source[left.source_id], by_source[right.source_id]])
        if actual_internal != expected_internal:
            raise ValueError("Deposited receptor heavy graph differs from declared AMBER templates")
        for identity, template in template_atoms.items():
            atoms = list(residues[identity].atoms())
            actual = sorted(a.name for a in external if a.residue == residues[identity])
            expected = sorted(atoms[i].name for i in template.externalBonds)
            if actual != expected:
                raise ValueError(f"Termini/disulfide connectivity disagrees with declared template at {identity}")
        for a, b in topology.bonds():
            if a.element.symbol == "H": particles[a.index]["parent"] = b.index
            if b.element.symbol == "H": particles[b.index]["parent"] = a.index
        return topology, np.asarray(positions), particles, template_map

    def ligand(self, snapshot, cancelled):
        import numpy as np
        from rdkit import Chem
        from openff.toolkit import Molecule, ForceField
        from openff.units import unit as offunit
        from openff.toolkit.utils.nagl_wrapper import NAGLToolkitWrapper
        import platformdirs
        import torch
        self.check_cancel(cancelled)
        if snapshot.ligand_sdf.count("M  END") != 1 or snapshot.ligand_sdf.count("$$$$") > 1:
            raise ValueError("Attach exactly one ligand SDF record")
        rd = Chem.MolFromMolBlock(snapshot.ligand_sdf, removeHs=False, sanitize=True, strictParsing=True)
        if rd is None or len(Chem.GetMolFrags(rd)) != 1 or any(a.GetNumRadicalElectrons() for a in rd.GetAtoms()):
            raise ValueError("Ligand must be one supported, non-radical explicit graph")
        if rd.GetNumConformers() != 1:
            raise ValueError("One SDF conformer required")
        expected_stereo = Chem.MolToSmiles(Chem.RemoveHs(rd))
        if snapshot.ligand_smiles is not None:
            declared = Chem.MolFromSmiles(snapshot.ligand_smiles)
            if declared is None or Chem.MolToSmiles(Chem.RemoveHs(declared)) != expected_stereo:
                raise ValueError("Embedded SMILES and SDF disagree on ligand chemistry")
        source_heavy = [a for a in snapshot.ligand if a.element != "H"]
        sdf_heavy = {a.GetIdx() for a in rd.GetAtoms() if a.GetAtomicNum() != 1}
        source_mapping = dict(zip(snapshot.ligand_map, snapshot.ligand))
        if {i for i, a in source_mapping.items() if a.element != "H"} != sdf_heavy:
            raise ValueError("SDF-to-scene map must cover every ligand heavy atom")
        positions = rd.GetConformer().GetPositions() / 10
        old_heavy = positions[sorted(sdf_heavy)]
        new_heavy = np.asarray([source_mapping[i].xyz_nm for i in sorted(sdf_heavy)])
        a, b = old_heavy - old_heavy.mean(axis=0), new_heavy - new_heavy.mean(axis=0)
        u, _, vt = np.linalg.svd(a.T @ b)
        rotation = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
        for atom in rd.GetAtoms():
            index = atom.GetIdx()
            if atom.GetSymbol() == "H" and index not in source_mapping:
                neighbors = list(atom.GetNeighbors())
                if len(neighbors) != 1 or neighbors[0].GetIdx() not in source_mapping:
                    raise ValueError("Declared ligand H needs one mapped heavy parent")
                parent = neighbors[0].GetIdx()
                xyz = np.asarray(source_mapping[parent].xyz_nm) + (positions[index] - positions[parent]) @ rotation
                rd.GetConformer().SetAtomPosition(index, xyz * 10)
        for index, source in source_mapping.items():
            if index < 0 or index >= rd.GetNumAtoms(): raise ValueError("SDF atom index outside graph")
            if source.element != rd.GetAtomWithIdx(index).GetSymbol():
                raise ValueError("SDF-to-scene element mismatch")
            rd.GetConformer().SetAtomPosition(index, np.asarray(source.xyz_nm) * 10)
        # RDKit adds coordinates only for newly declared valence hydrogens; no conformer search.
        rd = Chem.AddHs(rd, addCoords=True)
        perceived = Chem.RemoveHs(rd)
        Chem.AssignStereochemistryFrom3D(perceived, replaceExistingTags=True)
        if Chem.MolToSmiles(perceived) != expected_stereo:
            raise ValueError("Current ligand heavy pose disagrees with reviewed stereochemistry; review chemistry again")
        perceived = Chem.Mol(rd)
        Chem.AssignStereochemistryFrom3D(perceived, replaceExistingTags=True)
        if Chem.MolToSmiles(Chem.RemoveHs(perceived)) != expected_stereo:
            raise ValueError("Current ligand pose disagrees with reviewed stereochemistry; review chemistry again")
        rank = list(Chem.CanonicalRankAtoms(rd, breakTies=True, includeChirality=True))
        order = sorted(range(rd.GetNumAtoms()), key=rank.__getitem__)
        inverse = {old: new for new, old in enumerate(order)}
        rd = Chem.RenumberAtoms(rd, order)
        molecule = Molecule.from_rdkit(rd, hydrogens_are_explicit=True, allow_undefined_stereo=False)
        molecule.name = "LIG"
        for i, atom in enumerate(molecule.atoms): atom.name = f"{atom.symbol}{i + 1}"
        profile = SolventProfile.load()
        versions = {name: importlib.metadata.version(name) for name in ("openff-toolkit", "openff-nagl", "openmm", "openmmforcefields", "rdkit")}
        identity = {"smiles": molecule.to_smiles(isomeric=True, explicit_hydrogens=True, mapped=False), "profile": profile, "versions": versions}
        chemical_key = Artifacts.digest(Artifacts.canonical(identity))
        cache = Artifacts(root=self.directory / "parameters" / chemical_key)
        parameter_file = cache.path("parameters.json")
        site = Path(sysconfig.get_path("purelib"))
        offxml = site / "openforcefields/offxml/openff_unconstrained-2.3.0.offxml"
        if parameter_file.exists():
            entry = json.loads(parameter_file.read_text())
            if entry["identity"] != identity or entry["digest"] != Artifacts.digest(Artifacts.canonical({"charges": entry["charges"], "labels": entry["labels"]})):
                raise ValueError("Ligand parameter cache identity/integrity mismatch")
            charges, labels = entry["charges"], entry["labels"]
        else:
            platformdirs.user_cache_path(ensure_exists=True)
            torch.set_num_threads(4)
            if not Runtime.torch_configured:
                if torch.get_num_interop_threads() != 1: torch.set_num_interop_threads(1)
                Runtime.torch_configured = True
            model = site / "openff/nagl_models/models/am1bcc/openff-gnn-am1bcc-1.0.0.pt"
            NAGLToolkitWrapper().assign_partial_charges(molecule, str(model), normalize_partial_charges=False, file_hash=profile["assets"]["openff/nagl_models/models/am1bcc/openff-gnn-am1bcc-1.0.0.pt"])
            charges = molecule.partial_charges.m_as(offunit.elementary_charge).tolist()
            assigned = ForceField(str(offxml)).label_molecules(molecule.to_topology())[0]
            labels = {family: [{"atoms": list(atoms), "id": parameter.id} for atoms, parameter in assigned[family].items()] for family in ("Bonds", "Angles", "ProperTorsions", "ImproperTorsions")}
            self.check_cancel(cancelled)
            cache.write_json("parameters.json", {"identity": identity, "charges": charges, "labels": labels, "digest": Artifacts.digest(Artifacts.canonical({"charges": charges, "labels": labels}))})
        if len(charges) != molecule.n_atoms or not np.isfinite(charges).all() or abs(sum(charges) - molecule.total_charge.m_as(offunit.elementary_charge)) > 1e-6:
            raise ValueError("Invalid cached/inferred charges")
        molecule.partial_charges = np.asarray(charges) * offunit.elementary_charge
        source_map = {inverse[index]: atom for index, atom in source_mapping.items()}
        xyz = np.array(molecule.conformers[0].m_as(offunit.nanometer), copy=True)
        # Avoid an Angstrom-to-nm round trip changing any supplied coordinate bits.
        for index, source in source_map.items(): xyz[index] = source.xyz_nm
        particles = [{"source_id": source_map[i].source_id if i in source_map else None, "prepared_id": f"L/{i}", "element": atom.symbol, "name": atom.name, "residue": source_heavy[0].residue, "resname": "LIG", "chain": "L", "resi": "1", "parent": None, "formal_charge": int(atom.formal_charge.m_as(offunit.elementary_charge))} for i, atom in enumerate(molecule.atoms)]
        for bond in molecule.bonds:
            a, b = bond.atom1_index, bond.atom2_index
            if particles[a]["element"] == "H": particles[a]["parent"] = b
            if particles[b]["element"] == "H": particles[b]["parent"] = a
        return molecule, xyz, particles, labels, chemical_key, cache.path("templates.json"), versions

    def construct(self, snapshot, cancelled):
        import numpy as np
        import openmm as mm
        from openmm import app, unit
        from openmmforcefields.generators import SMIRNOFFTemplateGenerator
        from openff.units import unit as offunit
        profile = SolventProfile.load()
        forcefield = app.ForceField("amber14/protein.ff14SB.xml", "implicit/obc2.xml")
        hydrogen_positions, protonation = None, None
        if snapshot.preparation_recipe == "openmm-ph-v1":
            from .protonation import ReceptorProtonation
            snapshot, hydrogen_positions, protonation = ReceptorProtonation(snapshot=snapshot).prepare(cancelled)
        topology, positions, particles, template_map = self.protein(snapshot, forcefield, hydrogen_positions)
        molecule, ligand_positions, ligand_particles, labels, chemical_key, cache, versions = self.ligand(snapshot, cancelled)
        nprotein = len(particles)
        for p in ligand_particles:
            if p["parent"] is not None: p["parent"] += nprotein
        modeller = app.Modeller(topology, positions * unit.nanometer)
        modeller.add(molecule.to_topology().to_openmm(), ligand_positions * unit.nanometer)
        source_orders = {tuple(sorted((snapshot.receptor[a].source_id, snapshot.receptor[b].source_id))): order for (a, b), order in zip(snapshot.receptor_bonds, snapshot.receptor_bond_orders)}
        graph = []
        for a, b in topology.bonds():
            ids = [particles[i]["source_id"] for i in (a.index, b.index)]
            order = source_orders[tuple(sorted(ids))] if all(ids) else 1
            graph.append({"atoms": [a.index, b.index], "order": order})
        for bond in molecule.bonds:
            graph.append({"atoms": [nprotein+bond.atom1_index, nprotein+bond.atom2_index], "order": 4 if bond.is_aromatic else int(bond.bond_order)})
        particles += ligand_particles
        if nprotein > 10000 or len(ligand_particles) > 100:
            raise ValueError("Prepared-size limit: receptor <=10000 and ligand <=100 particles including H")
        coordinates = np.vstack([positions, ligand_positions])
        signature = Artifacts.digest(Artifacts.canonical({"particles": particles, "bonds": graph, "templates": snapshot.protein_templates, "chemical_key": chemical_key, "exclusions": snapshot.exclusions, "recipe": snapshot.preparation_recipe, "protonation": protonation}))
        if self.current_signature == signature:
            return self.current, coordinates
        site = Path(sysconfig.get_path("purelib"))
        generator = SMIRNOFFTemplateGenerator(molecules=[molecule], forcefield=str(site / "openforcefields/offxml/openff_unconstrained-2.3.0.offxml"), cache=str(cache))
        forcefield.registerTemplateGenerator(generator.generator)
        self.check_cancel(cancelled)
        # Modeller.add copies residues; bind the declared template to the copied residue.
        copied = list(modeller.topology.residues())
        residue_templates = {copied[r.index]: name for r, name in template_map.items()}
        system = forcefield.createSystem(modeller.topology, nonbondedMethod=app.NoCutoff, constraints=None, rigidWater=False, removeCMMotion=False, soluteDielectric=1.0, solventDielectric=78.5, residueTemplates=residue_templates)
        nb = next(f for f in system.getForces() if isinstance(f, mm.NonbondedForce))
        nb.setUseDispersionCorrection(False)
        np.testing.assert_allclose([nb.getParticleParameters(i)[0].value_in_unit(unit.elementary_charge) for i in range(nprotein, len(particles))], molecule.partial_charges.m_as(offunit.elementary_charge), rtol=0, atol=1e-12)
        provenance = {"profile": profile, "versions": versions, "chemical_key": chemical_key, "recipe": snapshot.preparation_recipe, "precision": "CPU mixed-precision pre-optimization for systems >100 particles, then double-precision Reference polishing and authoritative checks; double-precision arrays", "mm_threads": Runtime.mm_threads(), "exclusions": snapshot.exclusions, "templates": snapshot.protein_templates}
        if protonation is not None: provenance["protonation"] = protonation
        labels = {family: {tuple(r["atoms"]): r["id"] for r in records} for family, records in labels.items()}
        self.current = PreparedModel.extract(system, modeller.topology, particles, nprotein, labels, provenance, graph)
        self.current_signature = signature
        return self.current, coordinates

    @classmethod
    def minimize(cls, system, positions, active, iterations, cancelled):
        import numpy as np
        import openmm as mm
        from openmm import unit
        cls.check_cancel(cancelled)
        copy = mm.XmlSerializer.deserialize(mm.XmlSerializer.serialize(system))
        active = np.asarray(active, dtype=bool)
        if not active.any():
            return np.array(positions, copy=True), {"rms_force_kj_mol_nm": 0.0, "iterations_limit": iterations, "converged": True}
        for i in np.flatnonzero(~active): copy.setParticleMass(int(i), 0)
        # Small isolated ligands are cheaper without a native CPU thread pool.
        platforms = ("CPU", "Reference") if len(active) > 100 else ("Reference",)
        result, stages = np.array(positions, copy=True), []
        # OpenMM averages gradients over Cartesian coordinates, including frozen
        # zeros. Convert the active-atom vector-RMS requirement to its tolerance.
        tolerance = float(np.sqrt(active.sum() / (3 * len(active))))
        class Reporter(mm.MinimizationReporter):
            def report(self, iteration, x, grad, args):
                return cancelled.is_set()
        for platform in platforms:
            cls.check_cancel(cancelled)
            threads = Runtime.mm_threads() if platform == "CPU" else 1
            properties = {"Threads": str(threads), "DeterministicForces": "true"} if platform == "CPU" else {}
            integrator = mm.VerletIntegrator(0.001)
            context = mm.Context(copy, integrator, mm.Platform.getPlatformByName(platform), properties)
            try:
                context.setPositions(result * unit.nanometer)
                mm.LocalEnergyMinimizer.minimize(context, tolerance, iterations, Reporter())
                cls.check_cancel(cancelled)
                state = context.getState(getPositions=True, getForces=True, getEnergy=True)
                result = state.getPositions(asNumpy=True).value_in_unit(unit.nanometer)
                forces = state.getForces(asNumpy=True).value_in_unit(unit.kilojoule_per_mole / unit.nanometer)
                rms = float(np.sqrt(np.mean(np.sum(forces[active] ** 2, axis=1))))
                if not np.isfinite(result).all() or not np.isfinite(forces).all() or not np.isfinite(state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)):
                    raise ValueError("Non-finite minimization")
                if not np.array_equal(result[~active], np.asarray(positions)[~active]):
                    raise ValueError("Frozen atoms moved")
                stages.append({"platform": platform, "threads": threads, "iterations_limit": iterations, "rms_force_kj_mol_nm": rms})
            finally:
                del context, integrator
        if rms > 1.0:
            raise ValueError(f"Minimization did not converge: active RMS force {rms:.6g} > 1 kJ/mol/nm after at most {iterations} Reference iterations")
        return result, {"rms_force_kj_mol_nm": rms, "iterations_limit": iterations, "total_iterations_limit": iterations * len(platforms), "stages": stages, "converged": True}

    def endpoint(self, model, coordinates, cancelled):
        import numpy as np
        n, count = model.nprotein, len(model.particles)
        hydrogen = np.array([p["element"] == "H" for p in model.particles])
        receptor, receptor_meta = self.minimize(model.subset(range(n)), coordinates[:n], hydrogen[:n], 300, cancelled)
        start = np.vstack([receptor, coordinates[n:]])
        active = hydrogen.copy()
        active[:n] = False
        result, ligand_meta = self.minimize(model.subset(range(count)), start, active, 200, cancelled)
        if not np.array_equal(result[~hydrogen], coordinates[~hydrogen]):
            raise ValueError("Deposited heavy coordinates changed")
        return result, {"receptor": receptor_meta, "ligand": ligand_meta, "endpoint_hash": Artifacts.digest(result.astype("<f8").tobytes())}
