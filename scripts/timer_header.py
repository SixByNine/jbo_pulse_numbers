from __future__ import annotations

from dataclasses import dataclass
import struct
from pathlib import Path
from typing import List

TIMER_SIZE = 1024
TIMER_SPACE = 184
BAND_SIZE = 96
MINI_SIZE = 128


@dataclass(frozen=True)
class Band:
    lo1: float
    lo2: float
    loUP: float
    loDOWN: float
    centrefreq: float
    bw: float
    flux_A: float
    inv_mode: int
    auto_atten: int
    correlator_mode: int
    f_atten_A: float
    f_atten_B: float
    polar: int
    feed_offset: float
    nlag: int
    flux_B: float
    flux_err: float
    npol: int


@dataclass(frozen=True)
class Mini:
    junk: int
    junk2: int
    junk3: int
    mjd: int
    fracmjd: float
    lst_start: float
    pfold: float
    tel_zen: float
    tel_az: float
    feed_ang: float
    para_angle: float
    version: float
    pulse_phase: float
    flux_A: float
    flux_B: float
    integration: float
    space: bytes


@dataclass(frozen=True)
class TimerHeader:
    ram_boards: str
    corr_boards: str
    machine_id: str
    version: float
    minorversion: float
    tape_number: int
    file_number: int

    utdate: str
    fracmjd: float
    mjd: int
    number_of_ticks: int
    offset: float
    lst_start: float

    coord_type: str
    psrname: str
    ra: float
    dec: float
    l: float
    b: float
    nominal_period: float
    dm: float
    fold_true_ratio: int
    nperiods_long: int
    nperiods_short: int

    nbin: int
    tsmp: float
    sub_int_time: float
    ndump_sub_int: int
    narchive_int: int
    junk: int
    nsub_int: int
    junk2: int
    dump_time: float
    nfreq: int
    nsub_band: int
    feedmode: int
    tree: str
    telid: str

    tpover: str
    nspan: int
    ncoeff: int
    nbytespoly: int
    nbytesephem: int

    banda: Band
    bandb: Band

    rotm: float
    rmi: float
    pnterr: float
    ibeam: int
    tape_label: str

    schedule: str
    comment: str
    pos_angle: float
    headerlength: int

    corrected: int
    calibrated: int
    obstype: int
    calfile: str
    scalfile: str
    wts_and_bpass: int
    wtscheme: int
    software: str
    backend: str
    be_data_size: int
    rcvr_id: str
    space: bytes

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if not (0.0 <= self.fracmjd < 1.0):
            warnings.append(f"fracmjd out of range [0,1): {self.fracmjd}")
        if self.nbin <= 0:
            warnings.append(f"nbin is non-positive: {self.nbin}")
        if self.nfreq <= 0:
            warnings.append(f"nfreq is non-positive: {self.nfreq}")
        if self.nominal_period <= 0:
            warnings.append(f"nominal_period is non-positive: {self.nominal_period}")
        return warnings


def _decode_c_string(value: bytes) -> str:
    return value.decode("ascii", errors="replace").rstrip("\x00")


def _parse_band(data: bytes, offset: int) -> tuple[Band, int]:
    fmt = ">6d f i i i f f i f i f f i"
    size = struct.calcsize(fmt)
    if offset + size > len(data):
        raise ValueError("Insufficient data while parsing Band")
    values = struct.unpack_from(fmt, data, offset)
    band = Band(*values)
    return band, offset + size


