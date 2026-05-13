#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import numpy as np

try:
    import matplotlib
    if "--interactive" not in sys.argv and os.environ.get("MPLBACKEND") is None:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    def tqdm(iterable, **kwargs):
        return iterable


import run_enterprise.spectrum.cholspec as cholspec

# Angular frequency for the annual sinusoid used in position / proper-motion terms (rad day^-1).
_OMEGA_PER_DAY = 2.0 * np.pi / 365.25


def ensure_interactive_matplotlib_backend():
    if not HAS_MATPLOTLIB:
        raise RuntimeError("Interactive mode requires matplotlib")

    backend = matplotlib.get_backend().lower()
    if "agg" not in backend:
        return

    candidate_backends = ["MacOSX", "TkAgg", "QtAgg", "Qt5Agg"]
    backend_errors = []
    for candidate in candidate_backends:
        try:
            plt.switch_backend(candidate)
            return
        except Exception as exc:
            backend_errors.append(f"{candidate}: {exc}")

    message = "; ".join(backend_errors)
    raise RuntimeError(
        "Interactive mode requested, but matplotlib is using a non-interactive Agg backend and no interactive backend could be loaded. "
        f"Tried: {message}"
    )



class Particle:
    def __init__(self, assignments, observed_indices, observed_values, log_weight, diagnostics):
        self.assignments = assignments
        self.observed_indices = observed_indices
        self.observed_values = observed_values
        self.log_weight = log_weight
        self.diagnostics = diagnostics

    def __str__(self):
        io_flags = []
        for diagnostic in self.diagnostics:
            if "branch_is_outlier" in diagnostic:
                io_flags.append("O" if diagnostic["branch_is_outlier"] else "I")
            else:
                io_flags.append("?")
        wraps = ",".join(str(wrap) for wrap in self.assignments)
        io_str = "".join(io_flags)
        return f"Particle(log_weight={self.log_weight:.6f}, wraps=[{wraps}], in_out={io_str})"

    __repr__ = __str__




def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--par", required=True, help="input par file")
    parser.add_argument("--tim", required=True, help="tim file with trusted pulse numbers")
    parser.add_argument("--newtim", required=True, help="tim file containing new data to associate")
    parser.add_argument("--fc-yr", type=float, default=0.02, help="corner frequency in yr^-1 for the red-noise covariance")
    parser.add_argument("--covariance-scale", type=float, default=1.0, help="scale factor applied to the GP covariance matrix to tune predictive confidence")
    parser.add_argument("--wrap-min", type=int, default=-10, help="minimum integer wrap hypothesis")
    parser.add_argument("--wrap-max", type=int, default=10, help="maximum integer wrap hypothesis")
    parser.add_argument("--wrap-prior-sigma", type=float, default=0.0, help="Gaussian prior width for integer wraps; non-positive disables the prior")
    parser.add_argument("--particle-min-keep", type=int, default=16, help="minimum number of particles to retain after pruning when available")
    parser.add_argument("--particle-limit", type=int, default=128, help="maximum number of retained particles after pruning")
    parser.add_argument("--outlier-prob", type=float, default=0.05, help="mixture weight assigned to the broad outlier component")
    parser.add_argument("--outlier-sigma", type=float, default=3.0, help="sigma of the broad outlier Gaussian in phase units")
    parser.add_argument("--output", default=None, help="optional CSV file for per-observation diagnostics")
    parser.add_argument("--output-tim", default=None, help="optional output tim file with outlier comments and -pnadd wrap annotations")
    parser.add_argument("--output-dir", default=None, help="output directory for CSV/TIM/PNG when --output/--output-tim are not set")
    parser.add_argument("--run-id", default=None, help="optional external run identifier used for downstream review workflows")
    parser.add_argument("--pulsar", default=None, help="optional pulsar identifier for manifest metadata")
    parser.add_argument("--manifest-output", default=None, help="optional JSON output path for per-run summary metadata")
    parser.add_argument("--complete-marker", default=None, help="optional marker file written after all outputs are complete")
    parser.add_argument("--interactive", action="store_true", help="launch a basic interactive matplotlib UI for manual constraints and re-solving")
    parser.add_argument(
        "--mean-poly-order",
        type=int,
        default=-1,
        help="polynomial order for GP parametric mean (0=constant, 1=linear, 2=quadratic; negative disables and uses zero-mean)",
    )
    parser.add_argument("--ephindex", default=None, help="optional JBO/AGL style ephindex.dat file giving dates of possible glitches")
    parser.add_argument(
        "--fit-pos", "--fit-position",
        dest="fit_pos",
        action="store_true",
        help="include annual position sinusoid terms (A*sin(omega*t) + B*cos(omega*t)) in the GP parametric mean",
    )
    parser.add_argument(
        "--fit-pm", "--fit-proper-motion",
        dest="fit_pm",
        action="store_true",
        help="include proper-motion sinusoid terms (C*t*sin(omega*t) + D*t*cos(omega*t)) in the GP parametric mean; implies --fit-pos",
    )
    return parser.parse_args()


