"""Bounded scientific operations used by the Phase A transport/relocation gates."""

import ctypes
import hashlib
import math
import os
from pathlib import Path
import sys
import sysconfig
import time
from typing import ClassVar

from .protocol import BaseModelNoExtra


class Runtime(BaseModelNoExtra):
    torch_configured: ClassVar[bool] = False

    @staticmethod
    def mm_threads():
        count = os.cpu_count()
        if count is None or count < 1:
            raise RuntimeError("Cannot determine available molecular-mechanics CPUs")
        return count

    @staticmethod
    def execute(operation, payload, cancelled):
        if operation == "ligand_input":
            from .ligand_input import LigandInput
            return LigandInput.model_validate(payload).prepare(cancelled)
        import numpy as np
        import openmm as mm
        from openmm import unit

        if operation in {"charge", "inspect"}:
            import platformdirs
            import torch
            from openff.toolkit import Molecule, ForceField
            from openff.toolkit.utils.nagl_wrapper import NAGLToolkitWrapper
            from openff.units import unit as offunit

            # NAGL creates its child cache but assumes the user cache root exists.
            platformdirs.user_cache_path(ensure_exists=True)
            torch.set_num_threads(4)
            if not Runtime.torch_configured:
                if torch.get_num_interop_threads() != 1:
                    torch.set_num_interop_threads(1)
                Runtime.torch_configured = True
            site = Path(sysconfig.get_path("purelib"))
            model = site / "openff/nagl_models/models/am1bcc/openff-gnn-am1bcc-1.0.0.pt"
            offxml = site / "openforcefields/offxml/openff_unconstrained-2.3.0.offxml"
            smiles = payload["smiles"] if operation == "charge" else "CC(=O)Nc1ccccc1"
            assert isinstance(smiles, str) and 0 < len(smiles) <= 4096
            molecule = Molecule.from_smiles(smiles)
            ForceField(str(offxml))
            NAGLToolkitWrapper().assign_partial_charges(molecule, str(model), normalize_partial_charges=False, file_hash="7981e7f5b0b1e424c9e10a40d9e7606d96dcd3dd2b095cb4eeff6829f92238ee")
            charges = molecule.partial_charges.m_as(offunit.elementary_charge)
            assert np.isfinite(charges).all()
            assert abs(float(charges.sum()) - molecule.total_charge.m_as(offunit.elementary_charge)) <= 1e-6
            result = {"charges": charges.tolist(), "prefix": sys.prefix, "assets": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [model, offxml]]}
            if operation == "inspect":
                dyld = ctypes.CDLL(None)
                dyld._dyld_image_count.restype = ctypes.c_uint32
                dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
                dyld._dyld_get_image_name.restype = ctypes.c_char_p
                result["loaded_images"] = [dyld._dyld_get_image_name(i).decode() for i in range(dyld._dyld_image_count())]
                assert result["loaded_images"]
            return result
        if operation == "minimize":
            system = mm.XmlSerializer.deserialize(payload["system_xml"])
            assert 0 < system.getNumParticles() <= 1000
            integrator = mm.VerletIntegrator(0.001)
            context = mm.Context(system, integrator, mm.Platform.getPlatformByName("CPU"), {"Threads": str(Runtime.mm_threads()), "DeterministicForces": "true"})
            context.setPositions(payload["positions_nm"] * unit.nanometer)

            class Reporter(mm.MinimizationReporter):
                def report(self, iteration, x, grad, args):
                    return cancelled.is_set()

            mm.LocalEnergyMinimizer.minimize(context, 1.0, 100, Reporter())
            energy = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilocalorie_per_mole)
            assert math.isfinite(energy)
            return {"energy_kcal_mol": energy}
        assert operation == "tiles"
        count = payload["count"]
        assert type(count) is int and 0 < count <= 100000
        values = np.arange(4096, dtype=np.float64)
        total, completed = 0.0, 0
        deadline = time.monotonic() + 15
        for _ in range(count):
            if cancelled.is_set():
                break
            assert time.monotonic() < deadline, "Bounded tile fixture deadline"
            total += float(np.dot(values, values))
            completed += 1
        assert math.isfinite(total)
        return {"completed_tiles": completed, "sum": total}
