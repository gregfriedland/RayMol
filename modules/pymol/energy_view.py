"""Scene input and owned molecular annotations; numerical work stays in the helper."""

import hashlib
import json
import math
from pathlib import Path
import uuid
import copy

from pymol import cmd, cgo
from pymol.cmd import _cmd


class EnergyView:
    analyses = {}
    epoch = uuid.uuid4().hex
    session_root = Path.home() / "Library/Application Support/RayMol Energy Local/saved"

    @staticmethod
    def ledger():
        with cmd.lockcm:
            return {token: {"name": name, "enabled": bool(enabled), "parent": parent, "kind": kind}
                    for name, token, enabled, parent, kind in _cmd.energy_object_records(cmd._COb)}

    @classmethod
    def reconcile(cls, analysis):
        ledger = cls.ledger()
        if analysis.get("mask") and analysis["mask"]["expected"] != ledger:
            analysis.pop("mask")
        for leaf, token in analysis.get("tokens", {}).items():
            if token not in ledger:
                analysis.setdefault("deleted_components", set()).add(leaf)
                analysis["markers"].pop(analysis["owned"].get(leaf), None)
                continue
            old, new = analysis["owned"].get(leaf), ledger[token]["name"]
            if old != new:
                analysis["owned"][leaf] = new
                analysis["markers"][new] = analysis["markers"].pop(old, {})
        if analysis.get("parent_token") in ledger:
            analysis["parent"] = ledger[analysis["parent_token"]]["name"]
        elif analysis.get("parent_token"):
            analysis["removed"] = True
        return ledger

    @classmethod
    def source_signature(cls, records, bonds, orders, source_models):
        return {"atoms": {a["source_id"]: {**{k: a[k] for k in ("name", "element", "resname", "chain", "resi", "xyz_nm")}, "formal_charge": atom.formal_charge} for a, atom in zip(records, source_models)},
                "bonds": sorted([*sorted([records[a]["source_id"], records[b]["source_id"]]), order] for (a, b), order in zip(bonds, orders))}

    @classmethod
    def source_current(cls, analysis):
        expected = analysis["signature"]
        ids = {analysis.get("uid_map", {}).get(source, int(source.split("/")[1])): source for source in expected["atoms"]}
        atoms, bonds, locators = {}, [], {}
        state = analysis["snapshot"]["state"]
        for record in cls.ledger().values():
            if record["kind"] != "molecule": continue
            name = record["name"]
            with cmd.lockcm:
                try: native = {index: (uid, xyz) for index, uid, xyz in _cmd.energy_atom_records(cmd._COb, name, state - 1)}
                except ValueError: continue
            if not any(uid in ids for uid, _ in native.values()): continue
            model = cmd.get_model("%" + name, state)
            local = {}
            for i, atom in enumerate(model.atom):
                if atom.index not in native: continue
                uid, xyz = native[atom.index]
                if uid not in ids: continue
                if atom.q <= 0 or atom.alt: return None
                source = ids[uid]
                atoms[source] = {"name": atom.name, "element": atom.symbol, "resname": atom.resn, "chain": atom.chain, "resi": atom.resi, "xyz_nm": [v/10 for v in xyz], "formal_charge": atom.formal_charge}
                locators[source] = [name, atom.index]
                local[i] = source
            for bond in model.bond:
                if sum(i in local for i in bond.index) == 1: return None
                if all(i in local for i in bond.index): bonds.append([*sorted(local[i] for i in bond.index), bond.order])
        analysis["locators"] = locators
        return {"atoms": atoms, "bonds": sorted(bonds)}

    @classmethod
    def input_options(cls):
        return {"molecules": [name for name in cmd.get_names("public_objects")
                              if cmd.get_type(name) == "object:molecule" and cmd.count_atoms("%" + name) > 0],
                "selections": [name for name in cmd.get_names("public_selections") if cmd.count_atoms("%" + name) > 0],
                "session_epoch": [cls.epoch]}

    @staticmethod
    def atom_bindings(selection, state):
        indices = cmd.index(f"({selection}) and state {state}")
        model = cmd.get_model(selection, state)
        if len(indices) != len(model.atom): raise ValueError("Missing atoms in requested coordinate state")
        return [{"object": name, "index": index, "name": atom.name, "element": atom.symbol,
                 "chain": atom.chain, "segi": atom.segi, "resi": atom.resi, "resn": atom.resn}
                for (name, index), atom in zip(indices, model.atom)]

    @classmethod
    def ligand_chemistry(cls, ligand, state):
        payload = getattr(cmd._pymol.session, "ligand_chemistry", None)
        if payload is None: return None
        if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1 or not isinstance(payload.get("ligands"), list):
            raise ValueError("Unsupported embedded ligand chemistry")
        bindings = cls.atom_bindings(ligand, state)
        selected = {(atom["object"], atom["index"]) for atom in bindings}
        matches = []
        for record in payload["ligands"]:
            if not isinstance(record, dict): raise ValueError("Invalid embedded ligand chemistry record")
            if record.get("object") not in {atom["object"] for atom in bindings}: continue
            if type(record.get("state")) is not int or record["state"] < 1: raise ValueError("Invalid embedded ligand state")
            if record["state"] != state: continue
            atoms = record.get("atoms")
            if not isinstance(atoms, list) or not atoms: raise ValueError("Embedded ligand has no atom bindings")
            indices = {(record["object"], atom["index"]) for atom in atoms}
            if not selected.intersection(indices): continue
            if indices != selected: raise ValueError("Select exactly the ligand atoms bound to embedded chemistry")
            if len(indices) != len(atoms): raise ValueError("Duplicate embedded ligand atom binding")
            by_index = {atom["index"]: atom for atom in atoms}
            for binding in bindings:
                atom = by_index[binding["index"]]
                if type(atom["index"]) is not int or atom["index"] < 1 or any(atom.get(key) != value for key, value in binding.items() if key != "object"):
                    raise ValueError(f"Embedded ligand atom binding changed: {record['object']}/{binding['name']}")
            smiles, sdf = record.get("smiles"), record.get("sdf")
            if smiles is not None and (not isinstance(smiles, str) or not smiles.strip() or len(smiles) > 4096): raise ValueError("Invalid embedded SMILES")
            if sdf is not None and (not isinstance(sdf, str) or not sdf.strip() or len(sdf) > 200000): raise ValueError("Invalid embedded SDF")
            if smiles is None and sdf is None: raise ValueError("Embedded ligand has no explicit chemistry")
            mapping = [by_index[atom["index"]].get("sdf_index") for atom in bindings]
            if sdf is not None and (any(type(index) is not int or index < 0 for index in mapping) or len(set(mapping)) != len(mapping)):
                raise ValueError("Embedded SDF atom mapping is invalid")
            matches.append({"smiles": smiles, "sdf": sdf, "mapping": mapping if sdf is not None else None,
                            "source": record.get("source", "PSE"), "hash": hashlib.sha256(cls.canonical(record)).hexdigest()})
        if len(matches) > 1: raise ValueError("Multiple embedded chemistry records match this ligand")
        return matches[0] if matches else None

    @classmethod
    def session_inputs(cls):
        values = getattr(cmd._pymol.session, "energy_inputs", None)
        if values is None: return None
        if not isinstance(values, dict) or type(values.get("version")) is not int or values["version"] != 1: raise ValueError("Unsupported saved Energy inputs")
        bindings = cls.atom_bindings(values["receptor"], values["state"])
        if bindings != values["receptor_atoms"]: raise ValueError("Saved receptor atom bindings changed")
        atoms, _, _ = cls.atoms(values["receptor"], values["state"])
        anchors = {(binding["object"], binding["chain"], binding["segi"], binding["resi"], binding["resn"]): atom["residue"] for binding, atom in zip(bindings, atoms)}
        overrides = {}
        for record in values.get("overrides", []):
            anchor = tuple(record[key] for key in ("object", "chain", "segi", "resi", "resn"))
            if anchor not in anchors: raise ValueError("Saved residue override no longer matches receptor")
            overrides[anchors[anchor]] = record["template"]
        return {"receptor": values["receptor"], "ligand": values["ligand"], "state": values["state"],
                "pH": values.get("pH", 7.0), "overrides": overrides, "protonation": values.get("protonation")}

    @classmethod
    def remember_inputs(cls, receptor, ligand, state, sdf, mapping, smiles, ph, overrides, protein):
        bindings = cls.atom_bindings(ligand, state)
        objects = {atom["object"] for atom in bindings}
        if len(objects) != 1: raise ValueError("Ligand chemistry must belong to one molecular object")
        name = next(iter(objects))
        record = {"object": name, "state": state, "source": "RayMol Energy input", "smiles": smiles or None, "sdf": sdf,
                  "atoms": [{**{key: value for key, value in atom.items() if key != "object"}, "sdf_index": index} for atom, index in zip(bindings, mapping)]}
        existing = getattr(cmd._pymol.session, "ligand_chemistry", {"version": 1, "ligands": []})
        if not isinstance(existing, dict) or type(existing.get("version")) is not int or existing["version"] != 1: raise ValueError("Unsupported embedded ligand chemistry")
        selected = {atom["index"] for atom in bindings}
        records = [row for row in existing["ligands"] if not (row["object"] == name and row["state"] == state and {atom["index"] for atom in row["atoms"]} == selected)]
        receptor_bindings = cls.atom_bindings(receptor, state)
        saved_overrides = []
        for residue, template in overrides.items():
            atom = next((binding for binding, source in zip(receptor_bindings, protein) if source["residue"] == residue), None)
            if atom is None: raise ValueError("Residue override does not belong to the selected receptor")
            saved_overrides.append({**{key: atom[key] for key in ("object", "chain", "segi", "resi", "resn")}, "template": template})
        cmd._pymol.session.ligand_chemistry = {"version": 1, "ligands": records + [record]}
        cmd._pymol.session.energy_inputs = {"version": 1, "receptor": receptor, "ligand": ligand, "state": state,
                                           "receptor_atoms": receptor_bindings, "pH": ph, "overrides": saved_overrides}

    @classmethod
    def health(cls, analysis_id=None):
        result = {}
        for identifier, analysis in cls.analyses.items():
            if analysis_id and identifier != analysis_id: continue
            ledger = cls.reconcile(analysis)
            if not analysis["removed"] and not analysis.get("stale") and cls.source_current(analysis) != analysis["signature"]:
                analysis["stale"] = True
                analysis["accepts_events"] = False
                for leaf in analysis["owned"]: cls.replace_geometry(analysis, leaf, [], {})
            result[identifier] = {"removed": analysis["removed"], "stale": analysis.get("stale", False), "components": analysis["owned"],
                                  "needs_redraw": bool(analysis.get("visibility") is not None and analysis["visibility"] != {leaf: name in cmd.get_names("objects", enabled_only=1) for leaf, name in analysis["owned"].items()}),
                                  "states": {stage: "unavailable" if analysis.get("restore_error") else event["status"] for stage, event in analysis["stages"].items()},
                                  "summaries": {stage: event.get("summary", {}) for stage, event in analysis["stages"].items()},
                                  "parent": analysis.get("parent"), "error": analysis.get("restore_error"), "restored": not analysis.get("accepts_events", True)}
            result[identifier]["interruption"] = analysis.get("interruption")
            result[identifier]["visible"] = ledger.get(analysis.get("parent_token"), {}).get("enabled", False)
            result[identifier]["session_epoch"] = cls.epoch
            enabled = set(cmd.get_names("objects", enabled_only=1))
            result[identifier]["contact_counts"] = {leaf: {"shown": len(analysis["markers"].get(analysis["owned"].get(leaf), {})) if analysis["owned"].get(leaf) in enabled else 0, "eligible": record["eligible_count"]} for leaf, record in analysis.get("hidden", {}).items() if "displayed_count" in record}
        return result

    @staticmethod
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    @staticmethod
    def write(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = EnergyView.canonical(value)
        temporary = path.with_suffix(path.suffix + ".partial")
        temporary.write_bytes(data)
        temporary.replace(path)
        return {"path": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    @classmethod
    def atoms(cls, selection, state):
        if state < 1: raise ValueError("Choose an explicit positive coordinate state")
        indices = cmd.index(f"({selection}) and state {state}")
        if not indices: raise ValueError(f"Empty molecular selection: {selection}")
        model = cmd.get_model(selection, state)
        if len(model.atom) != len(indices): raise ValueError("Missing coordinates in selected state")
        native = {}
        for name in {name for name, _ in indices}:
            with cmd.lockcm:
                native[name] = {index: (uid, xyz) for index, uid, xyz in _cmd.energy_atom_records(cmd._COb, name, state - 1)}
        residues, records = {}, []
        for atom, (name, index) in zip(model.atom, indices):
            if index not in native[name]: raise ValueError("Incomplete requested coordinate state")
            uid, xyz = native[name][index]
            if atom.q <= 0: raise ValueError("Zero-occupancy input atom")
            if atom.alt: raise ValueError("Resolve alternate conformations before analysis")
            residue = (name, atom.segi, atom.chain, atom.resi, atom.resn)
            residues.setdefault(residue, []).append(uid)
            records.append({"source_id": f"{cls.epoch}/{uid}/{state}", "name": atom.name, "element": atom.symbol, "residue": residue, "resname": atom.resn, "chain": atom.chain, "resi": atom.resi, "xyz_nm": [v/10 for v in xyz]})
        for record in records:
            record["residue"] = f"{cls.epoch}/residue/{min(residues[record['residue']])}"
        bonds = [list(b.index) for b in model.bond]
        return records, bonds, indices

    @classmethod
    def ligand_input(cls, ligand, state):
        atoms, bonds, _ = cls.atoms(ligand, state)
        if len(atoms) > 100: raise ValueError("Select a ligand with at most 100 source atoms")
        return {"atoms": atoms, "bonds": bonds}

    @classmethod
    def input(cls, receptor, ligand, sdf_path, state, output, templates=None, mapping=None, confirmed=False, analysis_id=None, reviewed_hash=None, sdf_text=None, smiles=None, preparation_ph=None, protein_overrides=None):
        import numpy as np
        protein, protein_bonds, protein_indices = cls.atoms(receptor, state)
        ligand_atoms, _, ligand_indices = cls.atoms(ligand, state)
        if set(protein_indices) & set(ligand_indices): raise ValueError("Receptor and ligand overlap")
        combined_indices = cmd.index(f"(({receptor}) or ({ligand})) and state {state}")
        combined = cmd.get_model(f"({receptor}) or ({ligand})", state)
        protein_set, ligand_set = set(protein_indices), set(ligand_indices)
        for bond in combined.bond:
            a, b = (combined_indices[i] for i in bond.index)
            if (a in protein_set and b in ligand_set) or (b in protein_set and a in ligand_set):
                raise ValueError("Covalently connected protein-ligand complexes are unsupported")
        objects = {name for name, _ in protein_indices}
        chains = {(name, a["chain"]) for a, (name, _) in zip(protein, protein_indices)}
        standard = {"ALA", "ARG", "ASN", "ASP", "ASH", "CYS", "CYX", "CYM", "GLN", "GLU", "GLH", "GLY", "HIS", "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "LYN", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "ACE", "NME"}
        excluded = {}
        subsystem = protein_set | ligand_set
        molecular_objects = objects | {name for name, _ in ligand_indices}
        all_selection = " or ".join("%" + name for name in molecular_objects)
        graph_indices = cmd.index(f"({all_selection}) and state {state}")
        graph = cmd.get_model(all_selection, state)
        for bond in graph.bond:
            if sum(graph_indices[i] in subsystem for i in bond.index) == 1:
                raise ValueError("Selected subsystem is covalently connected to excluded atoms")
        for atom, identity in zip(graph.atom, graph_indices):
            if identity in protein_set or identity in ligand_set: continue
            if (identity[0], atom.chain) in chains and atom.resn in standard:
                raise ValueError("Select complete receptor chains; a protein residue/cap is missing")
            label = f"{identity[0]}/{atom.chain}/{atom.resn}{atom.resi}"
            excluded[label] = excluded.get(label, 0) + 1
        exclusions = [f"{label}: {count} atoms" for label, count in sorted(excluded.items())]
        sdf = sdf_text if sdf_text is not None else Path(sdf_path).read_text()
        if mapping is None:
            from chempy import io
            if "V3000" in sdf: raise ValueError("Supply explicit atom indices for V3000 SDF mapping")
            sdf_graph = io.mol.fromList(sdf.splitlines(keepends=True))
            mapping = []
            for atom in ligand_atoms:
                candidates = [i for i, candidate in enumerate(sdf_graph.atom) if candidate.symbol == atom["element"] and np.linalg.norm(np.asarray(candidate.coord)/10 - atom["xyz_nm"]) <= 0.0001]
                if len(candidates) != 1:
                    raise ValueError("SDF coordinates do not uniquely map to scene; supply explicit SDF atom indices")
                mapping.append(candidates[0])
            if len(set(mapping)) != len(mapping): raise ValueError("Ambiguous ligand mapping")
        if len(mapping) != len(ligand_atoms) or any(type(index) is not int or index < 0 for index in mapping) or len(set(mapping)) != len(mapping):
            raise ValueError("Explicit SDF indices must uniquely map every selected ligand atom")
        suggestions = {}
        external = set()
        for a, b in protein_bonds:
            if protein[a]["residue"] != protein[b]["residue"]:
                external.update([a, b])
        for atom in protein:
            residue = atom["residue"]
            if residue in suggestions: continue
            indices = [i for i, a in enumerate(protein) if a["residue"] == residue]
            base = atom["resname"]
            if base == "HIS": base = ""
            if base == "CYS" and any(i in external and protein[i]["name"] == "SG" for i in indices): base = "CYX"
            if base and base not in {"ACE", "NME"}:
                nterm = any(protein[i]["name"] == "N" and i not in external for i in indices)
                cterm = any(protein[i]["name"] == "C" and i not in external for i in indices)
                if nterm and cterm: raise ValueError("Uncapped single-residue chains are outside the supported templates")
                base = ("N" if nterm else "C" if cterm else "") + base
            suggestions[residue] = base
        bond_orders = [b.order for b in cmd.get_model(receptor, state).bond]
        source_models = list(cmd.get_model(receptor, state).atom) + list(cmd.get_model(ligand, state).atom)
        ligand_model = cmd.get_model(ligand, state)
        signature = cls.source_signature(protein + ligand_atoms, protein_bonds + [[a+len(protein), b+len(protein)] for a, b in [bond.index for bond in ligand_model.bond]], bond_orders + [bond.order for bond in ligand_model.bond], source_models)
        chemical_scope = {"source": {"atoms": {key: {field: value for field, value in atom.items() if field != "xyz_nm"} for key, atom in signature["atoms"].items()}, "bonds": signature["bonds"]},
                          "sdf": sdf, "mapping": mapping, "state": state, "epoch": cls.epoch,
                          "excluded_atoms": sorted([identity, atom.name, atom.symbol, atom.resn, atom.chain, atom.resi, atom.formal_charge, atom.q, atom.alt] for atom, identity in zip(graph.atom, graph_indices) if identity not in subsystem),
                          "excluded_bonds": sorted([*sorted(graph_indices[i] for i in bond.index), bond.order] for bond in graph.bond if any(graph_indices[i] not in subsystem for i in bond.index))}
        current_review_hash = hashlib.sha256(cls.canonical(chemical_scope)).hexdigest()
        if preparation_ph is not None:
            if not math.isfinite(preparation_ph) or not 0 <= preparation_ph <= 14: raise ValueError("Preparation pH must be between 0 and 14")
            chemical_scope.update(preparation_ph=preparation_ph, protein_overrides=protein_overrides or {}, smiles=smiles)
            current_review_hash = hashlib.sha256(cls.canonical(chemical_scope)).hexdigest()
        if confirmed and reviewed_hash != current_review_hash:
            raise ValueError("Chemistry or excluded groups changed since Review; review and confirm again")
        records = {"receptor": protein, "receptor_bonds": protein_bonds, "receptor_bond_orders": bond_orders, "ligand": ligand_atoms, "ligand_sdf": sdf, "ligand_map": mapping, "exclusions": exclusions, "state": state}
        revision = hashlib.sha256(cls.canonical({**records, "epoch": cls.epoch})).hexdigest()
        analysis_id = uuid.uuid4().hex if analysis_id is None else analysis_id
        if len(analysis_id) != 32 or any(c not in "0123456789abcdef" for c in analysis_id): raise ValueError("Invalid analysis ID")
        root = Path(output) / analysis_id
        snapshot = {"version": 1, "analysis_id": analysis_id, "scene_revision": revision, **records, "protein_templates": suggestions if templates is None else templates, "full_receptor_confirmed": confirmed, "chemistry_confirmed": confirmed, "preparation_recipe": "template-h-v1"}
        if preparation_ph is not None:
            snapshot.update(preparation_recipe="openmm-ph-v1", preparation_ph=preparation_ph, protein_overrides=protein_overrides or {}, ligand_smiles=smiles)
            revision = hashlib.sha256(cls.canonical({**snapshot, "epoch": cls.epoch})).hexdigest()
            snapshot["scene_revision"] = revision
        review = {"version": 1, "analysis_id": analysis_id, "scene_revision": revision, "directory": str(root), "templates": snapshot["protein_templates"], "exclusions": exclusions, "receptor_count": len(protein), "ligand_count": len(ligand_atoms), "ligand_map": mapping, "labels": {a["residue"]: f"{a['chain']}/{a['resname']}{a['resi']}" for a in protein}, "epoch": cls.epoch, "reviewed_hash": current_review_hash}
        if confirmed:
            if preparation_ph is None and any(not value for value in snapshot["protein_templates"].values()): raise ValueError("Unresolved histidine/protein template")
            review["snapshot"] = cls.write(root / "snapshot.json", snapshot)
            old = cls.analyses.get(analysis_id)
            if old and old["removed"]: raise ValueError("Removed analysis cannot be refreshed")
            if old:
                for leaf in old["owned"]: cls.replace_geometry(old, leaf, [], {})
            cls.analyses[analysis_id] = {"review": review, "snapshot": snapshot, "signature": signature, "accepts_events": True, "receptor": receptor, "ligand": ligand, "source_ids": [a["source_id"] for a in protein + ligand_atoms], "source_atoms": {a["source_id"]: copy.deepcopy(b) for a, b in zip(protein + ligand_atoms, source_models)}, "stages": {}, "owned": old["owned"] if old else {}, "tokens": old.get("tokens", {}) if old else {}, "markers": {}, "removed": False}
            if old and "parent" in old: cls.analyses[analysis_id]["parent"] = old["parent"]
            if old and "deleted_components" in old: cls.analyses[analysis_id]["deleted_components"] = old["deleted_components"]
            if old and "parent_token" in old: cls.analyses[analysis_id]["parent_token"] = old["parent_token"]
            if old and "group_tokens" in old: cls.analyses[analysis_id]["group_tokens"] = old["group_tokens"]
            if old and "mask" in old: cls.analyses[analysis_id]["mask"] = old["mask"]
            cls.source_current(cls.analyses[analysis_id])
        if preparation_ph is not None:
            cls.remember_inputs(receptor, ligand, state, sdf, mapping, smiles, preparation_ph, protein_overrides or {}, protein)
        cls.write(Path(output) / "input-review.json", review)
        return review

    @staticmethod
    def read_artifact(root, descriptor, array=False):
        root = Path(root).resolve()
        path = (root / descriptor["path"]).resolve()
        if not path.is_relative_to(root) or path == root: raise ValueError("Unsafe result path")
        data = path.read_bytes()
        if len(data) != descriptor["bytes"] or hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
            raise ValueError("Result artifact hash/length mismatch")
        if not array: return json.loads(data)
        import numpy as np
        values = np.load(path, allow_pickle=False, mmap_mode="r")
        if list(values.shape) != descriptor["shape"] or values.dtype.str != descriptor["dtype"] or values.dtype.kind not in "fiub" or not np.isfinite(values).all(): raise ValueError("Malformed result array")
        return values

    @classmethod
    def accept(cls, event):
        analysis = cls.analyses[event["analysis_id"]]
        if analysis["removed"]: raise ValueError("Analysis removed")
        cls.health(event["analysis_id"])
        if analysis.get("stale") or not analysis.get("accepts_events", True): raise ValueError("Stale or pre-recall result rejected")
        review = analysis["review"]
        if event["scene_revision"] != review["scene_revision"] or event["input_hash"] != review["snapshot"]["sha256"]:
            raise ValueError("Result refers to another input snapshot")
        stage = event["stage"]
        if event["status"] == "ready":
            root = review["directory"]
            if stage == "prepared":
                model = cls.read_artifact(root, event["artifacts"]["model"])
                coordinates = cls.read_artifact(root, event["artifacts"]["coordinates"], array=True)
                if model["endpoint_hash"] != event["endpoint_hash"] or model["model_hash"] != event["model_hash"]: raise ValueError("Prepared identity mismatch")
                analysis["model"], analysis["coordinates"] = model, coordinates
                saved = getattr(cmd._pymol.session, "energy_inputs", None)
                if saved is not None and saved["state"] == analysis["snapshot"]["state"] and saved["ligand"] == analysis["ligand"]:
                    saved["protonation"] = model["provenance"].get("protonation")
            elif event["endpoint_hash"] != analysis["model"]["endpoint_hash"] or event["model_hash"] != analysis["model"]["model_hash"]:
                raise ValueError("Endpoint/model identity changed between stages")
            for descriptor in event["artifacts"].values():
                cls.read_artifact(root, descriptor, array=descriptor["path"].endswith(".npy"))
        analysis["stages"][stage] = event
        return 1

    @classmethod
    def groups(cls, analysis):
        if analysis["owned"]:
            if all(leaf in analysis["owned"] for leaf in ("salt_bridges", "strong_contacts")): return
            ledger = cls.ledger()
            group = ledger.get(analysis["group_tokens"][0], {}).get("name")
            if group is None: return
            for leaf in ("salt_bridges", "strong_contacts"):
                if leaf in analysis["owned"]: continue
                name = group + "." + leaf
                if name in cmd.get_names("all"): raise ValueError("Analysis object-name collision")
                cmd.load_cgo([cgo.STOP], name, 1, zoom=0)
                cmd.group(group, name)
                cmd.disable(name)
                analysis["owned"][leaf] = name
                analysis["tokens"][leaf] = next(token for token, row in cls.ledger().items() if row["name"] == name)
            return
        base = "Energy_LIG_" + analysis["review"]["analysis_id"][:12]
        families = {"contacts": ["hbonds", "salt_bridges", "clashes", "strong_contacts", "electrostatics", "packing"], "strain": ["bonds", "angles", "proper_torsions", "improper_torsions", "nonbonded"], "solvation": ["favorable", "unfavorable"]}
        names = [base] + [base + "." + family for family in families] + [base + "." + family + "." + leaf for family, leaves in families.items() for leaf in leaves]
        if set(names) & set(cmd.get_names("all")): raise ValueError("Analysis object-name collision")
        cmd.group(base)
        for family, leaves in families.items():
            group = base + "." + family
            cmd.group(group)
            cmd.group(base, group)
            for leaf in leaves:
                name = group + "." + leaf
                cmd.load_cgo([cgo.STOP], name, 1, zoom=0)
                cmd.group(group, name)
                analysis["owned"][leaf] = name
                if leaf not in {"hbonds", "salt_bridges", "clashes", "strong_contacts"}: cmd.disable(name)
        analysis["parent"] = base
        names_by_id = {value["name"]: token for token, value in cls.ledger().items()}
        analysis["tokens"] = {leaf: names_by_id[name] for leaf, name in analysis["owned"].items()}
        analysis["parent_token"] = names_by_id[base]
        analysis["group_tokens"] = [names_by_id[base + "." + family] for family in families]

    @staticmethod
    def color(value, scale):
        fraction = min(abs(value)/scale, 1)
        target = [0.08, 0.65, 0.58] if value < 0 else [0.86, 0.18, 0.34]
        return [neutral*(1-fraction)+tint*fraction for neutral, tint in zip([0.56, 0.57, 0.58], target)]

    @staticmethod
    def cylinder(start, end, color, radius=0.05):
        return [cgo.CYLINDER, *start, *end, radius, *color, *color]

    @classmethod
    def dashes(cls, start, end, color, step=0.28):
        distance = math.dist(start, end)
        count = max(2, int(math.ceil(distance/step)))
        values = []
        for i in range(0, count, 2):
            values += cls.cylinder(start + (end-start)*i/count, start + (end-start)*min(i+1, count)/count, color)
        return values

    @classmethod
    def replace_geometry(cls, analysis, leaf, data, markers):
        cls.reconcile(analysis)
        if analysis["removed"]: return
        name = analysis["owned"][leaf]
        if name not in cmd.get_names("objects"):
            analysis.setdefault("deleted_components", set()).add(leaf)
            return
        if leaf in analysis.get("deleted_components", set()): return
        enabled = name in cmd.get_names("objects", enabled_only=1)
        cmd.load_cgo(data + [cgo.STOP], name, 1, zoom=0)
        if not enabled: cmd.disable(name)
        analysis["markers"][name] = markers

    @classmethod
    def hbonds(cls, analysis):
        import numpy as np
        from chempy import Atom, Bond
        from chempy.models import Indexed
        model, xyz = analysis["model"], analysis["coordinates"] * 10
        names, index_map = [], {}
        try:
            for partner, indices in [("P", range(model["nprotein"])), ("L", range(model["nprotein"], len(model["particles"])) )]:
                name = "_energy_prepared_" + uuid.uuid4().hex
                names.append(name)
                owned = Indexed()
                mapping = {original: local for local, original in enumerate(indices)}
                for i in indices:
                    p = model["particles"][i]
                    atom = copy.deepcopy(analysis["source_atoms"][p["source_id"]]) if p["source_id"] in analysis["source_atoms"] else Atom()
                    atom.name, atom.symbol, atom.resn, atom.resi, atom.chain = p["name"], p["element"], p["resname"], p["resi"], p["chain"]
                    atom.coord, atom.id = xyz[i].tolist(), i+1
                    if "formal_charge" in p: atom.formal_charge = p["formal_charge"]
                    owned.atom.append(atom)
                for term in model["bonds"]:
                    if all(i in mapping for i in term["atoms"]):
                        bond = Bond()
                        bond.index, bond.order = [mapping[i] for i in term["atoms"]], term["order"]
                        owned.bond.append(bond)
                cmd.load_model(owned, name, 1, zoom=0)
                cmd.disable(name)
                # ChemPy IDs are ignored by default; rank retains input order through sorting.
                ranks = {}
                cmd.iterate("%" + name, "ranks[index] = rank", space={"ranks": ranks})
                original = tuple(indices)
                if sorted(ranks.values()) != list(range(len(original))): raise ValueError("Prepared classifier copy mapping is not bijective")
                copied = cmd.get_model(name, 1)
                for atom in copied.atom:
                    particle = original[ranks[atom.index]]
                    if atom.symbol != model["particles"][particle]["element"] or not np.array_equal(np.asarray(atom.coord, dtype=np.float32), xyz[particle].astype(np.float32)):
                        raise ValueError("Prepared classifier copy differs from mapped endpoint")
                    index_map[(name, atom.index)] = particle
                actual_bonds = sorted((tuple(sorted(original[ranks[copied.atom[i].index]] for i in bond.index)), bond.order) for bond in copied.bond)
                expected_bonds = sorted((tuple(sorted(term["atoms"])), term["order"]) for term in model["bonds"] if all(i in mapping for i in term["atoms"]))
                if actual_bonds != expected_bonds: raise ValueError("Prepared classifier copy changed bond graph or orders")
            with cmd.lockcm:
                result = _cmd.hbond_records(cmd._COb, names[0], names[1], 0, 0, 3.6)
            records = []
            for record in result["records"]:
                roles = {role: index_map[(record[role][0], record[role][1])] for role in ("donor", "hydrogen", "acceptor")}
                d, h, a = (roles[role] for role in ("donor", "hydrogen", "acceptor"))
                angle = cls.angle(xyz[d]-xyz[h], xyz[a]-xyz[h])
                records.append({**roles, "label": "Geometric H-bond", "distance_angstrom": float(np.linalg.norm(xyz[d]-xyz[a])), "D_H_A_degrees": math.degrees(angle), "classifier": {k: result[k] for k in ("criteria", "cutoff", "exclusion")}, "endpoint_hash": model["endpoint_hash"]})
            return records
        finally:
            for name in names: cmd.delete(name)

    @staticmethod
    def angle(a, b):
        import numpy as np
        return math.atan2(float(np.linalg.norm(np.cross(a, b))), float(np.dot(a, b)))

    @staticmethod
    def ionic_groups(model):
        particles, n = model["particles"], model["nprotein"]
        residues = {}
        for i, atom in enumerate(particles[:n]): residues.setdefault(atom["residue"], {})[atom["name"]] = i
        groups = []
        charged = {"ASP": (-1, ["OD1", "OD2"]), "GLU": (-1, ["OE1", "OE2"]),
                   "LYS": (1, ["NZ"]), "ARG": (1, ["NE", "NH1", "NH2"]), "HIP": (1, ["ND1", "NE2"])}
        for residue, atoms in residues.items():
            template = model["provenance"]["templates"][residue]
            terminal = len(template) == 4 and template[0] in "NC"
            name = template[1:] if terminal else template
            if name in charged:
                sign, names = charged[name]
                groups.append({"atoms": [atoms[key] for key in names], "charge": sign})
            if terminal: groups.append({"atoms": [atoms[key] for key in (["N"] if template[0] == "N" else ["O", "OXT"])], "charge": 1 if template[0] == "N" else -1})
        neighbors = {i: {} for i in range(n, len(particles))}
        for bond in model["bonds"]:
            a, b = bond["atoms"]
            if a >= n and b >= n:
                neighbors[a][b] = neighbors[b][a] = bond["order"]
        ligand_groups = []
        for i in neighbors:
            charge = particles[i]["formal_charge"]
            if not charge: continue
            atoms = {i}
            atoms.update(j for j in neighbors[i] if particles[j]["formal_charge"])
            for center, order in neighbors[i].items():
                atoms.update(j for j, other_order in neighbors[center].items() if particles[j]["formal_charge"] and (order >= 2 or other_order >= 2))
            # Extend terminal charge centers over explicit common resonance groups.
            heavy = [j for j in neighbors[i] if particles[j]["element"] != "H"]
            if len(heavy) == 1:
                center = heavy[0]
                element = particles[i]["element"]
                resonant = particles[center]["element"] in {"P", "S"} or any(order == 2 and particles[j]["element"] == element for j, order in neighbors[center].items())
                if resonant:
                    atoms.update(j for j in neighbors[center] if particles[j]["element"] == element and sum(particles[k]["element"] != "H" for k in neighbors[j]) == 1)
            ligand_groups.append(atoms)
        while ligand_groups:
            atoms = ligand_groups.pop(0)
            while any(atoms & other for other in ligand_groups):
                overlapping = [other for other in ligand_groups if atoms & other]
                atoms.update(set().union(*overlapping))
                ligand_groups = [other for other in ligand_groups if other not in overlapping]
            net = sum(particles[j]["formal_charge"] for j in atoms)
            if net: groups.append({"atoms": sorted(atoms), "charge": net})
        return groups

    @classmethod
    def selective_contacts(cls, model, xyz, pairs, hbonds, threshold, pocket, cap):
        import numpy as np
        n, particles = model["nprotein"], model["particles"]
        width = len(particles)-n
        lj = pairs[:, :, 1]+pairs[:, :, 2]
        distances = np.linalg.norm(xyz[:n, None]-xyz[None, n:], axis=2)
        parent = [atom["parent"] if atom["element"] == "H" else i for i, atom in enumerate(particles)]
        hbond_pairs = {tuple(sorted((record["donor"], record["acceptor"]))) for record in hbonds}
        clashes = {}
        for p, local in np.argwhere((lj >= max(1.0, threshold)) & (distances <= pocket)):
            p, l = int(p), n+int(local)
            key = (parent[p], parent[l])
            if key in hbond_pairs:
                sigma = (particles[key[0]]["sigma_nm"]+particles[key[1]]["sigma_nm"])/2
                if np.linalg.norm(xyz[key[0]]-xyz[key[1]]) >= 0.75*2**(1/6)*sigma*10: continue
            pair = p*width+l-n
            if key not in clashes or lj[p, l-n] > lj[clashes[key]//width, clashes[key]%width]: clashes[key] = pair
        clash_ids = sorted(clashes.values(), key=lambda pair: (-lj.ravel()[pair], pair))
        clash_groups = set(clashes)
        ionic = cls.ionic_groups(model)
        salts = []
        for left in ionic:
            if left["atoms"][0] >= n: continue
            for right in ionic:
                if right["atoms"][0] < n or left["charge"]*right["charge"] >= 0: continue
                candidates = [(distances[p, l-n], p, l) for p in left["atoms"] for l in right["atoms"]]
                distance, p, l = min(candidates)
                if distance <= min(4.0, pocket) and (p, l) not in clash_groups:
                    salts.append({"atoms": [p, l], "pair_id": p*width+l-n, "group_atoms": [left["atoms"], right["atoms"]], "formal_charges": [left["charge"], right["charge"]], "distance_angstrom": float(distance)})
        salts.sort(key=lambda record: (record["distance_angstrom"], record["pair_id"]))
        represented = {particles[parent[p]]["residue"] for p, l in clash_groups}
        represented.update(particles[record["atoms"][0]]["residue"] for record in salts)
        represented.update(particles[min(record["donor"], record["acceptor"])]["residue"] for record in hbonds)
        residues = {}
        for i, atom in enumerate(particles[:n]): residues.setdefault(atom["residue"], []).append(i)
        packing, residue_totals = [], {}
        ligand_heavy = [i-n for i, atom in enumerate(particles[n:], n) if atom["element"] != "H"]
        for residue, atoms in residues.items():
            values = pairs[atoms].sum(axis=(0, 1))
            residue_totals[residue] = {"residue_coulomb_kcal": float(values[0]), "residue_net_lj_kcal": float(values[1]+values[2])}
            if values[1]+values[2] > -max(1.0, threshold) or residue in represented: continue
            candidates = [(distances[p, local], p, n+local) for p in atoms if particles[p]["element"] != "H" for local in ligand_heavy]
            distance, p, l = min(candidates)
            if distance <= min(4.5, pocket): packing.append({"atoms": [p, l], "pair_id": p*width+l-n, "residue": residue, **residue_totals[residue], "distance_angstrom": float(distance)})
        packing.sort(key=lambda record: (record["residue_net_lj_kcal"], record["pair_id"]))
        limit = min(cap, 24)
        return {"clashes": clash_ids[:limit], "salt_bridges": salts[:limit], "strong_contacts": packing[:min(cap, 8)],
                "eligible": {"clashes": len(clash_ids), "salt_bridges": len(salts), "strong_contacts": len(packing)}, "residue_totals": residue_totals}

    @staticmethod
    def group_strain(records):
        groups = {}
        for record in records:
            if record["kind"] != "torsion":
                key = (record["kind"], tuple(record["atoms"]))
            elif record["family"] == "ImproperTorsions":
                key = (record["family"], record["improper_center"], tuple(sorted(record["atoms"])), record["parameter_id"])
            else:
                atoms = tuple(record["atoms"])
                key = (record["family"], min(atoms, atoms[::-1]), record["parameter_id"])
            if key not in groups:
                groups[key] = {**record, "terms": [], "bound_kj": 0.0, "reference_kj": 0.0, "delta_kj": 0.0}
            group = groups[key]
            group["terms"].append(record)
            for field in ("bound_kj", "reference_kj", "delta_kj"): group[field] += record[field]
        return list(groups.values())

    @classmethod
    def query(cls, analysis_id, selection=""):
        import numpy as np
        analysis = cls.analyses[analysis_id]
        cls.health(analysis_id)
        if analysis["removed"] or analysis.get("stale"): raise ValueError("Stale or unavailable analysis")
        if analysis["stages"].get("direct", {}).get("status") != "ready": raise ValueError("Direct values unavailable")
        model, n = analysis["model"], analysis["model"]["nprotein"]
        pairs = cls.read_artifact(analysis["review"]["directory"], analysis["stages"]["direct"]["artifacts"]["pairs"], array=True)/4.184
        if pairs.shape != (n, len(model["particles"])-n, 3): raise ValueError("Direct array shape mismatch")
        residues = {}
        for i, particle in enumerate(model["particles"][:n]):
            residue = particle["residue"]
            residues.setdefault(residue, np.zeros(3))
            residues[residue] += pairs[i].sum(axis=0)
        total = pairs.sum(axis=(0, 1))
        if np.any(np.abs(sum(residues.values()) - total) > 1e-6 + 1e-10*np.abs(total)): raise ValueError("Residue reduction mismatch")
        result = {"units": "kcal/mol", "total": total.tolist(), "channels": ["coulomb", "attraction", "repulsion"], "residues": {r: v.tolist() for r, v in residues.items()}}
        if selection:
            selected = set(cmd.index(selection))
            ids = {source for source, locator in analysis["locators"].items() if tuple(locator) in selected}
            indices = [p["source_id"] in ids or (p.get("parent") is not None and model["particles"][p["parent"]]["source_id"] in ids) for p in model["particles"]]
            mask = np.asarray(indices[:n])[:, None] | np.asarray(indices[n:])[None, :]
            values = pairs[mask].sum(axis=0)
            result.update(selected=values.tolist(), remainder=(total-values).tolist(), selected_pair_count=int(mask.sum()), selection=selection)
        return result

    @classmethod
    def master(cls, analysis_id, visible):
        analysis = cls.analyses[analysis_id]
        ledger = cls.reconcile(analysis)
        if analysis["removed"]: raise ValueError("Analysis group removed")
        if not visible:
            if "mask" in analysis: return True
            owned = set(analysis["tokens"].values()) | set(analysis.get("group_tokens", [])) | {analysis["parent_token"]}
            mask = {token: ledger[token]["enabled"] for token in owned if token in ledger}
            cmd.disable(analysis["parent"])
            analysis["mask"] = {"values": mask, "expected": cls.ledger()}
        elif "mask" in analysis:
            mask = analysis.pop("mask")["values"]
            for token, enabled in mask.items():
                if token in ledger: (cmd.enable if enabled else cmd.disable)(ledger[token]["name"])
        else:
            cmd.enable(analysis["parent"])
        return visible

    @classmethod
    def remove(cls, analysis_id):
        analysis = cls.analyses[analysis_id]
        ledger = cls.reconcile(analysis)
        # Delete only the recorded incarnations, never external group members.
        tokens = set(analysis.get("tokens", {}).values()) | set(analysis.get("group_tokens", [])) | {analysis.get("parent_token")}
        for token in tokens:
            if token in ledger: cmd.delete(ledger[token]["name"])
        analysis["removed"] = True
        analysis["accepts_events"] = False
        analysis["markers"].clear()
        for key in ("coordinates", "model", "source_atoms", "hbonds", "mask"): analysis.pop(key, None)
        return True

    @classmethod
    def interrupt(cls, analysis_id, epoch, reason):
        if epoch != cls.epoch: return False
        analysis = cls.analyses[analysis_id]
        if analysis["removed"]: return False
        analysis["interruption"] = reason
        analysis["accepts_events"] = False
        for stage in ("prepared", "direct", "global_solvation", "local_solvation", "strain"):
            previous = analysis["stages"].get(stage, {})
            if previous.get("status") in {"ready", "failed", "unavailable"}: continue
            analysis["stages"][stage] = {**previous, "status": "unavailable", "artifacts": {}, "summary": {}, "message": reason}
        if analysis.get("model"): cls.render(analysis_id, **analysis.get("display", {}))
        return {stage: event["status"] for stage, event in analysis["stages"].items()}

    @classmethod
    def session_save(cls, session, **kwargs):
        cls.health()
        saved = []
        for analysis in cls.analyses.values():
            if analysis["removed"] or not analysis.get("model"): continue
            cls.refresh_saved_input_names(analysis, session)
            review = analysis["review"]
            relative = review["analysis_id"] + "-" + review["snapshot"]["sha256"][:16]
            descriptors = [review["snapshot"]] + [d for event in analysis["stages"].values() for d in event.get("artifacts", {}).values()]
            embedded = {}
            for descriptor in descriptors:
                cls.read_artifact(review["directory"], descriptor, array=descriptor["path"].endswith(".npy"))
                embedded[descriptor["path"]] = (Path(review["directory"]) / descriptor["path"]).read_bytes()
            metadata = {key: analysis[key] for key in ("signature", "locators", "stages", "owned", "parent", "display", "hbonds") if key in analysis}
            metadata["deleted_components"] = sorted(analysis.get("deleted_components", set()))
            metadata["stale"] = analysis.get("stale", False)
            metadata["interruption"] = analysis.get("interruption")
            metadata["review"] = {**review, "directory": relative}
            cls.canonical(metadata)
            metadata["embedded_artifacts"] = embedded
            saved.append(metadata)
        session["raymol_energy"] = {"version": 2, "analyses": saved}
        return 1

    @classmethod
    def refresh_saved_input_names(cls, analysis, session):
        values = getattr(cmd._pymol.session, "energy_inputs", None)
        chemistry = getattr(cmd._pymol.session, "ligand_chemistry", None)
        if not values or not chemistry or analysis.get("stale"): return
        snapshot = analysis["snapshot"]
        if any(values.get(key) != analysis.get(key) for key in ("receptor", "ligand")) or values["state"] != snapshot["state"] or values["pH"] != snapshot.get("preparation_ph"): return
        names, bindings = {}, {}
        for key in ("receptor", "ligand"):
            locators = [analysis["locators"][atom["source_id"]] for atom in snapshot[key]]
            objects = {name for name, _ in locators}
            if len(objects) != 1: return
            name = next(iter(objects))
            expected = {tuple(locator) for locator in locators}
            selected = values[key] if values[key] in cmd.get_names("all") else name
            if set(cmd.index(f"({selected}) and state {snapshot['state']}")) != expected: return
            names[key] = selected
            current = {(a["object"], a["index"]): a for a in cls.atom_bindings(selected, snapshot["state"])}
            bindings[key] = [current[tuple(locator)] for locator in locators]
        if names == {key: values[key] for key in names}: return
        records = [record for record in chemistry["ligands"] if record["object"] == analysis["ligand"] and record["state"] == snapshot["state"] and record.get("sdf") == snapshot["ligand_sdf"]]
        if len(records) != 1: return
        record = records[0]
        if [a.get("sdf_index") for a in record["atoms"]] != snapshot["ligand_map"]: return
        record["object"] = bindings["ligand"][0]["object"]
        record["atoms"] = [{**{k: v for k, v in binding.items() if k != "object"}, "sdf_index": index}
                           for binding, index in zip(bindings["ligand"], snapshot["ligand_map"])]
        for override in values["overrides"]:
            old = next(a for a in values["receptor_atoms"] if all(a[k] == override[k] for k in ("object", "chain", "segi", "resi", "resn")))
            override["object"] = bindings["receptor"][values["receptor_atoms"].index(old)]["object"]
        values.update(**names, receptor_atoms=bindings["receptor"])
        if "session" in session:
            session["session"].energy_inputs = copy.deepcopy(values)
            session["session"].ligand_chemistry = copy.deepcopy(chemistry)

    @classmethod
    def session_restore(cls, session, **kwargs):
        cls.epoch = uuid.uuid4().hex
        cls.analyses = {}
        payload = session.get("raymol_energy", {"version": 1, "analyses": []})
        if type(payload.get("version")) is not int or payload["version"] not in {1, 2} or len(payload["analyses"]) > 100: raise ValueError("Unsupported energy session")
        ledger = cls.ledger()
        by_name = {record["name"]: token for token, record in ledger.items()}
        for metadata in payload["analyses"]:
            analysis = copy.deepcopy(metadata)
            embedded = analysis.pop("embedded_artifacts", None)
            cls.canonical(analysis)
            review = analysis["review"]
            root = (cls.session_root / review["directory"]).resolve()
            review["directory"] = str(root)
            analysis.update(removed=False, accepts_events=False, markers={}, tokens={leaf: by_name[name] for leaf, name in analysis["owned"].items() if name in by_name}, deleted_components=set(analysis.get("deleted_components", [])))
            analysis["deleted_components"].update(set(analysis["owned"]) - set(analysis["tokens"]))
            analysis["parent_token"] = by_name.get(analysis["parent"])
            analysis["group_tokens"] = [token for token, row in ledger.items() if row["parent"] == analysis["parent"] and token not in analysis["tokens"].values()]
            cls.analyses[review["analysis_id"]] = analysis
            try:
                if not root.is_relative_to(cls.session_root.resolve()) or root == cls.session_root.resolve(): raise ValueError("Unsafe energy session directory")
                if payload["version"] == 2:
                    relative = review["analysis_id"] + "-" + review["snapshot"]["sha256"][:16]
                    if root != (cls.session_root / relative).resolve(): raise ValueError("Energy session directory identity mismatch")
                    descriptors = [review["snapshot"]] + [d for event in analysis["stages"].values() for d in event.get("artifacts", {}).values()]
                    if not isinstance(embedded, dict): raise ValueError("Missing embedded Energy artifacts")
                    targets = []
                    for descriptor in descriptors:
                        target = (root / descriptor["path"]).resolve()
                        if not target.is_relative_to(root) or target == root: raise ValueError("Unsafe embedded artifact path")
                        data = embedded.get(descriptor["path"])
                        if type(data) is not bytes or len(data) != descriptor["bytes"] or hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
                            raise ValueError("Embedded artifact missing or hash/length mismatch")
                        targets.append((target, data))
                    root.mkdir(parents=True, exist_ok=True)
                    for target, data in targets:
                        temporary = target.with_suffix(target.suffix + ".partial")
                        temporary.write_bytes(data); temporary.replace(target)
                analysis["snapshot"] = cls.read_artifact(root, review["snapshot"])
                for event in analysis["stages"].values():
                    for descriptor in event.get("artifacts", {}).values(): cls.read_artifact(root, descriptor, array=descriptor["path"].endswith(".npy"))
                prepared = analysis["stages"]["prepared"]["artifacts"]
                analysis["model"] = cls.read_artifact(root, prepared["model"])
                analysis["coordinates"] = cls.read_artifact(root, prepared["coordinates"], array=True)
                if analysis["coordinates"].shape != (len(analysis["model"]["particles"]), 3): raise ValueError("Prepared shape mismatch")
                mapping = {}
                for source, (name, index) in analysis["locators"].items():
                    with cmd.lockcm:
                        records = {i: uid for i, uid, xyz in _cmd.energy_atom_records(cmd._COb, name, analysis["snapshot"]["state"]-1)}
                    if index not in records: raise ValueError("Source mapping missing after recall")
                    mapping[source] = records[index]
                analysis["uid_map"] = mapping
                cls.health(review["analysis_id"])
                if not analysis.get("stale"): cls.render(review["analysis_id"], **analysis.get("display", {}))
            except Exception as error:
                analysis.update(stale=True, restore_error=str(error))
                for leaf in analysis["owned"]: cls.replace_geometry(analysis, leaf, [], {})
        return 1

    @classmethod
    def render(cls, analysis_id, threshold=0.1, pocket=8.0, scale=10.0, channel="total", contact_cap=200, dot_cap=5000):
        import numpy as np
        if not all(math.isfinite(value) for value in (threshold, pocket, scale)) or threshold < 0 or pocket <= 0 or scale <= 0 or channel not in {"total", "polar", "ace"} or not 1 <= contact_cap <= 2000 or not 1 <= dot_cap <= 10000:
            raise ValueError("Invalid display controls")
        analysis = cls.analyses[analysis_id]
        cls.health(analysis_id)
        if analysis["removed"] or analysis.get("stale"): return {"stale": True}
        cls.groups(analysis)
        stages, root, model = analysis["stages"], analysis["review"]["directory"], analysis["model"]
        xyz = analysis["coordinates"] * 10
        n = model["nprotein"]
        geometry = {leaf: [] for leaf in analysis["owned"]}
        markers = {leaf: {} for leaf in analysis["owned"]}
        hidden = {}
        if stages.get("direct", {}).get("status") == "ready":
            pairs = cls.read_artifact(root, stages["direct"]["artifacts"]["pairs"], array=True)/4.184
            if pairs.shape != (n, len(xyz)-n, 3): raise ValueError("Direct array shape mismatch")
            distances = np.linalg.norm(xyz[:n, None] - xyz[None, n:], axis=2)
            if "hbonds" not in analysis: analysis["hbonds"] = cls.hbonds(analysis)
            hbonds = {}
            for record in sorted(analysis["hbonds"], key=lambda record: (record["distance_angstrom"], -record["D_H_A_degrees"], record["donor"], record["acceptor"])):
                if record["distance_angstrom"] <= pocket: hbonds.setdefault(tuple(sorted((record["donor"], record["acceptor"]))), record)
            selected = cls.selective_contacts(model, xyz, pairs, list(hbonds.values()), threshold, pocket, contact_cap)
            clash_mask = np.zeros(pairs.shape[:2], dtype=bool)
            clash_mask.ravel()[selected["clashes"]] = True
            enabled = set(cmd.get_names("objects", enabled_only=1))
            for leaf, values, condition in [("electrostatics", pairs[:, :, 0], np.ones(pairs.shape[:2], dtype=bool)), ("packing", pairs[:, :, 1]+pairs[:, :, 2], pairs[:, :, 1]+pairs[:, :, 2] < 0), ("clashes", pairs[:, :, 1]+pairs[:, :, 2], pairs[:, :, 1]+pairs[:, :, 2] > 0)]:
                if leaf == "clashes": condition = clash_mask
                flat = values.ravel()
                outside = distances.ravel() > pocket
                below = ~outside & ((np.abs(flat) < threshold) | ~condition.ravel())
                eligible = np.flatnonzero(~outside & ~below)
                ranked = eligible[np.argsort(-np.abs(flat[eligible]), kind="stable")]
                limit = contact_cap if leaf == "electrostatics" else dot_cap
                if leaf in {"electrostatics", "packing"} and analysis["owned"][leaf] not in enabled: limit = 0
                visible, capped = ranked[:limit], ranked[limit:]
                partition = {"displayed": float(flat[visible].sum()), "outside_pocket": float(flat[outside].sum()), "excluded_by_contact_filter": float(flat[below].sum()), "marker_budget": float(flat[capped].sum()), "total": float(flat.sum())}
                if abs(sum(partition[k] for k in partition if k != "total") - partition["total"]) > 1e-6 + 1e-10*abs(partition["total"]): raise ValueError("Display partition does not reconcile")
                hidden[leaf] = partition
                for pair in visible:
                    p, local = divmod(int(pair), len(xyz)-n)
                    l, token, value = n+local, int(pair)+1, float(flat[pair])
                    color = [0.86, 0.18, 0.34] if leaf == "clashes" else cls.color(value, scale)
                    geometry[leaf] += [cgo.PICK_COLOR, token, cgo.cPickableGadget]
                    if leaf == "electrostatics": geometry[leaf] += cls.dashes(xyz[p], xyz[l], color)
                    else:
                        midpoint = (xyz[p]+xyz[l])/2
                        geometry[leaf] += [cgo.COLOR, *color, cgo.SPHERE, *midpoint, 0.11 if leaf == "packing" else 0.19]
                        if leaf == "clashes": geometry[leaf] += cls.dashes(xyz[p], xyz[l], color, step=0.5)
                    markers[leaf][token] = {"kind": leaf, "atoms": [p, l], "pair_id": int(pair), "coulomb_kcal": float(pairs[p, local, 0]), "attraction_kcal": float(pairs[p, local, 1]), "repulsion_kcal": float(pairs[p, local, 2]), "net_lj_kcal": float(pairs[p, local, 1]+pairs[p, local, 2]), "distance_angstrom": float(distances[p, local]), "saturated": abs(value) > scale}
            for leaf in ("salt_bridges", "strong_contacts"):
                if leaf not in geometry: continue
                for record in selected[leaf]:
                    p, l = record["atoms"]
                    token, local = record["pair_id"]+1, l-n
                    color = [0.14, 0.4, 0.85] if leaf == "salt_bridges" else [0.08, 0.65, 0.38]
                    geometry[leaf] += [cgo.PICK_COLOR, token, cgo.cPickableGadget] + cls.dashes(xyz[p], xyz[l], color, step=0.5)
                    markers[leaf][token] = {"kind": leaf, **record, "coulomb_kcal": float(pairs[p, local, 0]), "net_lj_kcal": float(pairs[p, local, 1]+pairs[p, local, 2])}
            for record in list(hbonds.values())[:min(contact_cap, 24)]:
                p, l = sorted([record["donor"], record["acceptor"]])
                if p >= n or l < n: raise ValueError("Classifier returned a within-partner H-bond")
                local, pair = l-n, p*(len(xyz)-n)+(l-n)
                detail = {"hbond": record, "pair_id": pair}
                clash = next((marker for marker in markers["clashes"].values() if [model["particles"][i]["parent"] if model["particles"][i]["element"] == "H" else i for i in marker["atoms"]] == [p, l]), None)
                salt = next((marker for marker in markers.get("salt_bridges", {}).values() if p in marker["group_atoms"][0] and l in marker["group_atoms"][1]), None)
                merged = clash if clash is not None and analysis["owned"]["clashes"] in enabled else salt if salt is not None and analysis["owned"]["salt_bridges"] in enabled else None
                if merged is not None:
                    merged.update(hbond=record, D_H_A_degrees=record["D_H_A_degrees"], hbond_distance_angstrom=record["distance_angstrom"])
                    continue
                token = pair+1
                geometry["hbonds"] += [cgo.PICK_COLOR, token, cgo.cPickableGadget] + cls.dashes(xyz[record["donor"]], xyz[record["acceptor"]], [0.86, 0.64, 0.08], step=0.5)
                markers["hbonds"][token] = {"kind": "hbonds", **detail, **record, "coulomb_kcal": float(pairs[p, local, 0]), "net_lj_kcal": float(pairs[p, local, 1]+pairs[p, local, 2])}
            for leaf in ("hbonds", "salt_bridges", "clashes", "strong_contacts"):
                hidden.setdefault(leaf, {})["eligible_count"] = len(hbonds) if leaf == "hbonds" else selected["eligible"][leaf]
                hidden[leaf]["displayed_count"] = len(markers.get(leaf, {}))
                for marker in markers.get(leaf, {}).values():
                    atom = marker.get("donor", marker.get("atoms", [0])[0])
                    if atom >= n: atom = marker["acceptor"]
                    marker.update(selected["residue_totals"][model["particles"][atom]["residue"]])
        if stages.get("strain", {}).get("status") == "ready":
            strain = cls.read_artifact(root, stages["strain"]["artifacts"]["strain"])
            bound = np.asarray(strain["bound_nm"], dtype="<f8")
            reference = np.asarray(strain["reference_nm"], dtype="<f8")
            endpoint_hashes = {"bound_endpoint_hash": hashlib.sha256(bound.tobytes()).hexdigest(),
                               "reference_endpoint_hash": hashlib.sha256(reference.tobytes()).hexdigest(),
                               "strain_artifact_hash": stages["strain"]["artifacts"]["strain"]["sha256"]}
            strain_xyz = np.vstack([xyz[:n], bound*10])
            strain_displayed = 0.0
            for token, record in enumerate(cls.group_strain(strain["records"]), 1):
                value = record["delta_kj"]/4.184
                if abs(value) < threshold: continue
                atoms, kind = record["atoms"], record["kind"]
                leaf = {"bond": "bonds", "angle": "angles", "nonbonded": "nonbonded"}.get(kind)
                if kind == "torsion": leaf = "improper_torsions" if record["family"] == "ImproperTorsions" else "proper_torsions"
                if len(markers[leaf]) >= dot_cap: continue
                color = cls.color(value, scale)
                geometry[leaf] += [cgo.PICK_COLOR, token, cgo.cPickableGadget]
                points = strain_xyz[atoms]
                if leaf == "bonds": geometry[leaf] += cls.cylinder(points[0], points[1], color, radius=0.16)
                elif leaf == "angles":
                    a, b = points[0]-points[1], points[2]-points[1]
                    a, b = a/np.linalg.norm(a), b/np.linalg.norm(b)
                    arc = []
                    for fraction in np.linspace(0, 1, 17):
                        direction = (1-fraction)*a + fraction*b
                        if np.linalg.norm(direction) <= 1e-8: raise ValueError("Degenerate angle glyph")
                        arc.append(points[1] + 0.6*direction/np.linalg.norm(direction))
                    for start, end in zip(arc, arc[1:]): geometry[leaf] += cls.cylinder(start, end, color)
                elif leaf in {"proper_torsions", "improper_torsions"}:
                    if leaf == "improper_torsions":
                        center = strain_xyz[record["improper_center"]]
                        for atom in atoms:
                            if atom != record["improper_center"]: geometry[leaf] += cls.cylinder(center, strain_xyz[atom], color)
                    else:
                        midpoint = (points[1]+points[2])/2
                        for start, end in [(points[0], midpoint), (midpoint, points[3]), (points[3], points[0])]: geometry[leaf] += cls.cylinder(start, end, color)
                else:
                    geometry[leaf] += [cgo.COLOR, *color, cgo.SPHERE, *((points[0]+points[1])/2), 0.13]
                markers[leaf][token] = {"kind": leaf, **record, **endpoint_hashes, "delta_kcal": value, "saturated": abs(value) > scale, "endpoint": "isolated H-only bound vs local vacuum reference"}
                strain_displayed += value
            hidden["strain"] = {"displayed": strain_displayed, "hidden": strain["local_strain_kj"]/4.184-strain_displayed, "total": strain["local_strain_kj"]/4.184}
        if stages.get("local_solvation", {}).get("status") == "ready":
            allocation = cls.read_artifact(root, stages["local_solvation"]["artifacts"]["heavy"], array=True)/4.184
            if allocation.shape != (len(xyz), 2): raise ValueError("Solvation array shape mismatch")
            values = allocation.sum(axis=1) if channel == "total" else allocation[:, 0 if channel == "polar" else 1]
            count = 0
            displayed = 0.0
            for i, p in enumerate(model["particles"]):
                value = float(values[i])
                if p["element"] == "H" or abs(value) < threshold or count+8 > dot_cap or np.min(np.linalg.norm(xyz[n:]-xyz[i], axis=1)) > pocket: continue
                leaf, token, color = "favorable" if value < 0 else "unfavorable", i+1, cls.color(value, scale)
                displayed += value
                markers[leaf][token] = {"kind": "solvation", "atom": i, "value_kcal": value, "polar_kcal": float(allocation[i, 0]), "ace_kcal": float(allocation[i, 1]), "channel": channel, "nonunique_allocation": True, "saturated": abs(value) > scale}
                for j in range(8):
                    z = 1 - 2*(j+0.5)/8
                    angle = j * math.pi*(3-math.sqrt(5))
                    radius = math.sqrt(1-z*z)
                    point = xyz[i] + np.array([math.cos(angle)*radius, math.sin(angle)*radius, z]) * 1.65
                    geometry[leaf] += [cgo.PICK_COLOR, token, cgo.cPickableGadget, cgo.COLOR, *color, cgo.SPHERE, *point, 0.075]
                    count += 1
            hidden["solvation"] = {"displayed": displayed, "hidden": float(values.sum())-displayed, "total": float(values.sum()), "channel": channel}
        for leaf in geometry: cls.replace_geometry(analysis, leaf, geometry[leaf], markers[leaf])
        analysis["display"] = {"threshold": threshold, "pocket": pocket, "scale": scale, "channel": channel, "contact_cap": contact_cap, "dot_cap": dot_cap}
        analysis["hidden"] = hidden
        analysis["visibility"] = {leaf: name in cmd.get_names("objects", enabled_only=1) for leaf, name in analysis["owned"].items()}
        return {"parent": analysis["parent"], "components": analysis["owned"], "hidden": hidden, "marker_count": sum(len(m) for m in markers.values())}

    @classmethod
    def pick(cls, ndc_x, ndc_y):
        with cmd.lockcm:
            frame = _cmd.metal_marker_pick(cmd._COb, -1, -1, 0)
            if frame["status"] == "pending": return None
            hit = _cmd.metal_marker_pick(cmd._COb, int((ndc_x+1)*frame["width"]/2), int((1-ndc_y)*frame["height"]/2), frame["generation"])
        if hit["status"] != "hit": return None
        for analysis_id, analysis in cls.analyses.items():
            record = analysis["markers"].get(hit["object"], {}).get(hit["index"])
            if record and not analysis["removed"]:
                atoms = record.get("atoms", [record["atom"]] if "atom" in record else [record[k] for k in ("donor", "hydrogen", "acceptor") if k in record])
                labels = [f"{p['chain']}/{p['resname']}{p['resi']}/{p['name']}" for p in (analysis["model"]["particles"][i] for i in atoms)]
                record = {**record, "atom_labels": labels}
                return {"analysis_id": analysis_id, "object": hit["object"], "record": record, "model_hash": analysis["model"]["model_hash"], "endpoint_hash": analysis["model"]["endpoint_hash"]}
        return None

    @classmethod
    def dispatch(cls, operation, arguments, report):
        try:
            if operation == "input": result = cls.input(**arguments)
            elif operation == "input_options": result = cls.input_options()
            elif operation == "ligand_input": result = cls.ligand_input(**arguments)
            elif operation == "ligand_chemistry": result = cls.ligand_chemistry(**arguments)
            elif operation == "session_inputs": result = cls.session_inputs()
            elif operation == "stage":
                event = arguments["event"]
                cls.accept(event)
                accepted_model = cls.analyses[event["analysis_id"]].get("model")
                result = cls.render(event["analysis_id"], **arguments.get("display", {})) if accepted_model and event["status"] in {"ready", "failed", "unavailable"} else {}
            elif operation == "display": result = cls.render(**arguments)
            elif operation == "pick": result = cls.pick(**arguments)
            elif operation == "health": result = cls.health(**arguments)
            elif operation == "master": result = cls.master(**arguments)
            elif operation == "remove": result = cls.remove(**arguments)
            elif operation == "interrupt": result = cls.interrupt(**arguments)
            elif operation == "query": result = cls.query(**arguments)
            else: raise ValueError("Unknown energy UI operation")
            cls.write(report, {"status": "ready", "value": result})
        except Exception as error:
            cls.write(report, {"status": "failed", "message": str(error)[:2000]})
