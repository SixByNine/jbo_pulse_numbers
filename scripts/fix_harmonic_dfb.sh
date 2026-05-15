#!/bin/bash
usage() {
    echo "Usage: $0 [-d|--harmonic-directory DIR] [pulsar_directory_or_name]"
}
pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"


while [[ $# -gt 0 ]] ; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        -d|--harmonic-directory)
            shift
            if [[ -n "$harmonic_dir" ]] ; then
                echo "Only one harmonic directory may be provided." >&2
                usage >&2
                exit 2
            fi
            harmonic_dir="$1"
            shift
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



if [[ -z "$harmonic_dir" ]]; then
    harmonic_dir="data_dir/dfb/halfperiod"
fi
if [[ ! -d "$harmonic_dir" ]]; then
    echo "Harmonic directory not found: $harmonic_dir" >&2
    exit 2
fi

F0=$(grep "^F0 " best.par | awk '{print $2}')
orig_std=dfb_data/1520.std
if [[ -e dfb_data/dfb1520.std ]] ; then
    orig_std=dfb_data/dfb1520.std
fi

if [[ -e harmonic.tim ]] ; then
    rm -f harmonic.tim
fi
if [[ -e harmonic.tim.tmp ]] ; then
    rm -f harmonic.tim.tmp
fi
cp best.tim harmonic.tim.in

# Loop over any .FT or .FTp files in the harmonic directory.
# There may be symlinks from .FT to .FTp (or the other way around), so we want to make sure that we find the "real" files and not just the symlinks.
for hfile in $(find "$harmonic_dir" -type f \( -name "*.FT" -o -name "*.FTp" \) -print | sort) ; do
    stem=$(basename "$hfile")
    if [[ "$stem" == *.FTp ]]; then
        stem="${stem%.FTp}"
    else
        stem="${stem%.FT}"
    fi
    # determine harmonic number
    fold_period=$(vap -n -c period "$hfile" 2>/dev/null | awk '{print $2}')
    if [[ -z "$fold_period" ]]; then
        echo "Could not determine fold period for $hfile, skipping." >&2
        continue
    fi
    harmonic_number=$(awk -v f0="$F0" -v fold_period="$fold_period" 'BEGIN { printf("%d", 1/(fold_period*f0) + 0.5) }')
    if [[ "$harmonic_number" -le 1 ]]; then
        echo "Harmonic number for $hfile is not greater than 1, skipping." >&2
        continue
    fi
    newfile="${stem}_harmonic_${harmonic_number}.FT"
    echo "Processing $hfile: fold_period=$fold_period harmonic_number=$harmonic_number newfile=$newfile"

    harmonic_template="dfb_data/harm${harmonic_number}_1520.std"
    if [[ ! -e "$harmonic_template" ]]; then
        echo "Harmonic template not found: $harmonic_template, generating..." >&2
        $scriptdir/make_harmonic_template.py $orig_std --harmonic-number $harmonic_number --output "$harmonic_template"
        if [[ ! -e "$harmonic_template" ]]; then
            echo "Failed to generate harmonic template: $harmonic_template, skipping." >&2
            exit 1
        fi
    fi
    
    # remake symlinks in the dfb_data
    if [[ -f "dfb_data/${newfile}" ]]; then
        echo "File already exists: dfb_data/${newfile}." >&2
    fi    
    
    ln -f -s ../$hfile dfb_data/${newfile}

    # Mark any existing files as bad and remove them.
    if [[ -e "dfb_data/${stem}.FT" ]]; then
        echo "${stem}.FT  # replaced by harmonic" >> dfb_data/bad.list
        rm -f "dfb_data/${stem}.FT" "dfb_data/${stem}.dlyfix"
    fi

    pn=""
    # Comment out the old FT file from harmonic.tim.in and extract any pulse number that exists
    if grep -q -E "dfb_data/$stem" harmonic.tim.in ; then
        line2=$(grep dfb_data/$stem harmonic.tim.in | sed -e "s:^[\\s]*C dfb_data/$stem:dfb_data/$stem:")
        pn=$(grep dfb_data/$stem harmonic.tim.in | sed -e 's:.*\(-pn .[0-9]*\).*:\1:' | grep pn)
        
        echo "Removing existing obs from harmonic.tim.in: $stem"
        sed -i -e "s:^ *dfb_data/$stem:C dfb_data/$stem:" harmonic.tim.in
    else
        echo "No existing entry for $stem in harmonic.tim.in"
    fi

    if [[ -e "dfb_data/${newfile%.FT}.dlyfix" ]]; then
        rm -f "dfb_data/${newfile%.FT}.dlyfix"
    fi
    ~/dlyfix/dlyfix dfb_data/${newfile} -e dlyfix > /dev/null
    if [[ -e "dfb_data/${newfile%.FT}.dlyfix" ]]; then
        echo "Delays had to be fixed for $newfile, using dlyfix output."
        newfile="${newfile%.FT}.dlyfix"
    fi
   
    toa=$(pat -FT -A SIS -f tempo2 -s $harmonic_template dfb_data/${newfile} | sed -e "s:jbdfb:jbdfb -be jbdfb:g" | tail -n 1)

    toa1=$(echo "$toa" | awk '{print $3}')

    if [[ -z "$pn" ]]; then
        # Guess the pulse number. Find the closest ToA in the .tim file
        echo "No existing pulse number for $stem, trying to guess from closest ToA in harmonic.tim.in"
        closest_toa=$(awk -v target="$toa1" 'BEGIN { min_diff = 1e9; closest = "" } { diff = ($3 - target) * 86400.0; if (diff < 0) diff = -diff; if (diff < min_diff) { min_diff = diff; closest = $0 } } END { print closest }' harmonic.tim.in)
        if [[ -n "$closest_toa" ]]; then
            line2=$(echo "$closest_toa" | sed -e "s:^[\\s]*C ::")
            pn=$(echo "$closest_toa" | sed -e 's:.*\(-pn .[0-9]*\).*:\1:' | grep pn)
        fi
    fi
    if [[ -n "$pn" ]]; then
        delta_pn=$(echo "$pn $F0 $toa1 $line2" | awk '{printf("%0.12f", ($4-$7)*86400.0*$3)}')
        # if the absolute value of delta_pn is larger than 100 we might want to flag this to not be trusted
        if [[ $(echo "$delta_pn" | awk '{print ($1 < -100 || $1 > 100)}') -eq 1 ]]; then
            wflag="-distrust delta_pn_large"
            echo "Warning: large pulse number change for $fname: $delta_pn. Marking as untrusted." >&2
            echo "$pn $F0 $toa1 $line2" >&2
        fi
        pn_new=$(echo "$pn $delta_pn" | awk '
        function nint(x) { return (x >= 0) ? int(x + 0.5) : int(x - 0.5) }
        { printf("-pn %d", nint($2 + $3)) }')
    fi
    echo " $toa $pn_new $wflag -harmonic ${harmonic_number}">> harmonic.tim.tmp
    
done

cat harmonic.tim.in harmonic.tim.tmp | sort -nk3 > harmonic.tim