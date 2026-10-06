"""Progressive analysis with independent component failures and durable artifacts."""

import json
from pathlib import Path
import time
from typing import Any

from .artifacts import Artifacts
from .protocol import BaseModelNoExtra, MolecularSnapshot
from .prepare import Preparation
from .score import Score
from .solvation import Solvation


class Analysis(BaseModelNoExtra):
    preparation: Any = None

    def run(self, payload, cancelled, publish):
        if set(payload) != {"directory", "snapshot"}:
            raise ValueError("Analysis requires owned directory and snapshot descriptor")
        root = Path(payload["directory"])
        if not root.is_absolute(): raise ValueError("Absolute analysis directory required")
        store = Artifacts(root=root)
        snapshot = MolecularSnapshot.model_validate_json(store.read(payload["snapshot"]))
        if root.name != snapshot.analysis_id:
            raise ValueError("Analysis directory identity mismatch")
        if self.preparation is None or self.preparation.directory != root.parent:
            self.preparation = Preparation(directory=root.parent)
        started = time.monotonic()
        model, initial = self.preparation.construct(snapshot, cancelled)
        endpoint, preparation_meta = self.preparation.endpoint(model, initial, cancelled)
        identities = {"analysis_id": snapshot.analysis_id, "input_hash": payload["snapshot"]["sha256"], "scene_revision": snapshot.scene_revision, "model_hash": model.key, "endpoint_hash": preparation_meta["endpoint_hash"]}
        sequence, statuses, timings = 0, {}, {}
        score, solvent = Score(), Solvation()

        def event(stage, status, artifacts=None, summary=None, message=None):
            nonlocal sequence
            Preparation.check_cancel(cancelled)
            sequence += 1
            statuses[stage] = status
            value = {**identities, "sequence": sequence, "stage": stage, "status": status, "artifacts": artifacts or {}, "summary": summary or {}, "elapsed_seconds": time.monotonic() - started}
            if message is not None: value["message"] = message
            publish(value)
            return value

        model_manifest = {"version": 1, **identities, "nprotein": model.nprotein, "particles": model.particles, "terms": model.terms, "bonds": model.bonds, "system_xml": model.system_xml, "provenance": model.provenance, "preparation": preparation_meta}
        model_file = store.write_json("model.json", model_manifest)
        coordinates = store.array("prepared.npy", endpoint)
        event("prepared", "ready", {"model": model_file, "coordinates": coordinates}, preparation_meta)
        global_totals = None
        for stage in ("direct", "global_solvation", "local_solvation", "strain"):
            stage_start = time.monotonic()
            event(stage, "pending")
            try:
                if stage == "direct":
                    pairs, totals = score.direct(model, endpoint, cancelled)
                    files = {"pairs": store.array("pairs.npy", pairs)}
                    summary = totals
                elif stage == "global_solvation":
                    global_totals = solvent.global_totals(model, endpoint, cancelled)
                    files = {"totals": store.write_json("solvent-global.json", {**identities, "totals": global_totals})}
                    summary = global_totals["difference"]
                elif stage == "local_solvation":
                    if global_totals is None:
                        event(stage, "unavailable", message="Global solvation did not validate")
                        continue
                    atom, heavy, metadata = solvent.local(model, endpoint, global_totals, cancelled)
                    files = {"atom": store.array("solvent-atom.npy", atom), "heavy": store.array("solvent-heavy.npy", heavy), "metadata": store.write_json("solvent-allocation.json", {**identities, **metadata})}
                    summary = metadata["totals"]["difference"]
                else:
                    strain = score.strain(model, endpoint, cancelled)
                    files = {"strain": store.write_json("strain.json", {**identities, **strain})}
                    summary = {key: strain[key] for key in ("local_strain_kj", "heavy_rmsd_angstrom", "label")}
                timings[stage] = time.monotonic() - stage_start
                descriptor = store.write_json(f"stage-{stage}.json", {"version": 1, **identities, "stage": stage, "artifacts": files, "summary": summary, "seconds": timings[stage]})
                event(stage, "ready", {"stage": descriptor, **files}, summary)
            except ValueError as error:
                timings[stage] = time.monotonic() - stage_start
                event(stage, "failed", message=str(error)[:2000])
        terminal = {"version": 1, **identities, "statuses": statuses, "timings_seconds": timings, "complete": all(value == "ready" for value in statuses.values())}
        descriptor = store.write_json("result.json", terminal)
        return {**terminal, "result": descriptor}