def read_gp_parameters(par_path):
    parameters = {}
    with open(par_path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if fields[0] in {"TNRedAmp", "TNRedGam", "F0"}:
                parameters[fields[0]] = float(fields[1])

    missing = {"TNRedAmp", "TNRedGam", "F0"} - set(parameters)
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required red-noise parameters in par file: {missing_names}")
    return parameters

def read_ephindex(ephindex_path):
    glitch_epochs = []
    with open(ephindex_path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 3:
                continue
            try:
                epoch = float(fields[1])
                glitch_epochs.append(epoch)
            except ValueError:
                continue
    return glitch_epochs

def run_tempo2_exportres(par_path, tim_path, working_directory):
    command = [
        "tempo2",
        "-output",
        "exportres",
        "-f",
        par_path,
        tim_path,
        "-nofit",
        "-npsr",
        "1",
        "-nobs",
        "50000",
    ]
    print("Run tempo2:")
    print(" ".join(command))
    try:
        subprocess.run(command, cwd=working_directory, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip() or "tempo2 exportres failed") from exc
    out_res = os.path.join(working_directory, "out.res")
    times = []
    phase = []
    sigma = []
    pulse_number = []
    identifier = []
    frequency_mhz = []
    print("Reading tempo2 out.res")
    with open(out_res) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 6:
                continue
            try:
                times.append(float(fields[0]))
                phase.append(float(fields[1]))
                sigma.append(float(fields[2]))
                pulse_number.append(np.int64(fields[3]))
                identifier.append(fields[4])
                frequency_mhz.append(float(fields[5]))
            except ValueError:
                continue

    if not times:
        raise ValueError("tempo2 out.res does not contain parsable residual rows with columns 0-5")

    print("Done reading tempo2 out.res")
    return {
        "times": np.asarray(times, dtype=float),
        "phase": np.asarray(phase, dtype=float),
        "sigma": np.asarray(sigma, dtype=float),
        "pulse_number": np.asarray(pulse_number, dtype=np.int64),
        "identifier": np.asarray(identifier, dtype=object),
        "frequency_mhz": np.asarray(frequency_mhz, dtype=float),
    }


def identify_new_observations(all_times, all_identifiers, all_frequency_mhz, trusted_times, trusted_identifiers, trusted_frequency_mhz):
    all_times = np.asarray(all_times, dtype=float)
    all_identifiers = np.asarray(all_identifiers, dtype=object)
    all_frequency_mhz = np.asarray(all_frequency_mhz, dtype=float)
    trusted_times = np.asarray(trusted_times, dtype=float)
    trusted_identifiers = np.asarray(trusted_identifiers, dtype=object)
    trusted_frequency_mhz = np.asarray(trusted_frequency_mhz, dtype=float)

    mask = np.ones(len(all_times), dtype=bool)

    all_by_identifier = {}
    trusted_by_identifier = {}
    for index, identifier in enumerate(all_identifiers):
        all_by_identifier.setdefault(identifier, []).append(index)
    for index, identifier in enumerate(trusted_identifiers):
        trusted_by_identifier.setdefault(identifier, []).append(index)

    for identifier, all_indices in all_by_identifier.items():
        trusted_indices = trusted_by_identifier.get(identifier, [])
        if not trusted_indices:
            continue

        # Typical case: one-to-one identifier match.
        if len(all_indices) == 1 and len(trusted_indices) == 1:
            mask[all_indices[0]] = False
            continue

        # Rare duplicate-identifier case: greedily assign the closest time/frequency pairs.
        pair_candidates = []
        for all_index in all_indices:
            for trusted_index in trusted_indices:
                time_delta = abs(all_times[all_index] - trusted_times[trusted_index])
                freq_delta = abs(all_frequency_mhz[all_index] - trusted_frequency_mhz[trusted_index])
                pair_candidates.append((time_delta, freq_delta, all_index, trusted_index))
        pair_candidates.sort(key=lambda row: (row[0], row[1]))

        used_all = set()
        used_trusted = set()
        for _, _, all_index, trusted_index in pair_candidates:
            if all_index in used_all or trusted_index in used_trusted:
                continue
            mask[all_index] = False
            used_all.add(all_index)
            used_trusted.add(trusted_index)

    return mask


def gaussian_logpdf(value, mean, variance):
    safe_variance = max(float(variance), 1e-15)
    delta = value - mean
    return -0.5 * (math.log(2.0 * math.pi * safe_variance) + (delta * delta) / safe_variance)


def logsumexp(log_values):
    finite_values = np.asarray(log_values, dtype=float)
    max_value = np.max(finite_values)
    if not np.isfinite(max_value):
        return max_value
    return max_value + math.log(np.sum(np.exp(finite_values - max_value)))


def normalize_log_weights(log_weights):
    normalizer = logsumexp(log_weights)
    return np.exp(np.asarray(log_weights, dtype=float) - normalizer), normalizer


def systematic_resample(normalized_weights, rng):
    count = len(normalized_weights)
    positions = (rng.random() + np.arange(count)) / count
    cumulative = np.cumsum(normalized_weights)
    cumulative[-1] = 1.0
    indexes = np.zeros(count, dtype=int)
    source = 0
    for target, position in enumerate(positions):
        while position > cumulative[source]:
            source += 1
        indexes[target] = source
    return indexes


def build_polynomial_design(times, order, reference_time, time_scale):
    centered = (np.asarray(times, dtype=float) - reference_time) / time_scale
    columns = [np.ones_like(centered)]
    for degree in range(1, order + 1):
        columns.append(centered ** degree)
    return np.column_stack(columns)


def build_mean_design(times, poly_order, reference_time, time_scale, fit_pos, fit_pm):
    """Build a combined GLS design matrix.

    Columns (in order, when enabled):
      - Polynomial terms of degree 0..poly_order  (when poly_order >= 0)
      - sin(omega*t), cos(omega*t)                (when fit_pos or fit_pm)
      - t*sin(omega*t), t*cos(omega*t)            (when fit_pm)

    t is measured in days from reference_time.
    Returns an (N, K) array, or None when no columns would be produced.
    """
    times = np.asarray(times, dtype=float)
    t_days = times - reference_time
    columns = []

    if poly_order >= 0:
        centered = t_days / time_scale
        columns.append(np.ones_like(centered))
        for degree in range(1, poly_order + 1):
            columns.append(centered ** degree)

    if fit_pos or fit_pm:
        phase = _OMEGA_PER_DAY * t_days
        columns.append(np.sin(phase))
        columns.append(np.cos(phase))

    if fit_pm:
        phase = _OMEGA_PER_DAY * t_days
        columns.append(t_days * np.sin(phase))
        columns.append(t_days * np.cos(phase))

    if not columns:
        return None
    return np.column_stack(columns)


def predictive_observation_stats(candidate_index, observed_indices, observed_values, covariance, noise_variance, all_times, mean_poly_order, fit_pos=False, fit_pm=False):
    prior_mean = 0.0
    prior_variance = covariance[candidate_index, candidate_index] + noise_variance[candidate_index]
    if not observed_indices:
        return prior_mean, prior_variance

    history = np.asarray(observed_indices, dtype=int)
    observed = np.asarray(observed_values, dtype=float)
    system_covariance = covariance[np.ix_(history, history)] + np.diag(noise_variance[history])
    cross_covariance = covariance[candidate_index, history]

    if mean_poly_order < 0 and not fit_pos and not fit_pm:
        solved_mean = np.linalg.solve(system_covariance, observed)
        solved_cross = np.linalg.solve(system_covariance, cross_covariance)
        predictive_mean = float(cross_covariance.dot(solved_mean))
        predictive_variance = float(prior_variance - cross_covariance.dot(solved_cross))
        return predictive_mean, max(predictive_variance, 1e-12)

    reference_time = float(np.median(all_times))
    time_span = float(np.max(all_times) - np.min(all_times))
    time_scale = max(time_span, 1.0)

    history_times = np.asarray(all_times, dtype=float)[history]
    candidate_time = np.asarray([all_times[candidate_index]], dtype=float)
    design_history = build_mean_design(history_times, mean_poly_order, reference_time, time_scale, fit_pos, fit_pm)
    design_candidate = build_mean_design(candidate_time, mean_poly_order, reference_time, time_scale, fit_pos, fit_pm)[0]

    solved_cross = np.linalg.solve(system_covariance, cross_covariance)

    # GLS estimate of the mean model coefficients.
    whitened_design = np.linalg.solve(system_covariance, design_history)
    fisher = design_history.T.dot(whitened_design)
    fisher_inv = np.linalg.pinv(fisher)
    beta_hat = fisher_inv.dot(design_history.T.dot(np.linalg.solve(system_covariance, observed)))

    residual = observed - design_history.dot(beta_hat)
    solved_residual = np.linalg.solve(system_covariance, residual)
    predictive_mean = float(design_candidate.dot(beta_hat) + cross_covariance.dot(solved_residual))

    mean_uncertainty = design_candidate - design_history.T.dot(solved_cross)
    predictive_variance = float(
        prior_variance
        - cross_covariance.dot(solved_cross)
        + mean_uncertainty.dot(fisher_inv).dot(mean_uncertainty)
    )
    return predictive_mean, max(predictive_variance, 1e-12)


def evaluate_wrap_candidates(
    observation,
    predictive_mean,
    predictive_variance,
    wrap_options,
    baseline_wrap_value,
    outlier_probability,
    outlier_sigma,
    wrap_prior_sigma,
):
    signal_log_weights = []
    mixture_log_weights = []
    candidate_rows = []

    signal_scale = math.log(max(1.0 - outlier_probability, 1e-12))
    outlier_scale = math.log(max(outlier_probability, 1e-12))
    outlier_variance = outlier_sigma ** 2
    use_wrap_prior = wrap_prior_sigma is not None and wrap_prior_sigma > 0.0
    wrap_prior_variance = wrap_prior_sigma ** 2 if use_wrap_prior else None

    for wrap in wrap_options:
        unwrapped_value = observation + wrap + baseline_wrap_value
        wrap_prior_log = gaussian_logpdf(wrap, 0.0, wrap_prior_variance) if use_wrap_prior else 0.0
        signal_log = signal_scale + gaussian_logpdf(unwrapped_value, predictive_mean, predictive_variance) + wrap_prior_log
        outlier_log = outlier_scale + gaussian_logpdf(unwrapped_value, 0.0, outlier_variance) + wrap_prior_log
        mixture_log = np.logaddexp(signal_log, outlier_log)
        signal_log_weights.append(signal_log)
        mixture_log_weights.append(mixture_log)
        candidate_rows.append(
            {
                "wrap": int(wrap),
                "unwrapped_value": float(unwrapped_value),
                "wrap_prior_log": float(wrap_prior_log),
                "signal_log": float(signal_log),
                "outlier_log": float(outlier_log),
                "mixture_log": float(mixture_log),
                "baseline_wrap": float(baseline_wrap_value),
            }
        )

    wrap_probabilities, marginal_log_likelihood = normalize_log_weights(mixture_log_weights)
    signal_conditionals = np.exp(np.asarray(signal_log_weights) - np.asarray(mixture_log_weights))
    inlier_probability = float(np.sum(wrap_probabilities * signal_conditionals))

    for row, probability, signal_conditional in zip(candidate_rows, wrap_probabilities, signal_conditionals):
        row["wrap_probability"] = float(probability)
        row["signal_posterior"] = float(signal_conditional)

    return candidate_rows, inlier_probability, float(marginal_log_likelihood)


def get_step_constraint(constraints, step_number):
    if constraints is None:
        return {}
    return dict(constraints.get(step_number, {}))


def get_constrained_wrap_options(wrap_options, constraint):
    forced_wrap = constraint.get("forced_wrap")
    if forced_wrap is None:
        return wrap_options
    return np.asarray([int(forced_wrap)], dtype=int)


def validate_step_constraint(constraint):
    if not constraint:
        return
    if constraint.get("force_inlier") and constraint.get("force_outlier"):
        raise ValueError("A point cannot be forced to be both inlier and outlier")


def process_single_observation(
    step_number,
    observation_index,
    particles,
    wrapped_phase,
    phase_sigma,
    all_times,
    covariance,
    noise_variance,
    wrap_options,
    wrap_to_index,
    args,
    constraints=None,
):
    constraint = get_step_constraint(constraints, step_number)
    validate_step_constraint(constraint)
    constrained_wrap_options = get_constrained_wrap_options(wrap_options, constraint)
    observation = wrapped_phase[observation_index]

    proposal_particles = []
    per_wrap_log_weights = np.full(len(wrap_options), -np.inf, dtype=float)
    inlier_log_weight_total = -np.inf
    proposal_log_weight_total = -np.inf

    for particle in particles:
        predictive_mean, predictive_variance = predictive_observation_stats(
            observation_index,
            particle.observed_indices,
            particle.observed_values,
            covariance,
            noise_variance,
            all_times,
            args.mean_poly_order,
            fit_pos=args.fit_pos,
            fit_pm=args.fit_pm,
        )
        baseline_wrap_value = round(predictive_mean - observation)

        

        candidate_rows, inlier_probability, marginal_log_likelihood = evaluate_wrap_candidates(
            observation,
            predictive_mean,
            predictive_variance,
            constrained_wrap_options,
            baseline_wrap_value,
            args.outlier_prob,
            args.outlier_sigma,
            args.wrap_prior_sigma,
        )

        for candidate in candidate_rows:
            wrap = candidate["wrap"]
            wrap_index = wrap_to_index[wrap]

            if not constraint.get("force_outlier"):
                inlier_log_weight = particle.log_weight + candidate["signal_log"]
                proposal_log_weight_total = np.logaddexp(proposal_log_weight_total, inlier_log_weight)
                inlier_log_weight_total = np.logaddexp(inlier_log_weight_total, inlier_log_weight)
                per_wrap_log_weights[wrap_index] = np.logaddexp(per_wrap_log_weights[wrap_index], inlier_log_weight)
                proposal_particles.append(
                    Particle(
                        assignments=particle.assignments + [wrap],
                        observed_indices=particle.observed_indices + [int(observation_index)],
                        observed_values=particle.observed_values + [candidate["unwrapped_value"]],
                        log_weight=inlier_log_weight,
                        diagnostics=particle.diagnostics
                        + [
                            {
                                "time": float(all_times[observation_index]),
                                "baseline_wrap": float(baseline_wrap_value),
                                "predictive_mean": float(predictive_mean),
                                "predictive_sigma": float(math.sqrt(predictive_variance)),
                                "inlier_probability": float(inlier_probability),
                                "marginal_log_likelihood": float(marginal_log_likelihood),
                                "branch_is_outlier": 0,
                                "step": int(step_number),
                            }
                        ],
                    )
                )

            if not constraint.get("force_inlier") and wrap==0:
                outlier_log_weight = particle.log_weight + candidate["outlier_log"]
                proposal_log_weight_total = np.logaddexp(proposal_log_weight_total, outlier_log_weight)
                per_wrap_log_weights[wrap_index] = np.logaddexp(per_wrap_log_weights[wrap_index], outlier_log_weight)
                proposal_particles.append(
                    Particle(
                        assignments=particle.assignments + [wrap],
                        observed_indices=list(particle.observed_indices),
                        observed_values=list(particle.observed_values),
                        log_weight=outlier_log_weight,
                        diagnostics=particle.diagnostics
                        + [
                            {
                                "time": float(all_times[observation_index]),
                                "baseline_wrap": float(baseline_wrap_value),
                                "predictive_mean": float(predictive_mean),
                                "predictive_sigma": float(math.sqrt(predictive_variance)),
                                "inlier_probability": float(inlier_probability),
                                "marginal_log_likelihood": float(marginal_log_likelihood),
                                "branch_is_outlier": 1,
                                "step": int(step_number),
                            }
                        ],
                    )
                )

    if not proposal_particles:
        raise ValueError(f"Constraints left no valid branches at step {step_number}")

    return {
        "proposal_particles": proposal_particles,
        "per_wrap_log_weights": per_wrap_log_weights,
        "inlier_log_weight_total": inlier_log_weight_total,
        "proposal_log_weight_total": proposal_log_weight_total,
        "observation": observation,
        "observation_index": observation_index,
    }


def score(particle):
    return particle.log_weight

def keep_until_mass(particles, p_threshold):
    if not particles:
        return []

    threshold = min(max(float(p_threshold), 0.0), 1.0)
    log_weights = np.asarray([particle.log_weight for particle in particles], dtype=float)
    log_total = logsumexp(log_weights)
    if not np.isfinite(log_total):
        return [particles[0]]

    normalized_weights = np.exp(log_weights - log_total)
    cumulative = np.cumsum(normalized_weights)
    cutoff_index = int(np.searchsorted(cumulative, threshold, side="left"))
    cutoff_index = min(max(cutoff_index, 0), len(particles) - 1)
    return particles[: cutoff_index + 1]

def prune_particles(particles, particle_limit, particle_min_keep):
    
    ordered = sorted(particles, key=lambda particle: score(particle), reverse=True)

    max_score= score(ordered[0]) if ordered else -np.inf
    delta = 1000.0 
    trimmed = [p for p in ordered if score(p) >= max_score - delta]
    if len(ordered) < particle_min_keep:
        trimmed = ordered[:particle_min_keep]

    min_keep = max(1, int(particle_min_keep))
    max_keep = max(1, int(particle_limit))
    keep_count = min(max_keep, max(min_keep, len(trimmed)))
    return trimmed[:keep_count]




def associate_phases(
    all_times,
    wrapped_phase,
    phase_sigma,
    trusted_mask,
    new_indices,
    covariance,
    wrap_options,
    args,
    constraints=None,
    identifiers=None,
    frequency_mhz=None,
):
    efac = 1.0
    equad = 0.0
    noise_variance = np.square(phase_sigma*efac) + equad**2
    trusted_indices = np.flatnonzero(trusted_mask)
    trusted_values = wrapped_phase[trusted_mask]
    base_particle = Particle([], trusted_indices.tolist(), trusted_values.tolist(), 0.0, [])
    particles = [base_particle]
    diagnostics = []
    wrap_to_index = {int(wrap): index for index, wrap in enumerate(wrap_options)}

    for step_number, observation_index in tqdm(enumerate(new_indices), total=len(new_indices), desc="Processing new observations"):
        previous_log_total = logsumexp([particle.log_weight for particle in particles])
        step_result = process_single_observation(
            step_number,
            observation_index,
            particles,
            wrapped_phase,
            phase_sigma,
            all_times,
            covariance,
            noise_variance,
            wrap_options,
            wrap_to_index,
            args,
            constraints=constraints,
        )
        verbose=False
        if verbose:
            print("Step {}/{}: Proposed particles before pruning:".format(step_number + 1, len(new_indices)))
            for particle in sorted(step_result["proposal_particles"], key=lambda p: p.log_weight, reverse=True):
                # print In/Outlier and wrap assignments for each proposed particle
                io_flags = []
                for diagnostic in particle.diagnostics:
                    if "branch_is_outlier" in diagnostic:
                        io_flags.append("O" if diagnostic["branch_is_outlier"] else "I")
                    else:
                        io_flags.append("?")
                wraps = ",".join(str(wrap) for wrap in particle.assignments)
                io_str = "".join(io_flags)
                print(f"  Particle(log_weight={particle.log_weight:.6f}, wraps=[{wraps}], in_out={io_str})")
        particles = prune_particles(step_result["proposal_particles"], args.particle_limit, args.particle_min_keep)
        normalized_weights, _ = normalize_log_weights([particle.log_weight for particle in particles])
        ess = 1.0 / np.sum(normalized_weights ** 2)
        wrap_posteriors = np.exp(step_result["per_wrap_log_weights"] - step_result["proposal_log_weight_total"])
        aggregated_inlier = float(np.exp(step_result["inlier_log_weight_total"] - step_result["proposal_log_weight_total"]))
        aggregated_marginal = float(step_result["proposal_log_weight_total"] - previous_log_total)
        map_wrap_index = int(np.argmax(wrap_posteriors))
        map_wrap = int(wrap_options[map_wrap_index])
        # print(f"Step {step_number + 1}/{len(new_indices)}: time={all_times[observation_index]:.8f} map_wrap={map_wrap} map_wrap_prob={wrap_posteriors[map_wrap_index]:.6f} inlier_prob={aggregated_inlier:.6f} marginal_log_likelihood={aggregated_marginal:.6f} ess={ess:.1f} particles={len(particles)}")
        # for particle in particles[:32]:
        #     print(f"  {particle}")

        diagnostics.append(
            {
                "time": float(all_times[step_result["observation_index"]]),
                "wrapped_phase": float(step_result["observation"]),
                "phase_sigma": float(phase_sigma[observation_index]),
                "identifier": "" if identifiers is None else str(identifiers[observation_index]),
                "frequency_mhz": float("nan") if frequency_mhz is None else float(frequency_mhz[observation_index]),
                "baseline_wrap": float("nan"),
                "map_wrap": map_wrap,
                "map_wrap_probability": float(wrap_posteriors[map_wrap_index]),
                "inlier_probability": aggregated_inlier,
                "predictive_mean": float("nan"),
                "predictive_sigma": float("nan"),
                "marginal_log_likelihood": aggregated_marginal,
                "effective_sample_size": float(ess),
                "outlier_flag": 0,
                "wrap_probabilities": {str(int(wrap)): float(probability) for wrap, probability in zip(wrap_options, wrap_posteriors)},
            }
        )

    best_particle = max(particles, key=lambda particle: particle.log_weight)
    for step_index, row in enumerate(diagnostics):
        final_wrap = int(best_particle.assignments[step_index])
        final_diag = best_particle.diagnostics[step_index]
        row["map_wrap"] = final_wrap
        row["map_wrap_probability"] = row["wrap_probabilities"].get(str(final_wrap), 0.0)
        row["baseline_wrap"] = float(final_diag["baseline_wrap"])
        row["predictive_mean"] = float(final_diag["predictive_mean"])
        row["predictive_sigma"] = float(final_diag["predictive_sigma"])
        row["outlier_flag"] = int(final_diag["branch_is_outlier"])
    return best_particle, diagnostics


def write_diagnostics(rows, output_path):
    fieldnames = [
        "time",
        "wrapped_phase",
        "phase_sigma",
        "identifier",
        "frequency_mhz",
        "baseline_wrap",
        "map_wrap",
        "map_wrap_probability",
        "inlier_probability",
        "predictive_mean",
        "predictive_sigma",
        "marginal_log_likelihood",
        "effective_sample_size",
        "outlier_flag",
        "wrap_probabilities",
    ]
    with open(output_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serializable = dict(row)
            serializable["wrap_probabilities"] = ";".join(
                f"{wrap}:{probability:.8g}" for wrap, probability in row["wrap_probabilities"].items()
            )
            writer.writerow(serializable)


def parse_tim_line(line):
    """
    Parse a TIM file line (standard format: identifier, frequency_MHz, time_MJD, error, site, flags...).
    Returns (identifier, frequency_mhz, time_mjd) or (None, None, None) if parsing fails.
    """
    tokens = line.split()
    if len(tokens) < 3:
        return None, None, None
    try:
        identifier = tokens[0]
        frequency_mhz = float(tokens[1])
        time_mjd = float(tokens[2])
        return identifier, frequency_mhz, time_mjd
    except (ValueError, IndexError):
        return None, None, None


def update_pulse_number(line, wrap):
    if wrap == 0:
        return line
    else:
        tokens = line.split()
        cleaned = []
        index = 0
        
        while index < len(tokens):
            token = tokens[index]
            if token == "-pn":
                current_pn = np.int64(tokens[index + 1])
                index += 2
                continue
            cleaned.append(token)
            index += 1

        cleaned.extend(["-pn", str(current_pn-np.int64(wrap))])
        return " ".join(cleaned)


def write_updated_tim(input_tim_path, output_tim_path, diagnostics):
    decisions = [
        {
            "time": float(row["time"]),
            "identifier": str(row.get("identifier", "")),
            "frequency_mhz": float(row.get("frequency_mhz", float("nan"))),
            "baseline_wrap": int(round(float(row.get("baseline_wrap", 0.0)))),
            "map_wrap": int(row["map_wrap"]),
            "outlier": int(row["outlier_flag"]),
            "used": False,
        }
        for row in diagnostics
    ]
    remaining_inliers = sum(1 for decision in decisions if not decision["outlier"])

    with open(input_tim_path) as input_handle, open(output_tim_path, "w") as output_handle:
        for raw_line in input_handle:
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            # Pass through comments and FORMAT lines as-is
            if not stripped or stripped.startswith("#") or stripped.startswith("C") or stripped.startswith("FORMAT"):
                output_handle.write(raw_line)
                continue

            # Parse the line using standard TIM format
            toa_identifier, toa_frequency, toa_time = parse_tim_line(stripped)
            if toa_identifier is None or toa_time is None:
                # Not enough fields or parsing failed; write as-is
                output_handle.write(raw_line)
                continue

            if remaining_inliers == 0:
                break

            # Find best matching decision by identifier, then time/frequency
            best_index = None
            best_key = None
            for index, decision in enumerate(decisions):
                if decision["used"]:
                    continue
                if decision["identifier"] != toa_identifier:
                    continue

                time_delta = abs(decision["time"] - toa_time)
                frequency_delta = float("inf")
                if np.isfinite(decision["frequency_mhz"]) and toa_frequency is not None:
                    frequency_delta = abs(decision["frequency_mhz"] - toa_frequency)
                candidate_key = (time_delta, frequency_delta)
                if best_key is None or candidate_key < best_key:
                    best_index = index
                    best_key = candidate_key

            if best_index is None:
                output_handle.write(raw_line)
                continue

            decision = decisions[best_index]
            decision["used"] = True

            total_wrap = decision["baseline_wrap"] + decision["map_wrap"]
            #print(f"Updating line for time={decision['time']:.8f} identifier={decision['identifier']} frequency={decision['frequency_mhz']} with total_wrap={total_wrap} (baseline={decision['baseline_wrap']} map={decision['map_wrap']} outlier={decision['outlier']})")
            updated = " " + update_pulse_number(stripped, total_wrap)
            if decision["outlier"]:
                updated = "C " + updated
            else:
                remaining_inliers -= 1
            output_handle.write(updated + "\n")

            if remaining_inliers == 0:
                break


def solve_with_constraints(all_times, wrapped_phase, phase_sigma, trusted_mask, new_indices, covariance, wrap_options, args, constraints=None):
    return associate_phases(
        all_times,
        wrapped_phase,
        phase_sigma,
        trusted_mask,
        new_indices,
        covariance,
        wrap_options,
        args,
        constraints=constraints,
    )


def print_summary(best_particle, diagnostics):
    print("index time map_wrap baseline_wrap wrap_probability inlier_probability predictive_mean predictive_sigma outlier")
    for index, row in enumerate(diagnostics, start=1):
        print(
            f"{index:5d} {row['time']:15.8f} {row['map_wrap']:8d} "
            f"{row['baseline_wrap']:16.0f} "
            f"{row['map_wrap_probability']:16.6f} {row['inlier_probability']:18.6f} "
            f"{row['predictive_mean']:16.6f} {row['predictive_sigma']:16.6f} {row['outlier_flag']:7d}"
        )
    print("best_wrap_sequence", " ".join(str(value) for value in best_particle.assignments))


def add_glitch_markers(axes, glitch_epochs):
    if not glitch_epochs:
        return

    for axis in axes:
        x_min, x_max = axis.get_xlim()
        for epoch in glitch_epochs:
            if x_min <= epoch <= x_max:
                print("Adding glitch marker at epoch", epoch)
                axis.axvline(epoch, color="purple", linestyle=":", linewidth=1.0, alpha=0.8, zorder=0)
        axis.set_xlim(x_min, x_max)


def plot_diagnostics(times, phase_sigma, wrapped_phase, diagnostics, trusted_mask, new_indices, output_path, glitch_epochs=None):
    """Generate diagnostic plot showing residuals, predictions, and outlier flags."""
    if not HAS_MATPLOTLIB:
        return None
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
    
    # Convert to arrays for indexing
    times = np.asarray(times)
    phase_sigma = np.asarray(phase_sigma)
    wrapped_phase = np.asarray(wrapped_phase)
    trusted_mask = np.asarray(trusted_mask)
    
    # Extract diagnostic info for new observations
    diag_times = np.array([d["time"] for d in diagnostics])
    diag_map_wraps = np.array([d["map_wrap"] for d in diagnostics])
    diag_baseline_wraps = np.array([d.get("baseline_wrap", 0.0) for d in diagnostics])
    diag_wrapped = np.array([d["wrapped_phase"] for d in diagnostics])
    diag_phase_sigma = np.array([d["phase_sigma"] for d in diagnostics])
    diag_pred_means = np.array([d["predictive_mean"] for d in diagnostics])
    diag_pred_sigmas = np.array([d["predictive_sigma"] for d in diagnostics])
    diag_inliers = np.array([d["inlier_probability"] for d in diagnostics])
    diag_outlier_flags = np.array([d["outlier_flag"] for d in diagnostics])
    
    # Compute unwrapped phase from persisted baseline + local wrap decision.
    diag_unwrapped = diag_wrapped + diag_baseline_wraps + diag_map_wraps
    
    # Compute residuals: unwrapped - predicted_mean
    residuals = diag_unwrapped - diag_pred_means
    
    # ========== Panel 1: Unwrapped phase with predictions ==========
    # Plot trusted observations (wrapped phase, no predictions)
    trusted_idx = np.where(trusted_mask)[0]
    if len(trusted_idx) > 0:
        ax1.scatter(times[trusted_idx], wrapped_phase[trusted_idx], 
                   alpha=0.6, s=30, c='gray', label='trusted (wrapped)', zorder=3)
        ax1.errorbar(times[trusted_idx], wrapped_phase[trusted_idx], 
                    yerr=phase_sigma[trusted_idx], fmt='none', 
                    ecolor='gray', alpha=0.4, zorder=2, capsize=3, capthick=1)
    
    # Plot new observations (color by inlier probability)
    is_outlier = diag_outlier_flags > 0
    is_inlier = ~is_outlier
    
    if np.any(is_inlier):
        scatter1 = ax1.scatter(diag_times[is_inlier], diag_unwrapped[is_inlier],
                              c=diag_inliers[is_inlier], cmap='RdYlGn', vmin=0, vmax=1,
                              s=60, edgecolors='blue', linewidths=1.5, label='new (inlier)', zorder=5)
        ax1.errorbar(diag_times[is_inlier], diag_unwrapped[is_inlier], 
                    yerr=diag_phase_sigma[is_inlier], fmt='none', 
                    ecolor='blue', alpha=0.4, zorder=4, capsize=3, capthick=1)
        cbar1 = plt.colorbar(scatter1, ax=ax1, pad=0.01)
        cbar1.set_label('inlier probability', fontsize=10)
    
    if np.any(is_outlier):
        ax1.scatter(diag_times[is_outlier], diag_unwrapped[is_outlier],
                   marker='X', s=30, c='red', edgecolors='darkred', linewidths=2,
                   label='new (outlier)', zorder=6)
        ax1.errorbar(diag_times[is_outlier], diag_unwrapped[is_outlier], 
                    yerr=diag_phase_sigma[is_outlier], fmt='none', 
                    ecolor='red', alpha=0.4, zorder=4, capsize=3, capthick=1)
    
    # Keep outlier predictions in the unwrapped frame so large excursions remain visible.
    pred_display = diag_pred_means
    pred_upper = pred_display + diag_pred_sigmas
    pred_lower = pred_display - diag_pred_sigmas
    
    ax1.plot(diag_times, pred_display, 'g-', linewidth=2, label='predictive mean', alpha=0.8, zorder=4)
    ax1.fill_between(diag_times, pred_lower, pred_upper, color='green', alpha=0.2, label='±1σ band', zorder=2)
    
    ax1.set_xlabel('time (days)', fontsize=11)
    ax1.set_ylabel('phase (unwrapped)', fontsize=11)
    ax1.set_title('Wrapped Phase Observations with Predictions', fontsize=12, fontweight='bold')
    ax1.legend(loc='best', fontsize=10)

    # Add faint horizontal lines at every integer y-value in view.
    y_min_1, y_max_1 = ax1.get_ylim()
    if y_max_1 - y_min_1 < 1000: 
        for y in range(int(np.floor (y_min_1)), int(np.ceil(y_max_1)) + 1):
            ax1.axhline(y=y, color='k', linewidth=0.5, alpha=0.12, zorder=1)
    else:
        # add text warning to plot if y-range is too large to show gridlines - solution likely wrong
        ax1.text(0.5, 0.9, 'WARNING: Large y-range may indicate incorrect solution',
                 transform=ax1.transAxes, color='red', fontsize=10, ha='center')
    ax1.set_ylim(y_min_1, y_max_1)

    # ========== Panel 2: Residuals ==========
    # Plot residuals for new observations
    scatter2 = ax2.scatter(diag_times[is_inlier], residuals[is_inlier],
                          c=diag_inliers[is_inlier], cmap='RdYlGn', vmin=0, vmax=1,
                          s=60, edgecolors='blue', linewidths=1.5, label='new (inlier)', zorder=5)
    ax2.errorbar(diag_times[is_inlier], residuals[is_inlier], 
                yerr=diag_phase_sigma[is_inlier], fmt='none', 
                ecolor='blue', alpha=0.4, zorder=4, capsize=3, capthick=1)
    cbar2 = plt.colorbar(scatter2, ax=ax2, pad=0.01)
    cbar2.set_label('inlier probability', fontsize=10)
    
    if np.any(is_outlier):
        ax2.scatter(diag_times[is_outlier], residuals[is_outlier],
                   marker='X', s=30, c='red', edgecolors='darkred', linewidths=2,
                   label='new (outlier)', zorder=6)
        ax2.errorbar(diag_times[is_outlier], residuals[is_outlier], 
                    yerr=diag_phase_sigma[is_outlier], fmt='none', 
                    ecolor='red', alpha=0.4, zorder=4, capsize=3, capthick=1)
    # Add uncertainty bands in residual space (±1σ centered at 0)
    ax2.fill_between(diag_times, -diag_pred_sigmas, diag_pred_sigmas, 
                    color='green', alpha=0.2, label='±1σ band', zorder=2)
    ax2.axhline(y=0, color='green', linestyle='--', linewidth=1.5, alpha=0.7, zorder=4)
    
    ax2.set_xlabel('time (days)', fontsize=11)
    ax2.set_ylabel('residual (unwrapped obs - predicted mean)', fontsize=11)
    ax2.set_title('Residuals with Predictive Uncertainty', fontsize=12, fontweight='bold')
    ax2.legend(loc='best', fontsize=10)

    y_min_2, y_max_2 = ax2.get_ylim()
    if y_max_2 - y_min_2 < 1000:
        for y in range(int(np.floor (y_min_2)), int(np.ceil(y_max_2)) + 1):
            ax2.axhline(y=y, color='k', linewidth=0.5, alpha=0.12, zorder=1)
    
    ax2.set_ylim(y_min_2, y_max_2)

    add_glitch_markers((ax1, ax2), glitch_epochs)
    
    plt.tight_layout()
    plot_path = output_path.replace('.csv', '.png')
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"wrote_plot {plot_path}")
    return plot_path


def infer_pulsar_name(par_path, explicit_name=None):
    if explicit_name:
        return explicit_name
    return os.path.splitext(os.path.basename(par_path))[0]


def infer_run_id(explicit_run_id=None):
    if explicit_run_id:
        return explicit_run_id
    return f"run_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"


def write_manifest(manifest_path, payload):
    if manifest_path is None:
        return
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    with open(manifest_path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def write_complete_marker(marker_path):
    if marker_path is None:
        return
    os.makedirs(os.path.dirname(os.path.abspath(marker_path)), exist_ok=True)
    with open(marker_path, "w") as handle:
        handle.write("complete\n")


class InteractivePhaseUI:
    def __init__(
        self,
        times,
        wrapped_phase,
        phase_sigma,
        trusted_mask,
        new_indices,
        covariance,
        wrap_options,
        args,
        newtim_path,
        output_path,
        output_tim_path,
        best_particle,
        diagnostics,
        glitch_epochs=None,
    ):
        self.times = np.asarray(times)
        self.wrapped_phase = np.asarray(wrapped_phase)
        self.phase_sigma = np.asarray(phase_sigma)
        self.trusted_mask = np.asarray(trusted_mask)
        self.new_indices = np.asarray(new_indices)
        self.covariance = covariance
        self.wrap_options = wrap_options
        self.args = args
        self.newtim_path = newtim_path
        self.output_path = output_path
        self.output_tim_path = output_tim_path
        self.best_particle = best_particle
        self.diagnostics = diagnostics
        self.glitch_epochs = list(glitch_epochs or [])
        self.constraints = {}
        self.selected_step = None
        self.hover_step = None
        self.dirty = False
        self.last_error = None
        self.save_on_exit = False

        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 10))
        self.status_text = self.fig.text(0.01, 0.01, "", fontsize=9)
        self.fig.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key_press)
        self.fig.suptitle(
            "Interactive Phase Association\n"
            "move mouse near point | i: force inlier | o: force outlier | =/-: adjust wrap | x: clear wrap | u: clear constraints | r: re-solve | w: write and quit | q: quit without saving",
            fontsize=12,
        )
        self.redraw()

    def current_constraint(self):
        active_step = self.active_step()
        if active_step is None:
            return None
        return self.constraints.setdefault(active_step, {})

    def active_step(self):
        if self.hover_step is not None:
            return self.hover_step
        return self.selected_step

    def get_nearest_step(self, event):
        if event.inaxes not in {self.ax1, self.ax2} or event.xdata is None or event.ydata is None:
            return None

        diag_times = np.array([row["time"] for row in self.diagnostics])
        if len(diag_times) == 0:
            return None

        diag_map_wraps = np.array([row["map_wrap"] for row in self.diagnostics])
        diag_baseline_wraps = np.array([row.get("baseline_wrap", 0.0) for row in self.diagnostics])
        diag_wrapped = np.array([row["wrapped_phase"] for row in self.diagnostics])
        diag_pred_means = np.array([row["predictive_mean"] for row in self.diagnostics])
        diag_unwrapped = diag_wrapped + diag_baseline_wraps + diag_map_wraps
        residuals = diag_unwrapped - diag_pred_means

        x_values = diag_times
        if event.inaxes is self.ax1:
            y_values = diag_unwrapped
        else:
            y_values = residuals

        x_span = max(float(np.max(x_values) - np.min(x_values)), 1e-12)
        y_span = max(float(np.max(y_values) - np.min(y_values)), 1e-12)
        dx = (x_values - event.xdata) / x_span
        dy = (y_values - event.ydata) / y_span
        distances = dx * dx + dy * dy
        return int(np.argmin(distances))

    def set_status(self, message):
        self.status_text.set_text(message)

    def mark_dirty(self, message):
        self.dirty = True
        self.set_status(f"{message} Press 'r' to re-solve.")
        self.redraw()

    def on_mouse_move(self, event):
        nearest_step = self.get_nearest_step(event)
        if nearest_step == self.hover_step:
            return
        self.hover_step = nearest_step
        if nearest_step is not None:
            self.selected_step = nearest_step
            self.set_status(f"Current point {nearest_step} at time {self.diagnostics[nearest_step]['time']:.8f}")
        self.redraw()

    def on_key_press(self, event):
        key = event.key
        if key == "q":
            plt.close(self.fig)
            return

        active_step = self.get_nearest_step(event)
        if active_step is not None:
            self.hover_step = active_step
            self.selected_step = active_step
        else:
            active_step = self.active_step()

        if active_step is None and key not in {"r", "s"}:
            self.set_status("Move the mouse near a point first.")
            self.redraw()
            return

        if key == "i":
            constraint = self.current_constraint()
            constraint["force_inlier"] = True
            constraint["force_outlier"] = False
            self.mark_dirty(f"Point {active_step} forced inlier.")
        elif key == "o":
            constraint = self.current_constraint()
            constraint["force_outlier"] = True
            constraint["force_inlier"] = False
            self.mark_dirty(f"Point {active_step} forced outlier.")
        elif key == "=":
            constraint = self.current_constraint()
            constraint["forced_wrap"] = int(constraint.get("forced_wrap", 0)) + 1
            self.mark_dirty(f"Point {active_step} forced wrap set to {constraint['forced_wrap']}.")
        elif key == "-":
            constraint = self.current_constraint()
            constraint["forced_wrap"] = int(constraint.get("forced_wrap", 0)) - 1
            self.mark_dirty(f"Point {active_step} forced wrap set to {constraint['forced_wrap']}.")
        elif key == "x":
            constraint = self.current_constraint()
            if "forced_wrap" in constraint:
                del constraint["forced_wrap"]
            if not constraint:
                self.constraints.pop(active_step, None)
            self.mark_dirty(f"Cleared forced wrap for point {active_step}.")
        elif key == "u":
            self.constraints.pop(active_step, None)
            self.mark_dirty(f"Cleared all constraints for point {active_step}.")
        elif key == "r":
            self.solve_current()
        elif key == "w":
            self.save_on_exit=True
            plt.close(self.fig)

    def solve_current(self):
        try:
            self.best_particle, self.diagnostics = solve_with_constraints(
                self.times,
                self.wrapped_phase,
                self.phase_sigma,
                self.trusted_mask,
                self.new_indices,
                self.covariance,
                self.wrap_options,
                self.args,
                constraints=self.constraints,
            )
            self.dirty = False
            self.last_error = None
            self.set_status(f"Solved with {len(self.constraints)} constrained points.")
        except Exception as exc:
            self.last_error = str(exc)
            self.set_status(f"Solve failed: {exc}")
        self.redraw()

  

    def redraw(self):
        self.ax1.clear()
        self.ax2.clear()

        diag_times = np.array([row["time"] for row in self.diagnostics])
        diag_map_wraps = np.array([row["map_wrap"] for row in self.diagnostics])
        diag_baseline_wraps = np.array([row.get("baseline_wrap", 0.0) for row in self.diagnostics])
        diag_wrapped = np.array([row["wrapped_phase"] for row in self.diagnostics])
        diag_pred_means = np.array([row["predictive_mean"] for row in self.diagnostics])
        diag_pred_sigmas = np.array([row["predictive_sigma"] for row in self.diagnostics])
        diag_inliers = np.array([row["inlier_probability"] for row in self.diagnostics])
        diag_outlier_flags = np.array([row["outlier_flag"] for row in self.diagnostics])
        diag_unwrapped = diag_wrapped + diag_baseline_wraps + diag_map_wraps
        residuals = diag_unwrapped - diag_pred_means

        trusted_idx = np.where(self.trusted_mask)[0]
        if len(trusted_idx) > 0:
            self.ax1.scatter(
                self.times[trusted_idx],
                self.wrapped_phase[trusted_idx],
                alpha=0.6,
                s=30,
                c="gray",
                label="trusted (wrapped)",
                zorder=2,
            )

        is_outlier = diag_outlier_flags > 0
        is_inlier = ~is_outlier

        if np.any(is_inlier):
            self.scatter_top = self.ax1.scatter(
                diag_times[is_inlier],
                diag_unwrapped[is_inlier],
                c=diag_inliers[is_inlier],
                cmap="RdYlGn",
                vmin=0,
                vmax=1,
                s=30,
                edgecolors="blue",
                linewidths=1.5,
                label="new (inlier)",
                zorder=4,
            )
        else:
            self.scatter_top = self.ax1.scatter([], [])

        if np.any(is_outlier):
            self.ax1.scatter(
                diag_times[is_outlier],
                diag_unwrapped[is_outlier],
                marker="X",
                s=30,
                c="red",
                edgecolors="darkred",
                linewidths=2,
                label="new (outlier)",
                zorder=5,
            )
            self.ax2.scatter(
                diag_times[is_outlier],
                residuals[is_outlier],
                marker="X",
                s=30,
                c="red",
                edgecolors="darkred",
                linewidths=2,
                label="new (outlier)",
                zorder=5,
            )

        self.scatter_bottom = self.ax2.scatter(
            diag_times,
            residuals,
            c=diag_inliers,
            cmap="RdYlGn",
            vmin=0,
            vmax=1,
            s=30,
            edgecolors="blue",
            linewidths=1.0,
            label="new points",
            zorder=4,
        )

        pred_upper = diag_pred_means + diag_pred_sigmas
        pred_lower = diag_pred_means - diag_pred_sigmas
        self.ax1.plot(diag_times, diag_pred_means, "g-", linewidth=2, label="predictive mean", alpha=0.8, zorder=3)

        self.ax1.plot(diag_times, diag_pred_means+1, "g:", linewidth=1, alpha=0.5, zorder=3)
        self.ax1.plot(diag_times, diag_pred_means-1, "g:", linewidth=1, alpha=0.5, zorder=3)


        self.ax1.fill_between(diag_times, pred_lower, pred_upper, color="green", alpha=0.2, label="±1σ band", zorder=1)
        self.ax2.fill_between(diag_times, -diag_pred_sigmas, diag_pred_sigmas, color="green", alpha=0.2, label="±1σ band", zorder=1)
        self.ax2.axhline(y=0, color="green", linestyle="--", linewidth=1.5, alpha=0.7, zorder=2)

        for step_index, constraint in self.constraints.items():
            time_value = diag_times[step_index]
            y_value = diag_unwrapped[step_index]
            edge_color = "orange"
            if constraint.get("force_outlier"):
                edge_color = "red"
            elif constraint.get("force_inlier"):
                edge_color = "limegreen"
            self.ax1.scatter([time_value], [y_value], s=180, facecolors="none", edgecolors=edge_color, linewidths=2.0, zorder=6)
            wrap_value = constraint.get("forced_wrap")
            if wrap_value is not None:
                self.ax1.text(time_value, y_value, f" {wrap_value:+d}", color=edge_color, fontsize=9, zorder=7)

        highlight_step = self.active_step()
        if highlight_step is not None and 0 <= highlight_step < len(diag_times):
            self.ax1.scatter(
                [diag_times[highlight_step]],
                [diag_unwrapped[highlight_step]],
                s=220,
                facecolors="none",
                edgecolors="black",
                linewidths=2.5,
                zorder=8,
            )
            self.ax2.scatter(
                [diag_times[highlight_step]],
                [residuals[highlight_step]],
                s=220,
                facecolors="none",
                edgecolors="black",
                linewidths=2.5,
                zorder=8,
            )

        self.ax1.set_xlabel("time (days)")
        self.ax1.set_ylabel("phase (unwrapped)")
        self.ax1.set_title("Interactive Phase Association")
        self.ax1.grid(True, alpha=0.3)
        self.ax1.legend(loc="best", fontsize=9)

        self.ax2.set_xlabel("time (days)")
        self.ax2.set_ylabel("residual (unwrapped obs - predicted mean)")
        self.ax2.set_title("Residuals")
        self.ax2.grid(True, alpha=0.3)
        self.ax2.legend(loc="best", fontsize=9)

        add_glitch_markers((self.ax1, self.ax2), self.glitch_epochs)

        self.fig.canvas.draw_idle()

    def run(self):
        plt.show()
        return self.save_on_exit, self.best_particle, self.diagnostics