def parse_timer_header_bytes(data: bytes) -> TimerHeader:
    if len(data) < TIMER_SIZE:
        raise ValueError(f"Need at least {TIMER_SIZE} bytes, got {len(data)}")

    # Read only the header bytes.
    data = data[:TIMER_SIZE]
    offset = 0

    def unpack_one(fmt: str):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > TIMER_SIZE:
            raise ValueError(f"Insufficient data while unpacking format {fmt!r}")
        values = struct.unpack_from(fmt, data, offset)
        offset += size
        return values

    (ram_boards, corr_boards, machine_id, version, minorversion, tape_number, file_number) = unpack_one(
        ">32s32s8sffii"
    )

    (utdate, fracmjd, mjd, number_of_ticks, obs_offset, lst_start) = unpack_one(
        ">16sdiidd"
    )

    (
        coord_type,
        psrname,
        ra,
        dec,
        l,
        b,
        nominal_period,
        dm,
        fold_true_ratio,
        nperiods_long,
        nperiods_short,
    ) = unpack_one(">8s16sddffdfiii")

    (
        nbin,
        tsmp,
        sub_int_time,
        ndump_sub_int,
        narchive_int,
        junk,
        nsub_int,
        junk2,
        dump_time,
        nfreq,
        nsub_band,
        feedmode,
        tree,
        telid,
    ) = unpack_one(">iffiiiiifiii8s16s")

    (tpover, nspan, ncoeff, nbytespoly, nbytesephem) = unpack_one(">8siiii")

    banda, offset = _parse_band(data, offset)
    bandb, offset = _parse_band(data, offset)

    (rotm, rmi, pnterr, ibeam, tape_label) = unpack_one(">fffi8s")

    (schedule, comment, pos_angle, headerlength) = unpack_one(">32s64sfi")

    (
        corrected,
        calibrated,
        obstype,
        calfile,
        scalfile,
        wts_and_bpass,
        wtscheme,
        software,
        backend,
        be_data_size,
        rcvr_id,
        space,
    ) = unpack_one(">iii24s24sii128s8sI8s184s")

    if offset != TIMER_SIZE:
        raise ValueError(f"Internal parse error: consumed {offset} bytes, expected {TIMER_SIZE}")

    return TimerHeader(
        ram_boards=_decode_c_string(ram_boards),
        corr_boards=_decode_c_string(corr_boards),
        machine_id=_decode_c_string(machine_id),
        version=version,
        minorversion=minorversion,
        tape_number=tape_number,
        file_number=file_number,
        utdate=_decode_c_string(utdate),
        fracmjd=fracmjd,
        mjd=mjd,
        number_of_ticks=number_of_ticks,
        offset=obs_offset,
        lst_start=lst_start,
        coord_type=_decode_c_string(coord_type),
        psrname=_decode_c_string(psrname),
        ra=ra,
        dec=dec,
        l=l,
        b=b,
        nominal_period=nominal_period,
        dm=dm,
        fold_true_ratio=fold_true_ratio,
        nperiods_long=nperiods_long,
        nperiods_short=nperiods_short,
        nbin=nbin,
        tsmp=tsmp,
        sub_int_time=sub_int_time,
        ndump_sub_int=ndump_sub_int,
        narchive_int=narchive_int,
        junk=junk,
        nsub_int=nsub_int,
        junk2=junk2,
        dump_time=dump_time,
        nfreq=nfreq,
        nsub_band=nsub_band,
        feedmode=feedmode,
        tree=_decode_c_string(tree),
        telid=_decode_c_string(telid),
        tpover=_decode_c_string(tpover),
        nspan=nspan,
        ncoeff=ncoeff,
        nbytespoly=nbytespoly,
        nbytesephem=nbytesephem,
        banda=banda,
        bandb=bandb,
        rotm=rotm,
        rmi=rmi,
        pnterr=pnterr,
        ibeam=ibeam,
        tape_label=_decode_c_string(tape_label),
        schedule=_decode_c_string(schedule),
        comment=_decode_c_string(comment),
        pos_angle=pos_angle,
        headerlength=headerlength,
        corrected=corrected,
        calibrated=calibrated,
        obstype=obstype,
        calfile=_decode_c_string(calfile),
        scalfile=_decode_c_string(scalfile),
        wts_and_bpass=wts_and_bpass,
        wtscheme=wtscheme,
        software=_decode_c_string(software),
        backend=_decode_c_string(backend),
        be_data_size=be_data_size,
        rcvr_id=_decode_c_string(rcvr_id),
        space=space,
    )


