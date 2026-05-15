#!/usr/bin/env python3

import psrchive
import numpy as np

def make_harmonic_template(archive_path, output_path, harmonic_number):
    # Load the archive
    archive = psrchive.Archive_load(archive_path)
    
    # Get the total intensity profile (Stokes I)
    profile = archive.get_Profile(0, 0, 0).get_amps()  # Assuming single subint, single pol, single freq
    

    # use FFT to interpolate to harmonic_number times the original resolution
    fft_result = np.fft.rfft(profile)  # Compute the FFT of the profile
    # Create a new array for the harmonic template with zero-padding
    harmonic_fft = np.zeros(harmonic_number * len(fft_result), dtype=complex)
    harmonic_fft[:len(fft_result)] = fft_result  # Copy the original FFT coefficients
    # Perform the inverse FFT to get the harmonic template
    harmonic_template = np.fft.irfft(harmonic_fft, n=harmonic_number * len(profile))
    # Fold the template back to the original length by summing over the harmonics
    folded_template = np.zeros_like(profile)
    for i in range(harmonic_number):
        folded_template += harmonic_template[i*len(profile):(i+1)*len(profile)]
    folded_template /= np.amax(folded_template)  # Normalize the template

    profile[:] = folded_template  # Replace the original profile with the harmonic template

    archive.unload(output_path)  # Save the modified archive to the output path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create a harmonic template from a pulsar archive.")
    parser.add_argument("archive_path", help="Path to the input pulsar archive")
    parser.add_argument("--output-path", help="Path to save the output harmonic template archive")
    parser.add_argument("--harmonic-number", "-H", type=int, default=2, help="Number of harmonics to include in the template")
    args = parser.parse_args()

    if args.output_path is None:
        # take the input file and prepend with "h{harmonic_number}_"
        # but keep the same directory and extension
        import os
        dir_name, file_name = os.path.split(args.archive_path)
        name, ext = os.path.splitext(file_name)
        args.output_path = os.path.join(dir_name, f"h{args.harmonic_number}_{name}{ext}")
    print(f"Creating harmonic template folded at {args.harmonic_number} harmonic from {args.archive_path} and saving to {args.output_path}")
    make_harmonic_template(args.archive_path, args.output_path, args.harmonic_number)