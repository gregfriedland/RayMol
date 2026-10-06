"""Assign SMILES chemistry to an existing scene pose without generating a new pose."""

from collections import Counter

from pydantic import Field, model_validator

from .protocol import BaseModelNoExtra, SceneAtom


class LigandInput(BaseModelNoExtra):
    smiles: str = Field(min_length=1, max_length=4096)
    atoms: list[SceneAtom] = Field(min_length=1, max_length=100)
    bonds: list[tuple[int, int]]

    @model_validator(mode="after")
    def check_graph(self):
        edges = [tuple(sorted(edge)) for edge in self.bonds]
        if len(set(edges)) != len(edges) or any(a < 0 or b >= len(self.atoms) or a == b for a, b in edges):
            raise ValueError("Invalid ligand connectivity")
        if len({atom.source_id for atom in self.atoms}) != len(self.atoms):
            raise ValueError("Duplicate ligand source atom IDs")
        return self

    def prepare(self, cancelled):
        import numpy as np
        from rdkit import Chem

        if cancelled.is_set(): raise InterruptedError("Chemistry review cancelled")
        template = Chem.MolFromSmiles(self.smiles.strip())
        if template is None: raise ValueError("Invalid ligand SMILES")
        if len(Chem.GetMolFrags(template)) != 1 or any(a.GetNumRadicalElectrons() for a in template.GetAtoms()):
            raise ValueError("SMILES must describe one connected, radical-free ligand")
        if any(a.GetIsotope() for a in template.GetAtoms()):
            raise ValueError("Isotopic SMILES require an explicitly mapped SDF")
        if any(s.specified == Chem.StereoSpecified.Unspecified for s in Chem.FindPotentialStereo(template)):
            raise ValueError("SMILES must specify all ligand stereochemistry (@, @@, / or \\)")
        template = Chem.RemoveHs(template)
        for atom in template.GetAtoms(): atom.SetAtomMapNum(0)
        heavy = [i for i, atom in enumerate(self.atoms) if atom.element != "H"]
        if Counter(self.atoms[i].element for i in heavy) != Counter(a.GetSymbol() for a in template.GetAtoms()):
            raise ValueError("SMILES heavy-atom count or elements do not match the selected ligand")
        reverse = {source: local for local, source in enumerate(heavy)}
        probe = Chem.RWMol()
        for i in heavy: probe.AddAtom(Chem.Atom(self.atoms[i].element))
        for a, b in self.bonds:
            if a in reverse and b in reverse: probe.AddBond(reverse[a], reverse[b], Chem.BondType.SINGLE)
        query = Chem.RWMol()
        for atom in template.GetAtoms(): query.AddAtom(Chem.AtomFromSmarts(f"[#{atom.GetAtomicNum()}]"))
        for bond in template.GetBonds():
            query.AddBond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx(), Chem.BondType.SINGLE)
        # Query bonds ignore order, but the complete scene connectivity must match.
        parameters = Chem.AdjustQueryParameters.NoAdjustments()
        parameters.makeBondsGeneric = True
        query = Chem.AdjustQueryProperties(query, parameters)
        if probe.GetNumBonds() != template.GetNumBonds():
            raise ValueError("Ligand connectivity does not match SMILES; check the selection or supply a mapped SDF")
        matches = probe.GetMol().GetSubstructMatches(query, uniquify=False, maxMatches=257)
        if not matches: raise ValueError("Ligand connectivity does not match SMILES")
        if len(matches) > 256: raise ValueError("Too many ligand atom mappings; supply an explicitly mapped SDF")
        expected = Chem.MolToSmiles(template)
        assignments = {}
        for match in matches:
            if cancelled.is_set(): raise InterruptedError("Chemistry review cancelled")
            mol = Chem.Mol(template)
            conf = Chem.Conformer(mol.GetNumAtoms())
            conf.Set3D(True)
            for index, local in enumerate(match): conf.SetAtomPosition(index, np.asarray(self.atoms[heavy[local]].xyz_nm)*10)
            mol.AddConformer(conf)
            # Perceive stereo from coordinates independently of the SMILES tags.
            perceived = Chem.Mol(mol)
            Chem.AssignStereochemistryFrom3D(perceived, replaceExistingTags=True)
            if Chem.MolToSmiles(perceived) != expected: continue
            for index, local in enumerate(match): mol.GetAtomWithIdx(index).SetAtomMapNum(heavy[local]+1)
            identity = Chem.MolToSmiles(mol)
            assignments.setdefault(identity, (mol, match))
        if not assignments:
            raise ValueError("Ligand pose stereochemistry disagrees with SMILES or cannot be resolved from 3D coordinates")
        if len(assignments) != 1:
            raise ValueError("Ambiguous SMILES-to-scene chemistry; supply an SDF with explicit atom indices")
        mol, match = next(iter(assignments.values()))
        for atom in mol.GetAtoms(): atom.SetAtomMapNum(0)
        mol = Chem.AddHs(mol, addCoords=True)
        mapping = [None] * len(self.atoms)
        for index, local in enumerate(match): mapping[heavy[local]] = index
        used = set(mapping) - {None}
        for source, atom in enumerate(self.atoms):
            if atom.element != "H": continue
            parents = [b if a == source else a for a, b in self.bonds if source in (a, b)]
            if len(parents) != 1 or parents[0] not in reverse:
                raise ValueError("Each scene hydrogen must be bonded to one ligand heavy atom")
            parent = mapping[parents[0]]
            candidates = [a.GetIdx() for a in mol.GetAtomWithIdx(parent).GetNeighbors() if a.GetSymbol() == "H" and a.GetIdx() not in used]
            if not candidates: raise ValueError("Scene hydrogen count disagrees with the SMILES protonation state")
            target = candidates[0]
            mapping[source] = target; used.add(target)
            mol.GetConformer().SetAtomPosition(target, np.asarray(atom.xyz_nm)*10)
        # Explicit source H can contradict a stereocenter even when the heavy pose does not.
        perceived = Chem.Mol(mol)
        Chem.AssignStereochemistryFrom3D(perceived, replaceExistingTags=True)
        if Chem.MolToSmiles(Chem.RemoveHs(perceived)) != expected:
            raise ValueError("Scene hydrogens disagree with SMILES stereochemistry")
        if cancelled.is_set(): raise InterruptedError("Chemistry review cancelled")
        return {"sdf": Chem.MolToMolBlock(mol, forceV3000=True), "mapping": mapping, "smiles": expected}
