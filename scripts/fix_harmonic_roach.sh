#!/bin/bash


usage() {
    echo "Usage: $0 [pulsar_directory_or_name]"
}


pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"


while [[ $# -gt 0 ]] ; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ -n "$psrdir" ]] ; then
                echo "Only one pulsar directory or name may be provided." >&2
                usage >&2
                exit 2
            fi
                psr="$1"
                psrdir=$pulsar_base_dir/$psr
                if [[ ! -d "$psrdir" ]]; then
                    echo "Pulsar directory not found: $psrdir" >&2
                    exit 2
                fi
            shift
            ;;
    esac
done

if [[ $# -gt 0 ]] ; then
    echo "Only one pulsar directory or name may be provided." >&2
    usage >&2
    exit 2
fi

if [[ -z "$psrdir" ]] ; then
    psrdir=$(pwd)
fi

scriptdir=$(dirname "$(readlink -f "$0")")

cd $psrdir

psrdir=$(pwd)
pulsar_name=$(basename "$psrdir")




F0=$(grep "^F0 " best.par | awk '{print $2}')
orig_std=dfb_data/1520.std
if [[ -e dfb_data/dfb1520.std ]] ; then
    orig_std=dfb_data/dfb1520.std
fi



for infile in dfb_data/ROACH_2*.FT; do

    if [[ $infile == dfb_data/ROACH_*_harmonic*.FT ]]; then
        echo "File $infile appears to already be a harmonic file, skipping." >&2
        continue
    fi

   
    # basename strip ROACH_ and .FT
    base=$(basename "$infile" .FT)
    base=${base#ROACH_}
    

    

    # look for a .zzz file
    origfile="data_dir/roach/${base}.zzz"
    if [[ ! -e "$origfile" ]]; then
        origfile="data_dir/roach/${base}.med"
    fi
    if [[ ! -e "$origfile" ]]; then
        echo "Original file not found for $infile, skipping." >&2
        continue
    fi

    # determine harmonic number
    fold_period=$(vap -n -c period "$origfile" 2>/dev/null | awk '{print $2}')
    if [[ -z "$fold_period" ]]; then
        echo "Could not determine fold period for $origfile, skipping." >&2
        continue
    fi
    harmonic_number=$(awk -v f0="$F0" -v fold_period="$fold_period" 'BEGIN { printf("%d", 1/(fold_period*f0) + 0.5) }')
    if [[ "$harmonic_number" -le 1 ]]; then
        echo "Harmonic number for $origfile is not greater than 1, skipping." >&2
        continue
    fi
    
    # determine new filename
    newfile="dfb_data/ROACH_${base}_harmonic_${harmonic_number}.FT"
    if [[ -e "$newfile" ]]; then
        echo "New file already exists: $newfile, skipping." >&2
        continue
    fi

    hparfile="harmonic_h${harmonic_number}.par"
    if [[ ! -e "$hparfile" ]]; then
        echo "Par file not found: $hparfile" >&2
        $scriptdir/make_harmonic_parfile.py best.par --harmonic-number "$harmonic_number" --output "$hparfile"
    fi

    harmonic_template="dfb_data/harm${harmonic_number}_1520.std"
    if [[ ! -e "$harmonic_template" ]]; then
        echo "Harmonic template not found: $harmonic_template, generating..." >&2
        $scriptdir/make_harmonic_template.py $orig_std --harmonic-number $harmonic_number --output "$harmonic_template"
        if [[ ! -e "$harmonic_template" ]]; then
            echo "Failed to generate harmonic template: $harmonic_template, skipping." >&2
            exit 1
        fi
    fi

    echo "Processing $infile (harmonic number $harmonic_number) -> $newfile"
    pam -FTp -E "$hparfile" -e newFT -u dfb_data $origfile
    mv dfb_data/${base}.newFT "$newfile"
    ls -l "$newfile"
    rm -f "$infile"
done