def read_timer_header(path: str | Path) -> TimerHeader:
    path = Path(path)
    with path.open("rb") as fptr:
        data = fptr.read(TIMER_SIZE)
    if len(data) != TIMER_SIZE:
        raise ValueError(f"File {path} is too short: expected {TIMER_SIZE} header bytes, got {len(data)}")
    return parse_timer_header_bytes(data)


def parse_mini_header_bytes(data: bytes) -> Mini:
    if len(data) < MINI_SIZE:
        raise ValueError(f"Need at least {MINI_SIZE} bytes, got {len(data)}")

    data = data[:MINI_SIZE]
    fmt = ">3i i 3d 4f 4f d 48s"
    values = struct.unpack(fmt, data)
    mini = Mini(*values)
    return mini


def read_mini_header(
    path: str | Path, offset: int | None = None, header: TimerHeader | None = None
) -> Mini:
    if header is not None:
        offset = TIMER_SIZE + header.nbytespoly + header.nbytesephem+header.be_data_size
    elif offset is None:
        offset = TIMER_SIZE

    path = Path(path)
    with path.open("rb") as fptr:
        fptr.seek(offset)
        data = fptr.read(MINI_SIZE)
    if len(data) != MINI_SIZE:
        raise ValueError(
            f"File {path} is too short: expected {MINI_SIZE} mini bytes at offset {offset}, got {len(data)}"
        )
    return parse_mini_header_bytes(data)

def read_polyco(path: str | Path, header: TimerHeader) -> bytes:
    path = Path(path)
    with path.open("rb") as fptr:
        fptr.seek(TIMER_SIZE+header.be_data_size)
        data = fptr.read(header.nbytespoly)
    if len(data) != header.nbytespoly:
        raise ValueError(
            f"File {path} is too short: expected {header.nbytespoly} polyco bytes at offset {TIMER_SIZE+header.be_data_size}, got {len(data)}"
        )
    return data.decode("ascii", errors="replace")

def read_ephem(path: str | Path, header: TimerHeader) -> bytes:
    path = Path(path)
    with path.open("rb") as fptr:
        fptr.seek(TIMER_SIZE+header.be_data_size+header.nbytespoly)
        data = fptr.read(header.nbytesephem)
    if len(data) != header.nbytesephem:
        raise ValueError(
            f"File {path} is too short: expected {header.nbytesephem} ephem bytes at offset {TIMER_SIZE+header.be_data_size+header.nbytespoly}, got {len(data)}"
        )
    return data.decode("ascii", errors="replace")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Read TIMER and optional MINI headers")
    parser.add_argument("file", help="Path to TIMER-format file")
    parser.add_argument("--mini", action="store_true", help="Also read and display MINI header")
    parser.add_argument("--polyco", action="store_true", help="Also read and display polyco data")
    parser.add_argument("--ephem", action="store_true", help="Also read and display ephemeris data")
    args = parser.parse_args()

    header = read_timer_header(args.file)
    print(f"TIMER header:")
    print(f"  psrname={header.psrname}")
    print(f"  mjd={header.mjd}")
    print(f"  nominal_period={header.nominal_period:.15g}")
    print(f" header_length={header.headerlength}")
    if args.ephem:
        print(read_ephem(args.file, header))
    if args.polyco:
        print(read_polyco(args.file, header))
    if args.mini:
        mini = read_mini_header(args.file, header=header)
        print(f"MINI header (at offset {1024 + header.nbytespoly + header.nbytesephem+header.be_data_size}):")
        print(f"  mjd={mini.mjd}")
        print(f"  fracmjd={mini.fracmjd}")
        print(f"  pfold={mini.pfold:.15g}")
        print(f"  integration={mini.integration}")
