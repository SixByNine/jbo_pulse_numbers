#!/usr/bin/env python3


import re


def make_harmonic_parfile(parfile_path, output_path, harmonic_number):
    with open(parfile_path, 'r') as f:
        lines = f.readlines()

    # We need to scale F0, F1, etc by the harmonic number.
    # Also GLF0_*, GLF1_*, GLF0D_*, etc if they exist.
    newlines = []
    scale_regexes = [r'^F\d+\s', r'^GLF\d+_\d+\s', r'^GLF0D_\d+\s']
    for line in lines:
        for regex in scale_regexes:
            if re.match(regex, line):
                parts = line.split()
                parts[1] = str(float(parts[1]) * harmonic_number)
                line = ' '.join(parts) + '\n'
                break
        newlines.append(line)

    with open(output_path, 'w') as f:
        f.writelines(newlines)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create a harmonic parfile from a pulsar parfile.")
    parser.add_argument("parfile_path", help="Path to the input pulsar parfile")
    parser.add_argument("--output-path", help="Path to save the output harmonic parfile")
    parser.add_argument("--harmonic-number", "-H", type=int, default=2, help="Number of harmonics to include in the template")
    args = parser.parse_args()

    if args.output_path is None:
        # take the input file and prepend with "h{harmonic_number}_"
        # but keep the same directory and extension
        import os
        dir_name, file_name = os.path.split(args.parfile_path)
        name, ext = os.path.splitext(file_name)
        args.output_path = os.path.join(dir_name, f"h{args.harmonic_number}_{name}{ext}")
    print(f"Creating harmonic parfile folded at {args.harmonic_number} harmonic from {args.parfile_path} and saving to {args.output_path}")
    make_harmonic_parfile(args.parfile_path, args.output_path, args.harmonic_number)