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
    matplotlib.use('Agg')  # non-interactive backend
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
    parser.add_argument("--particle-limit", type=int, default=128, help="maximum number of retained particles after pruning")
    parser.add_argument("--outlier-prob", type=float, default=0.05, help="mixture weight assigned to the broad outlier component")
    parser.add_argument("--outlier-sigma", type=float, default=3.0, help="sigma of the broad outlier Gaussian in phase units")
    parser.add_argument("--time-tolerance", type=float, default=1e-6, help="matching tolerance for identifying new TOAs in days")
    parser.add_argument("--output", default=None, help="optional CSV file for per-observation diagnostics")
    parser.add_argument("--output-tim", default=None, help="optional output tim file with outlier comments and -pnadd wrap annotations")
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

    missing = {"TNRedAmp", "TNRedGam"} - set(parameters)
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required red-noise parameters in par file: {missing_names}")
    return parameters


def build_combined_tim(tim_path, newtim_path, combined_path):
    with open(combined_path, "w") as output_handle:
        with open(tim_path) as trusted_handle:
            for line in trusted_handle:
                stripped = line.rstrip()
                if stripped.startswith("FORMAT"):
                    output_handle.write(line if line.endswith("\n") else line + "\n")
                    continue
                if stripped:
                    output_handle.write(f"{stripped} -ds GOOD\n")
                else:
                    output_handle.write(line)

        with open(newtim_path) as new_handle:
            for line in new_handle:
                if line.startswith("FORMAT"):
                    continue
                stripped = line.rstrip()
                if stripped:
                    output_handle.write(f"{stripped} -ds NEW\n")
                else:
                    output_handle.write(line)


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


def predictive_observation_stats(candidate_index, observed_indices, observed_values, covariance, noise_variance):
    prior_mean = 0.0
    prior_variance = covariance[candidate_index, candidate_index] + noise_variance[candidate_index]
    if not observed_indices:
        return prior_mean, prior_variance

    history = np.asarray(observed_indices, dtype=int)
    observed = np.asarray(observed_values, dtype=float)
    system_covariance = covariance[np.ix_(history, history)] + np.diag(noise_variance[history])
    cross_covariance = covariance[candidate_index, history]
    solved_mean = np.linalg.solve(system_covariance, observed)
    solved_cross = np.linalg.solve(system_covariance, cross_covariance)
    predictive_mean = float(cross_covariance.dot(solved_mean))
    predictive_variance = float(prior_variance - cross_covariance.dot(solved_cross))
    return predictive_mean, max(predictive_variance, 1e-12)


