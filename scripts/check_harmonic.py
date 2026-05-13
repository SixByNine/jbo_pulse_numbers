#!/usr/bin/env python3
import argparse
import os
import sys
from dataclasses import dataclass
from enum import Enum

import astropy.io.fits as fits

# Add scripts directory to path to allow importing sibling modules
sys.path.insert(0, os.path.dirname(__file__))
from timer_header import read_mini_header, read_timer_header


FITS_MAGIC = b"SIMPLE"


class CheckStatus(str, Enum):
    OK = "ok"
    BAD = "bad"
    UNPARSEABLE = "unparseable"


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    message: str


def ok_result():
    return CheckResult(CheckStatus.OK,"")


def bad_result(message):
    return CheckResult(CheckStatus.BAD, message)


def unparseable_result(message):
    return CheckResult(CheckStatus.UNPARSEABLE, message)


def looks_like_fits(file):
    """Return True when the file starts with the FITS magic string."""
    try:
        with open(file, "rb") as fileobj:
            return fileobj.read(len(FITS_MAGIC)) == FITS_MAGIC
    except OSError:
        return False


def check_fits_file(file):
    """Check FITS file for fold period jumps."""
    try:
        with fits.open(file) as hdul:
            hist = hdul["HISTORY"]
            prev_fold_period = None

            for i, row in enumerate(hist.data):
                tbin = row["TBIN"]
                nbin = row["NBIN"]
                fold_period = tbin * nbin

                if prev_fold_period is not None:
                    jump = (
                        abs(fold_period - prev_fold_period) / abs(min(fold_period, prev_fold_period))
                        if min(fold_period, prev_fold_period) != 0
                        else float("inf")
                    )
                    if jump > 0.10:
                        return bad_result(
                            f"{file}: "
                            f"fold_period jump >10% at row {i}: "
                            f"prev={prev_fold_period:.12g}, current={fold_period:.12g}, jump={jump:.2%}"
                        )

                prev_fold_period = fold_period

        return ok_result()
    except (OSError, KeyError, IndexError, TypeError) as exc:
        return unparseable_result(f"Error reading FITS file {file}: {exc}")


def check_timer_file(file):
    """Check timer file by comparing the two periods stored in the file."""
    try:
        header = read_timer_header(file)
        mini = read_mini_header(file, header=header)

        orig_fold_period = header.nominal_period
        current_fold_period = mini.pfold
        if orig_fold_period is None or current_fold_period is None:
            return bad_result(f"{file}: missing fold period in timer data")

        jump = (
            abs(current_fold_period - orig_fold_period) / abs(min(current_fold_period, orig_fold_period))
            if min(current_fold_period, orig_fold_period) != 0
            else float("inf")
        )
        if jump > 0.10:
            return bad_result(
                f"{file}: fold_period jump >10%: "
                f"original={orig_fold_period:.12g}, current={current_fold_period:.12g}, jump={jump:.2%}"
            )

        return ok_result()
    except Exception as exc:
        return unparseable_result(f"Error reading timer file {file}: {exc}")


def check_file(file):
    """Choose the most likely parser for a file, with a safe fallback."""
    basename = os.path.basename(file)
    preferred = "timer" if basename.startswith("ROACH") else ("fits" if looks_like_fits(file) else "timer")
    fallback = "fits" if preferred == "timer" else "timer"
    first_unparseable = None

    for file_type in (preferred, fallback):
        if file_type == "fits":
            result = check_fits_file(file)
        else:
            result = check_timer_file(file)

        if result.status == CheckStatus.OK:
            return result
        if result.status == CheckStatus.BAD:
            return result
        if first_unparseable is None:
            first_unparseable = result

    return first_unparseable or unparseable_result(f"{file}: could not determine file type")


def main():
    parser = argparse.ArgumentParser(
        description="Check harmonic/fold period consistency in FITS or timer files"
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Files to check"
    )

    args = parser.parse_args()

    exit_code = 0
    for file in args.files:
        result = check_file(file)
        if result.status != CheckStatus.OK and result.message:
            print(result.message)
        if result.status != CheckStatus.OK:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
    