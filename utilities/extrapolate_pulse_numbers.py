#!/usr/bin/env python3
import argparse
import csv
import importlib.util
import math
import os
import subprocess
import sys
import tempfile

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
    parser.add_argument("--time-tolerance", type=float, default=1e-6, help="matching tolerance for identifying new TOAs in days")
    parser.add_argument("--output", default=None, help="optional CSV file for per-observation diagnostics")
    parser.add_argument("--output-tim", default=None, help="optional output tim file with outlier comments and -pnadd wrap annotations")
    parser.add_argument("--interactive", action="store_true", help="launch a basic interactive matplotlib UI for manual constraints and re-solving")
    parser.add_argument(
        "--mean-poly-order",
        type=int,
        default=-1,
        help="polynomial order for GP parametric mean (0=constant, 1=linear, 2=quadratic; negative disables and uses zero-mean)",
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



def run_tempo2_exportres(par_path, tim_path, working_directory):
    command = [
        "tempo2",
        "-output",
        "exportres",
        "-f",
        par_path,
        tim_path,
        "-writeres",
        "-nofit",
        "-npsr",
        "1",
        "-nobs",
        "50000",
    ]
    try:
        subprocess.run(command, cwd=working_directory, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip() or "tempo2 exportres failed") from exc
    out_res = os.path.join(working_directory, "out.res")
    data = np.loadtxt(out_res, ndmin=2, usecols=(0, 1, 2))
    print(data[:,1])
    if data.shape[1] < 3:
        raise ValueError("tempo2 out.res does not contain the expected phase columns")
    return {
        "times": data[:, 0],
        "phase": data[:, 1],
        "sigma": data[:, 2],
        "raw": data,
    }


def identify_new_observations(all_times, trusted_times, tolerance):
    trusted_times = np.sort(np.asarray(trusted_times, dtype=float))
    mask = np.ones(len(all_times), dtype=bool)
    for index, time_value in enumerate(all_times):
        insertion_point = np.searchsorted(trusted_times, time_value)
        nearby = []
        if insertion_point < len(trusted_times):
            nearby.append(abs(trusted_times[insertion_point] - time_value))
        if insertion_point > 0:
            nearby.append(abs(trusted_times[insertion_point - 1] - time_value))
        if nearby and min(nearby) <= tolerance:
            mask[index] = False
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


def predictive_observation_stats(candidate_index, observed_indices, observed_values, covariance, noise_variance, all_times, mean_poly_order):
    prior_mean = 0.0
    prior_variance = covariance[candidate_index, candidate_index] + noise_variance[candidate_index]
    if not observed_indices:
        return prior_mean, prior_variance

    history = np.asarray(observed_indices, dtype=int)
    observed = np.asarray(observed_values, dtype=float)
    system_covariance = covariance[np.ix_(history, history)] + np.diag(noise_variance[history])
    cross_covariance = covariance[candidate_index, history]

    if mean_poly_order < 0:
        solved_mean = np.linalg.solve(system_covariance, observed)
        solved_cross = np.linalg.solve(system_covariance, cross_covariance)
        predictive_mean = float(cross_covariance.dot(solved_mean))
        predictive_variance = float(prior_variance - cross_covariance.dot(solved_cross))
        return predictive_mean, max(predictive_variance, 1e-12)

    reference_time = float(np.min(all_times))
    time_span = float(np.max(all_times) - np.min(all_times))
    time_scale = max(time_span, 1.0)

    history_times = np.asarray(all_times, dtype=float)[history]
    candidate_time = np.asarray([all_times[candidate_index]], dtype=float)
    design_history = build_polynomial_design(history_times, mean_poly_order, reference_time, time_scale)
    design_candidate = build_polynomial_design(candidate_time, mean_poly_order, reference_time, time_scale)[0]

    solved_cross = np.linalg.solve(system_covariance, cross_covariance)

    # GLS estimate of the polynomial mean coefficients.
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
    cumulitive_wrap,
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
        unwrapped_value = observation + wrap + cumulitive_wrap
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
        )
        cumulative_wrap = np.sum(particle.assignments) if particle.assignments else 0
        candidate_rows, inlier_probability, marginal_log_likelihood = evaluate_wrap_candidates(
            observation,
            predictive_mean,
            predictive_variance,
            constrained_wrap_options,
            cumulative_wrap,
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
    delta = 100.0 
    trimmed = [p for p in ordered if score(p) >= max_score - delta]
    if len(ordered) < particle_min_keep:
        trimmed = ordered[:particle_min_keep]

    min_keep = max(1, int(particle_min_keep))
    max_keep = max(1, int(particle_limit))
    keep_count = min(max_keep, max(min_keep, len(trimmed)))
    return trimmed[:keep_count]




def associate_phases(all_times, wrapped_phase, phase_sigma, trusted_mask, new_indices, covariance, wrap_options, args, constraints=None):
    noise_variance = np.square(phase_sigma)
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
        row["predictive_mean"] = float(final_diag["predictive_mean"])
        row["predictive_sigma"] = float(final_diag["predictive_sigma"])
        row["outlier_flag"] = int(final_diag["branch_is_outlier"])
    return best_particle, diagnostics


def write_diagnostics(rows, output_path):
    fieldnames = [
        "time",
        "wrapped_phase",
        "phase_sigma",
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


def extract_tim_mjd(line):
    tokens = line.split()
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            continue
        if 30000.0 <= value <= 100000.0:
            return value
    return None


def upsert_pnadd(line, wrap):
    tokens = line.split()
    cleaned = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-pnadd":
            index += 2
            continue
        cleaned.append(token)
        index += 1

    if wrap != 0:
        cleaned.extend(["-pnadd", str(-int(wrap))])
    return " ".join(cleaned)


def write_updated_tim(input_tim_path, output_tim_path, diagnostics, tolerance):
    decisions = [
        {
            "time": float(row["time"]),
            "wrap": int(row["map_wrap"]),
            "outlier": int(row["outlier_flag"]),
            "used": False,
        }
        for row in diagnostics
    ]

    with open(input_tim_path) as input_handle, open(output_tim_path, "w") as output_handle:
        for raw_line in input_handle:
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            if not stripped or stripped.startswith("FORMAT"):
                output_handle.write(raw_line)
                continue

            toa_time = extract_tim_mjd(stripped)
            if toa_time is None:
                output_handle.write(raw_line)
                continue

            best_index = None
            best_delta = None
            for index, decision in enumerate(decisions):
                if decision["used"]:
                    continue
                delta = abs(decision["time"] - toa_time)
                if delta <= tolerance and (best_delta is None or delta < best_delta):
                    best_index = index
                    best_delta = delta

            if best_index is None:
                output_handle.write(raw_line)
                continue

            decision = decisions[best_index]
            decision["used"] = True

            updated = upsert_pnadd(stripped, decision["wrap"])
            if decision["outlier"]:
                updated = "C " + updated
            output_handle.write(updated + "\n")


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
    print("index time map_wrap wrap_probability inlier_probability predictive_mean predictive_sigma outlier")
    for index, row in enumerate(diagnostics, start=1):
        print(
            f"{index:5d} {row['time']:15.8f} {row['map_wrap']:8d} "
            f"{row['map_wrap_probability']:16.6f} {row['inlier_probability']:18.6f} "
            f"{row['predictive_mean']:16.6f} {row['predictive_sigma']:16.6f} {row['outlier_flag']:7d}"
        )
    print("best_wrap_sequence", " ".join(str(value) for value in best_particle.assignments))


def plot_diagnostics(times, wrapped_phase, diagnostics, trusted_mask, new_indices, output_path):
    """Generate diagnostic plot showing residuals, predictions, and outlier flags."""
    if not HAS_MATPLOTLIB:
        return
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
    
    # Convert to arrays for indexing
    times = np.asarray(times)
    wrapped_phase = np.asarray(wrapped_phase)
    trusted_mask = np.asarray(trusted_mask)
    
    # Extract diagnostic info for new observations
    diag_times = np.array([d["time"] for d in diagnostics])
    diag_map_wraps = np.array([d["map_wrap"] for d in diagnostics])
    diag_wrapped = np.array([d["wrapped_phase"] for d in diagnostics])
    diag_pred_means = np.array([d["predictive_mean"] for d in diagnostics])
    diag_pred_sigmas = np.array([d["predictive_sigma"] for d in diagnostics])
    diag_inliers = np.array([d["inlier_probability"] for d in diagnostics])
    diag_outlier_flags = np.array([d["outlier_flag"] for d in diagnostics])
    
    # Compute unwrapped phase for new observations: wrapped + map_wrap
    diag_unwrapped = diag_wrapped + np.cumsum(diag_map_wraps)
    
    # Compute residuals: unwrapped - predicted_mean
    residuals = diag_unwrapped - diag_pred_means
    
    # ========== Panel 1: Unwrapped phase with predictions ==========
    # Plot trusted observations (wrapped phase, no predictions)
    trusted_idx = np.where(trusted_mask)[0]
    if len(trusted_idx) > 0:
        ax1.scatter(times[trusted_idx], wrapped_phase[trusted_idx], 
                   alpha=0.6, s=30, c='gray', label='trusted (wrapped)', zorder=3)
    
    # Plot new observations (color by inlier probability)
    is_outlier = diag_outlier_flags > 0
    is_inlier = ~is_outlier
    
    if np.any(is_inlier):
        scatter1 = ax1.scatter(diag_times[is_inlier], diag_unwrapped[is_inlier],
                              c=diag_inliers[is_inlier], cmap='RdYlGn', vmin=0, vmax=1,
                              s=60, edgecolors='blue', linewidths=1.5, label='new (inlier)', zorder=5)
        cbar1 = plt.colorbar(scatter1, ax=ax1, pad=0.01)
        cbar1.set_label('inlier probability', fontsize=10)
    
    if np.any(is_outlier):
        ax1.scatter(diag_times[is_outlier], diag_unwrapped[is_outlier],
                   marker='X', s=120, c='red', edgecolors='darkred', linewidths=2,
                   label='new (outlier)', zorder=6)
    
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
    ax1.grid(True, alpha=0.3)
    
    # ========== Panel 2: Residuals ==========
    # Plot residuals for new observations
    scatter2 = ax2.scatter(diag_times[is_inlier], residuals[is_inlier],
                          c=diag_inliers[is_inlier], cmap='RdYlGn', vmin=0, vmax=1,
                          s=60, edgecolors='blue', linewidths=1.5, label='new (inlier)', zorder=5)
    cbar2 = plt.colorbar(scatter2, ax=ax2, pad=0.01)
    cbar2.set_label('inlier probability', fontsize=10)
    
    if np.any(is_outlier):
        ax2.scatter(diag_times[is_outlier], residuals[is_outlier],
                   marker='X', s=120, c='red', edgecolors='darkred', linewidths=2,
                   label='new (outlier)', zorder=6)
    
    # Add uncertainty bands in residual space (±1σ centered at 0)
    ax2.fill_between(diag_times, -diag_pred_sigmas, diag_pred_sigmas, 
                    color='green', alpha=0.2, label='±1σ band', zorder=2)
    ax2.axhline(y=0, color='green', linestyle='--', linewidth=1.5, alpha=0.7, zorder=4)
    
    ax2.set_xlabel('time (days)', fontsize=11)
    ax2.set_ylabel('residual (unwrapped obs - predicted mean)', fontsize=11)
    ax2.set_title('Residuals with Predictive Uncertainty', fontsize=12, fontweight='bold')
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plot_path = output_path.replace('.csv', '.png')
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"wrote_plot {plot_path}")


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
        self.constraints = {}
        self.selected_step = None
        self.hover_step = None
        self.dirty = False
        self.last_error = None
        self.saved = False

        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 10))
        self.status_text = self.fig.text(0.01, 0.01, "", fontsize=9)
        self.fig.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key_press)
        self.fig.suptitle(
            "Interactive Phase Association\n"
            "move mouse near point | i: force inlier | o: force outlier | =/-: adjust wrap | x: clear wrap | u: clear constraints | r: re-solve | s: save | q: quit",
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
        diag_wrapped = np.array([row["wrapped_phase"] for row in self.diagnostics])
        diag_pred_means = np.array([row["predictive_mean"] for row in self.diagnostics])
        diag_unwrapped = diag_wrapped + np.cumsum(diag_map_wraps)
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
        elif key == "s":
            self.save_outputs()

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

    def save_outputs(self):
        if self.dirty:
            self.solve_current()
            if self.last_error is not None:
                return
        write_diagnostics(self.diagnostics, self.output_path)
        write_updated_tim(self.newtim_path, self.output_tim_path, self.diagnostics, self.args.time_tolerance)
        plot_diagnostics(self.times, self.wrapped_phase, self.diagnostics, self.trusted_mask, self.new_indices, self.output_path)
        self.saved = True
        self.set_status(f"Saved outputs to {self.output_path} and {self.output_tim_path}")
        self.redraw()

    def redraw(self):
        self.ax1.clear()
        self.ax2.clear()

        diag_times = np.array([row["time"] for row in self.diagnostics])
        diag_map_wraps = np.array([row["map_wrap"] for row in self.diagnostics])
        diag_wrapped = np.array([row["wrapped_phase"] for row in self.diagnostics])
        diag_pred_means = np.array([row["predictive_mean"] for row in self.diagnostics])
        diag_pred_sigmas = np.array([row["predictive_sigma"] for row in self.diagnostics])
        diag_inliers = np.array([row["inlier_probability"] for row in self.diagnostics])
        diag_outlier_flags = np.array([row["outlier_flag"] for row in self.diagnostics])
        diag_unwrapped = diag_wrapped + np.cumsum(diag_map_wraps)
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
                s=60,
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
                s=120,
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
                s=120,
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
            s=50,
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

        self.fig.canvas.draw_idle()

    def run(self):
        plt.show()
        return self.saved, self.best_particle, self.diagnostics



def main():
    args = parse_args()
    
    # Convert input paths to absolute paths so they're found when tempo2 runs in a temp directory
    par_path = os.path.abspath(args.par)
    tim_path = os.path.abspath(args.tim)
    newtim_path = os.path.abspath(args.newtim)
    
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

    with tempfile.TemporaryDirectory(prefix="phase_assoc_") as working_directory:

        
        trusted = run_tempo2_exportres(par_path, tim_path, working_directory)
        combined = run_tempo2_exportres(par_path, newtim_path, working_directory)

    new_mask = identify_new_observations(combined["times"], trusted["times"], args.time_tolerance)
    if not np.any(new_mask):
        raise ValueError("No new observations were identified in the combined exportres output")

    order = np.argsort(combined["times"])
    times = combined["times"][order]
    wrapped_phase = combined["phase"][order]
    phase_sigma = combined["sigma"][order]
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
    )

    output_path = args.output
    if output_path is None:
        output_path = os.path.splitext(newtim_path)[0] + ".phase_association.csv"

    output_tim_path = args.output_tim
    if output_tim_path is None:
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
        )
        saved, best_particle, diagnostics = ui.run()
        if not saved:
            print("interactive_session_closed_without_saving")
            return

    write_diagnostics(diagnostics, output_path)
    write_updated_tim(newtim_path, output_tim_path, diagnostics, args.time_tolerance)
    plot_diagnostics(times, wrapped_phase, diagnostics, trusted_mask, new_indices, output_path)
    print_summary(best_particle, diagnostics)
    print(f"trusted_observations {int(np.sum(trusted_mask))}")
    print(f"new_observations {int(len(new_indices))}")
    print(f"wrote_diagnostics {output_path}")
    print(f"wrote_output_tim {output_tim_path}")



if __name__ == "__main__":
    main()