def main():
    args = parse_args()
    
    # Convert input paths to absolute paths so they're found when tempo2 runs in a temp directory
    par_path = os.path.abspath(args.par)
    tim_path = os.path.abspath(args.tim)
    newtim_path = os.path.abspath(args.newtim)
    
    if args.fit_pm:
        args.fit_pos = True

    if not os.path.exists(par_path):
        raise FileNotFoundError(f"Par file not found: {par_path}")
    if not os.path.exists(tim_path):
        raise FileNotFoundError(f"Tim file not found: {tim_path}")
    if not os.path.exists(newtim_path):
        raise FileNotFoundError(f"Newtim file not found: {newtim_path}")
    if args.covariance_scale <= 0.0:
        raise ValueError(f"Covariance scale must be positive, got {args.covariance_scale}")
    
    gp_parameters = read_gp_parameters(par_path)
    wrap_options = np.arange(args.wrap_min, args.wrap_max + 1, dtype=int)

    glitch_index=[]
    if args.ephindex is not None:
        glitch_index = read_ephindex(args.ephindex)

    with tempfile.TemporaryDirectory(prefix="phase_assoc_") as working_directory:
        trusted = run_tempo2_exportres(par_path, tim_path, working_directory)
    with tempfile.TemporaryDirectory(prefix="phase_assoc_") as working_directory:
        combined = run_tempo2_exportres(par_path, newtim_path, working_directory)

    new_mask = identify_new_observations(
        combined["times"],
        combined["identifier"],
        combined["frequency_mhz"],
        trusted["times"],
        trusted["identifier"],
        trusted["frequency_mhz"],
    )
    if not np.any(new_mask):
        raise ValueError("No new observations were identified in the combined exportres output")

    order = np.argsort(combined["times"])
    times = combined["times"][order]
    wrapped_phase = combined["phase"][order]
    phase_sigma = combined["sigma"][order]
    identifiers = combined["identifier"][order]
    frequencies_mhz = combined["frequency_mhz"][order]
    trusted_mask = ~new_mask[order]
    new_indices = np.flatnonzero(new_mask[order])

    wrapped_phase = wrapped_phase - np.mean(wrapped_phase[trusted_mask])

    F0_hz = gp_parameters["F0"]
    covariance = args.covariance_scale * F0_hz**2 * cholspec.getC(
        times,
        gp_parameters["TNRedAmp"],
        gp_parameters["TNRedGam"],
        fc_yr=args.fc_yr,
    )
    best_particle, diagnostics = associate_phases(
        times,
        wrapped_phase,
        phase_sigma,
        trusted_mask,
        new_indices,
        covariance,
        wrap_options,
        args,
        identifiers=identifiers,
        frequency_mhz=frequencies_mhz,
    )

    output_path = args.output
    if output_path is None:
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            output_path = os.path.join(
                args.output_dir,
                os.path.basename(os.path.splitext(newtim_path)[0] + ".phase_association.csv"),
            )
        else:
            output_path = os.path.splitext(newtim_path)[0] + ".phase_association.csv"

    output_tim_path = args.output_tim
    if output_tim_path is None:
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            output_tim_path = os.path.join(
                args.output_dir,
                os.path.basename(os.path.splitext(newtim_path)[0] + ".phase_association.tim"),
            )
        else:
            output_tim_path = os.path.splitext(newtim_path)[0] + ".phase_association.tim"

    if args.interactive:
        ensure_interactive_matplotlib_backend()
        ui = InteractivePhaseUI(
            times,
            wrapped_phase,
            phase_sigma,
            trusted_mask,
            new_indices,
            covariance,
            wrap_options,
            args,
            newtim_path,
            output_path,
            output_tim_path,
            best_particle,
            diagnostics,
            glitch_epochs=glitch_index,
        )
        save_on_exit, best_particle, diagnostics = ui.run()
        if not save_on_exit:
            print("interactive_session_closed_without_saving")
            return

    write_diagnostics(diagnostics, output_path)
    write_updated_tim(newtim_path, output_tim_path, diagnostics)
    plot_path = plot_diagnostics(
        times,
        phase_sigma,
        wrapped_phase,
        diagnostics,
        trusted_mask,
        new_indices,
        output_path,
        glitch_epochs=glitch_index,
    )
    print_summary(best_particle, diagnostics)
    trusted_count = int(np.sum(trusted_mask))
    new_count = int(len(new_indices))
    print(f"trusted_observations {trusted_count}")
    print(f"new_observations {new_count}")
    print(f"wrote_diagnostics {output_path}")
    print(f"wrote_output_tim {output_tim_path}")

    run_id = infer_run_id(args.run_id)
    pulsar_name = infer_pulsar_name(par_path, args.pulsar)
    manifest = {
        "run_id": run_id,
        "pulsar": pulsar_name,
        "created_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "inputs": {
            "par": par_path,
            "tim": tim_path,
            "newtim": newtim_path,
        },
        "parameters": {
            "fc_yr": args.fc_yr,
            "covariance_scale": args.covariance_scale,
            "wrap_min": args.wrap_min,
            "wrap_max": args.wrap_max,
            "wrap_prior_sigma": args.wrap_prior_sigma,
            "particle_min_keep": args.particle_min_keep,
            "particle_limit": args.particle_limit,
            "outlier_prob": args.outlier_prob,
            "outlier_sigma": args.outlier_sigma,
            "mean_poly_order": args.mean_poly_order,
            "fit_pos": bool(args.fit_pos),
            "fit_pm": bool(args.fit_pm),
            "interactive": bool(args.interactive),
        },
        "summary": {
            "trusted_observations": trusted_count,
            "new_observations": new_count,
            "best_wrap_sequence": best_particle.assignments,
            "best_particle_log_weight": float(best_particle.log_weight),
        },
        "outputs": {
            "diagnostics_csv": os.path.basename(output_path),
            "output_tim": os.path.basename(output_tim_path),
            "diagnostic_plot": os.path.basename(plot_path) if plot_path is not None else None,
        },
    }
    write_manifest(args.manifest_output, manifest)
    write_complete_marker(args.complete_marker)
    if args.manifest_output:
        print(f"wrote_manifest {args.manifest_output}")
    if args.complete_marker:
        print(f"wrote_complete_marker {args.complete_marker}")



if __name__ == "__main__":
    main()

