# Energy Regression Tests

Run with the scientific helper's Python environment and the RayMol root as the
working directory:

```sh
PYTHONPATH=modules "$HELPER_PYTHON" -m unittest discover -s testing/tests/energy -v
```

`HELPER_PYTHON` must be an absolute interpreter path. The tests require the
OpenMM/OpenFF/RDKit/NAGL helper dependencies and the exact scientific assets
identified by `modules/raymol_energy/scientific-profile-v1.json`. They do not
launch the viewer. Calculations use all available CPUs for molecular mechanics;
the numerical cases are small, targeted regressions.

Public fixture inputs live in `fixtures/`. Outputs and caches go to the ignored
local `run_261003_energy_feasibility/` directory. Three additional histidine
tests use a private local `user-inputs.json` in that directory. They explicitly
skip when it is absent; private structures are not distributed.

## Fixture Sources

`alanine-dipeptide.pdb` comes from openmmtools commit
`f6ef22a8b9f66e582df2ffa62f3bb6516de43536`, at
`openmmtools/data/alanine-dipeptide-gbsa/alanine-dipeptide.pdb`.
Its original SHA-256 is
`4110a5c2f68e6336e8ebf9f968635aa136c19873ca786dd085709be89929092d`.
Only trailing whitespace was removed; coordinates and names are unchanged.
The upstream MIT notice is retained in `fixtures/openmmtools-LICENSE`.

`acetanilide.sdf` is the synthetic RDKit-generated acetanilide fixture used by
these tests, corresponding to `CC(=O)Nc1ccccc1`. It is not a private compound
or deposited protein-ligand complex.
