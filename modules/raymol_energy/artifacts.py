"""Atomic, hash-checked JSON and numeric arrays within one owned analysis directory."""

import hashlib
import json
from pathlib import Path
import os
from collections.abc import Mapping

from .protocol import BaseModelNoExtra


class Artifacts(BaseModelNoExtra):
    root: Path

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()) or path == self.root.resolve():
            raise ValueError("Artifact outside owned analysis directory")
        return path

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=Artifacts.json_default).encode()

    @staticmethod
    def json_default(value):
        if isinstance(value, Mapping): return dict(value)
        raise TypeError(f"Not a JSON record: {type(value).__name__}")

    def write_json(self, relative, value):
        return self.write(relative, self.canonical(value))

    def write(self, relative, data):
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
        return {"path": relative, "sha256": self.digest(data), "bytes": len(data)}

    def read(self, descriptor):
        path = self.path(descriptor["path"])
        if path.stat().st_size != descriptor["bytes"]:
            raise ValueError("Artifact byte count mismatch")
        data = path.read_bytes()
        if self.digest(data) != descriptor["sha256"]:
            raise ValueError("Artifact hash mismatch")
        return data

    def array(self, relative, values):
        import numpy as np
        import io
        values = np.asarray(values)
        if values.dtype.kind not in "fiub" or not np.isfinite(values).all():
            raise ValueError("Numeric finite arrays only")
        file = io.BytesIO()
        np.save(file, values, allow_pickle=False)
        return {**self.write(relative, file.getvalue()), "shape": list(values.shape), "dtype": values.dtype.str}

    def read_array(self, descriptor):
        import numpy as np
        self.read(descriptor)
        values = np.load(self.path(descriptor["path"]), allow_pickle=False, mmap_mode="r")
        if list(values.shape) != descriptor["shape"] or values.dtype.str != descriptor["dtype"] or values.dtype.kind not in "fiub" or not np.isfinite(values).all():
            raise ValueError("Artifact array shape/dtype/finiteness mismatch")
        return values
