"""One-time standard conda-pack prefix repair in the chosen install location."""

from pathlib import Path
import runpy
import sys


class Bootstrap:
    @staticmethod
    def run():
        script = Path(sys.prefix) / "bin/conda-unpack"
        assert script.is_file(), "Not an unpacked conda-pack helper"
        sys.argv = [str(script)]
        runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    Bootstrap.run()