def evaluate_wrap_candidates(observation, predictive_mean, predictive_variance, wrap_options, cumulitive_wrap, outlier_probability, outlier_sigma):
    signal_log_weights = []
    mixture_log_weights = []
    candidate_rows = []

    signal_scale = math.log(max(1.0 - outlier_probability, 1e-12))
    outlier_scale = math.log(max(outlier_probability, 1e-12))
    outlier_variance = outlier_sigma ** 2

    for wrap in wrap_options:
        unwrapped_value = observation + wrap + cumulitive_wrap
        signal_log = signal_scale + gaussian_logpdf(unwrapped_value, predictive_mean, predictive_variance)
        outlier_log = outlier_scale + gaussian_logpdf(unwrapped_value, 0.0, outlier_variance)
        mixture_log = np.logaddexp(signal_log, outlier_log)
        signal_log_weights.append(signal_log)
        mixture_log_weights.append(mixture_log)
        candidate_rows.append(
            {
                "wrap": int(wrap),
                "unwrapped_value": float(unwrapped_value),
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


def prune_particles(particles, particle_limit):
    ordered = sorted(particles, key=lambda particle: particle.log_weight, reverse=True)
    return ordered[:particle_limit]


def resample_particles(particles, particle_limit, rng):
    log_weights = [particle.log_weight for particle in particles]
    normalized_weights, log_normalizer = normalize_log_weights(log_weights)
    ess = 1.0 / np.sum(normalized_weights ** 2)
    if ess >= 0.5 * len(particles) or len(particles) <= 1:
        return particles, ess

    particle_count = min(len(particles), particle_limit)
    indices = systematic_resample(normalized_weights, rng)[:particle_count]
    resampled = []
    reset_weight = log_normalizer - math.log(particle_count)
    for index in indices:
        original = particles[index]
        resampled.append(
            Particle(
                assignments=list(original.assignments),
                observed_indices=list(original.observed_indices),
                observed_values=list(original.observed_values),
                log_weight=reset_weight,
                diagnostics=list(original.diagnostics),
            )
        )
    return resampled, ess


def associate_phases(all_times, wrapped_phase, phase_sigma, trusted_mask, new_indices, covariance, wrap_options, args):
    rng = np.random.default_rng(12345)
    noise_variance = np.square(phase_sigma)
    trusted_indices = np.flatnonzero(trusted_mask)
    trusted_values = wrapped_phase[trusted_mask]
    base_particle = Particle([], trusted_indices.tolist(), trusted_values.tolist(), 0.0, [])
    particles = [base_particle]
    diagnostics = []
    wrap_to_index = {int(wrap): index for index, wrap in enumerate(wrap_options)}

    for step_number, observation_index in tqdm(enumerate(new_indices), total=len(new_indices), desc="Processing new observations"):
        previous_log_total = logsumexp([particle.log_weight for particle in particles])
        proposal_particles = []
        per_wrap_log_weights = np.full(len(wrap_options), -np.inf, dtype=float)
        inlier_log_weight_total = -np.inf
        proposal_log_weight_total = -np.inf
        observation = wrapped_phase[observation_index]

        for particle in particles:
            predictive_mean, predictive_variance = predictive_observation_stats(
                observation_index,
                particle.observed_indices,
                particle.observed_values,
                covariance,
                noise_variance,
            )
            cumulitive_wrap = np.sum(particle.assignments) if particle.assignments else 0
            candidate_rows, inlier_probability, marginal_log_likelihood = evaluate_wrap_candidates(
                observation,
                predictive_mean,
                predictive_variance,
                wrap_options,
                cumulitive_wrap,
                args.outlier_prob,
                args.outlier_sigma,
            )

            for candidate in candidate_rows:
                wrap = candidate["wrap"]
                wrap_index = wrap_to_index[wrap]

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
            wrap=0
            wrap_index = wrap_to_index[wrap]
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
            raise RuntimeError("No proposal particles were generated during phase association")

        particles = prune_particles(proposal_particles, args.particle_limit)
        particles, ess = resample_particles(particles, args.particle_limit, rng)

        wrap_posteriors = np.exp(per_wrap_log_weights - proposal_log_weight_total)
        aggregated_inlier = float(np.exp(inlier_log_weight_total - proposal_log_weight_total))
        aggregated_marginal = float(proposal_log_weight_total - previous_log_total)
        map_wrap_index = int(np.argmax(wrap_posteriors))
        map_wrap = int(wrap_options[map_wrap_index])


        diagnostics.append(
            {
                "time": float(all_times[observation_index]),
                "wrapped_phase": float(observation),
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
        ax1.scatter(diag_times[is_outlier], diag_wrapped[is_outlier],
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
        combined_tim = os.path.join(working_directory, "combined.tim")
        build_combined_tim(tim_path, newtim_path, combined_tim)
        
        trusted = run_tempo2_exportres(par_path, tim_path, working_directory)
        combined = run_tempo2_exportres(par_path, combined_tim, working_directory)

    new_mask = identify_new_observations(combined["times"], trusted["times"], args.time_tolerance)
    if not np.any(new_mask):
        raise ValueError("No new observations were identified in the combined exportres output")

    order = np.argsort(combined["times"])
    times = combined["times"][order]
    wrapped_phase = combined["phase"][order]
    phase_sigma = combined["sigma"][order]
    trusted_mask = ~new_mask[order]
    new_indices = np.flatnonzero(new_mask[order])

    covariance = args.covariance_scale * cholspec.getC(
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

