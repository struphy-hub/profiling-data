import os

# -----------------------------
# Description of the simulation
# -----------------------------

description = """
Higher-resolution variant of ToyDrift/periodic_slab (see that case's own description for the
model/domain background), tuned specifically to show a *dramatic* gap between pcg and PETSc:
a 32^3 grid with degree-3 splines (roughly 4x the dofs of periodic_slab's 24^3 grid) pushes
feectools' unpreconditioned CG into several hundred iterations per solve, while PETSc with an
algebraic multigrid preconditioner (`pc_type="gamg"`, set via `SolverParameters.pc_type` -- see
struphy.linear_algebra.solver.SolverParameters) stays at a handful of iterations regardless of
problem size. Measured standalone (not through this driver, to isolate just the solve): pcg took
~21s/solve (623 iterations) against PETSc+gamg's ~0.35s/solve (2 iterations) -- a ~60x difference,
vs. periodic_slab's ~39x at the smaller grid. This is the expected trend: pcg's iteration count
(hence cost) grows with the condition number of the discrete Laplacian, which grows with grid
resolution, while gamg's multigrid hierarchy keeps convergence essentially grid-independent.

Like periodic_slab, gc_poisson runs as a *regular per-step propagator*, so a single
`sim.run(one_time_step=True)` call already times one full, representative solve.
"""

# ------------------
# Import Struphy API
# ------------------

from struphy import (
    BaseUnits,
    BoundaryParameters,
    DerhamOptions,
    EnvironmentOptions,
    LoadingParameters,
    Simulation,
    SortingParameters,
    Time,
    WeightsParameters,
    domains,
    equils,
    grids,
    maxwellians,
    perturbations,
)
from struphy.linear_algebra.solver import SolverParameters

# ---------------------
# Instance of the model
# ---------------------
from struphy.models import ToyDrift

# Units
base_units = BaseUnits(kBT=1.0)

# Model instance
model = ToyDrift(base_units=base_units)

# List all variables and decide whether to save their data
model.em_fields.phi.save_data = True
model.kinetic_ions.var.save_data = False

# --------------------------
# Instance of the simulation
# --------------------------

# `--id` distinguishes runs that share a rank count but differ in something else; the
# profiling driver passes its launch counter (see `ProfilingJob.build_commands`).
# Unknown flags are ignored so the driver can forward other parameters as well.
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--id", type=int, default=0, help="Run id, used to name the output folder.")
parser.add_argument(
    "--solver", type=str, default="pcg", choices=["pcg", "petsc"], help="Solver for the Poisson-type solve."
)
args, _ = parser.parse_known_args()

# scope-profiler label: distinguishes solver/rank-count combinations in post-processing
# (chart legends, `scope-profiler inspect`), see EnvironmentOptions.profiling_label.
from feectools.ddm.mpi import mpi as MPI

_comm = MPI.COMM_WORLD
_num_ranks = _comm.Get_size() if _comm is not None else 1
_profiling_label = f"{args.solver}, {_num_ranks} rank" + ("s" if _num_ranks != 1 else "")

# Environment options
env = EnvironmentOptions(
    sim_folder=f"sim_{args.id:02d}",
    out_folders=os.environ.get("STRUPHY_PROFILING_OUT_FOLDERS", os.getcwd()),
    profiling_activated=True,
    profiling_trace=True,
    profiling_label=_profiling_label,
)

# Time stepping
time_opts = Time(dt=0.05, Tend=0.05, split_algo="LieTrotter")

# Geometry
domain = domains.Cuboid()

# Fluid equilibrium: straight B field, homogeneous density (default n0=1.0)
equil = equils.HomogenSlab(B0z=1.0, n0=1.0)

# Grid -- 32^3, ~2x periodic_slab's 24^3 per direction (~2.4x the dofs at fixed degree): large
# enough for pcg's iteration count (hence cost) to blow up while PETSc+gamg stays flat.
grid = grids.TensorProductGrid(num_elements=(32, 32, 32))

# Derham options -- fully periodic (required: PETScSolver cannot (yet) assemble
# DirectionalDerivativeOperator along a non-periodic axis, see
# struphy.linear_algebra.petsc_solver._directional_derivative_to_stencil_matrix)
derham_opts = DerhamOptions(degree=(3, 3, 3), bcs=(None, None, None))

# Simulation object
sim = Simulation(
    model=model,
    params_path=__file__,
    env=env,
    time_opts=time_opts,
    domain=domain,
    equil=equil,
    grid=grid,
    derham_opts=derham_opts,
)

# -------------------
# Particle parameters
# -------------------

loading_params = LoadingParameters(ppc=5, seed=42)
weights_params = WeightsParameters(control_variate=True)
boundary_params = BoundaryParameters()
sorting_params = SortingParameters(boxes_per_dim=(4, 4, 4), do_sort=True)

model.kinetic_ions.set_markers(
    loading_params=loading_params,
    weights_params=weights_params,
    boundary_params=boundary_params,
    sorting_params=sorting_params,
    bufsize=0.4,
)

# ------------------
# Propagator options
# ------------------

model.propagators.gc_poisson.options.solver = args.solver
# pc_type only affects solver="petsc" (ignored by pcg, see SolverParameters.pc_type); "gamg" is
# what makes PETSc's advantage large here -- the default "jacobi" preconditioner still beats pcg,
# but by a far smaller margin (~12x vs. ~60x at this grid size).
model.propagators.gc_poisson.options.solver_params = SolverParameters(tol=1e-10, maxiter=5_000, pc_type="gamg")
model.propagators.push_gc_bxe.options = model.propagators.push_gc_bxe.Options(
    algo="explicit",
    evaluate_e_field=True,
)

# ------------------
# Initial conditions
# ------------------
# Initial conditions are the sum of the background(s) and the perturbation(s).

# Background for kinetic species
background = maxwellians.GyroMaxwellian2D(
    n=(1.0, None),
    vth_para=(1.0, None),
    vth_perp=(1.0, None),
    equil=equil,
)
model.kinetic_ions.var.add_background(background)

# Perturbation, matching the Landau-damping style used elsewhere in this benchmark suite
perturbation = perturbations.ModesCos(amps=(0.5,), ls=(1,))
init = maxwellians.GyroMaxwellian2D(
    n=(1.0, perturbation),
    vth_para=(1.0, None),
    vth_perp=(1.0, None),
    equil=equil,
)
model.kinetic_ions.var.add_initial_condition(init)

if __name__ == "__main__":
    # one_time_step=True isolates the (still per-step, unlike VlasovAmpereOneSpecies)
    # gc_poisson solve for a single-step timing snapshot.
    sim.run(one_time_step=True)
