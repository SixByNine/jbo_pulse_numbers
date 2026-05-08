#!/bin/bash

#set -euo pipefail


usage() {
    echo "Usage: $0 [-i|--interactive] [-n|--no-upload] [-f|--force] [pulsar_directory_or_name]"
}
pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"

interactive_flag="0"
upload_flag="1"
force_flag="0"
clear_manual_flag="0"
psrdir=""

while [[ $# -gt 0 ]] ; do
    case "$1" in
        -i|--interactive)
            interactive_flag="1"
            shift
            ;;
        -n|--no-upload)
            upload_flag="0"
            shift
            ;;
        -f|--force)
            force_flag="1"
            shift
            ;;
        --clear-manual)
            clear_manual_flag="1"
            shift
            ;;
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

if [[ "$clear_manual_flag" == "1" ]]; then
    echo "Clearing manual follow-up status for $pulsar_name"
    "$scriptdir/manual_followup.py" --clear "$pulsar_name"
fi

manual_status=$(python3 "$scriptdir/manual_followup.py" "$pulsar_name") || exit $?
if [[ "$manual_status" == *"manual_active"* ]] ; then
    if [[ "$force_flag" != "1" ]] ; then
        echo "Manual follow-up is active for $pulsar_name. Aborting. Use --force to continue without upload." >&2
        exit 1
    fi
    if [[ "$upload_flag" != "0" ]] ; then
        echo "Manual follow-up is active for $pulsar_name. Continuing because --force was used; upload disabled."
    fi
    upload_flag="0"
fi

$scriptdir/apply_web_updates.sh "$psrdir"

$scriptdir/change_data_symlink_to_vraid1

web_sync_target="${TIMING_WEB_RSYNC_TARGET:-}"

std=dfb_data/1520.std
if [[ -e dfb_data/dfb1520.std ]] ; then
    std=dfb_data/dfb1520.std
fi

cd dfb_data || exit 1

# Create symlinks to DFB files not already there.

for f in ../data_dir/dfb/J??????_??????.FT ; do
    l=$(basename $f)
    if [[ "$l" < "J2407" ]] ; then
        if [[ ! -e $l ]] ; then
            ln -s $f .
        fi
    fi
done

# get and scrunch the roach data
for f in ../data_dir/roach/2???*.ft ; do
    l=$(basename $f .ft)
    if [[ "$l" > "202306" ]] ; then
        newf=ROACH_${l}.FT
        if [[ ! -e $newf ]] ; then
            pam -FTp -u . -e FTnew $f
            mv ${l}.FTnew $newf
        fi
    fi
done

cd $psrdir || exit 1

last_dfb=$(grep -v "^C" best.tim | grep -e "J......_......" | sed -e "s:FTp:FT:" |  tail -n 1 | awk '{print $1}')
last_roach=$(grep -v "^C" best.tim | grep -e "ROACH_" | sed -e "s:FTp:FT:" |  tail -n 1 | awk '{print $1}')
if [[ -z "$last_roach" ]];  then
    last_roach="none"
    newfiles_ROACH=$(ls dfb_data/ROACH_*.FT)
else
    newfiles_ROACH=$(ls dfb_data/ROACH_*.FT | sed "0,\\:$last_roach:d")
fi
if [[ -z "$last_dfb" ]] ; then
    last_dfb='none'
    newfiles_DFB=$(ls dfb_data/J??????_??????.FT )
else
    newfiles_DFB=$(ls dfb_data/J??????_??????.FT  | sed "0,\\:$last_dfb:d")
fi
echo "Last DFB file: $last_dfb"
echo "Last ROACH file: $last_roach"

trusted_file="best.tim"

# If any new DFB files, we haven't updated since 2023, so the old data should be checked for dlyfix issues.
if [[ -n "$newfiles_DFB" ]] ; then
    
    echo "The DFB data may be out of date. Checking dlyfix"
    cd dfb_data
    rm -f *.dlyfix
    for i in J*.FT ; do
        ~/dlyfix/dlyfix $i -e dlyfix > /dev/null
    done

    cd $psrdir
    files=$(for i in dfb_data/J*.FT ; do d=dfb_data/$(basename $i .FT).dlyfix ; if [[ -e $d ]] ; then echo $d ; else echo $i ; fi ; done )

    pat -A SIS -f tempo2 -s $std $files | sed -e "s:jbdfb:jbdfb -be jbdfb:g" > jdfb_new.tim
    ndly=$(grep dlyfix jdfb_new.tim | wc -l)
    if [[ $ndly -gt 0 ]] ; then
        echo "Warning... there are $ndly toas that had to have delays fixed!"
    fi

    echo "fix pulse numbers..."

    F0=$(grep "^F0 " best.par | awk '{print $2}')

    grep "dfb_data" jdfb_new.tim | while read line ; do
        set -- $line
    #    fname=`basename $1 .dlyfix`
        fname=$(basename $1)
        fname=${fname%.*}
        if grep -q $fname best.tim ; then
            pn=$(grep $fname best.tim | sed -e 's:.*\(-pn [0-9]*\)[^0-9].*:\1:' | grep pn)
            half=$(grep $fname best.tim | sed -e 's:.*\(-halfperiod [A-Z]*\)[^A-Z].*:\1:' | grep halfperiod)
        else
            continue
        fi



        c=$(grep $fname best.tim | awk '{print $1}')
        line2=$(grep $fname best.tim)
        if [[ "$c" == "C" ]] ; then
            line="C $line"
            # strip leading C from line2 for pn calculation
            line2="${line2#C }"
        fi

        toa1=$3
        wflag=""
        delta_pn=$(echo "$pn $F0 $toa1 $line2" | awk '{printf("%0.12f", ($4-$7)*86400.0*$3)}')
        # if the absolute value of delta_pn is larger than 100 we might want to flag this to not be trusted
        if [[ $(echo "$delta_pn" | awk '{print ($1 < -100 || $1 > 100)}') -eq 1 ]]; then
            wflag="-distrust delta_pn_large"
        fi
        pn_new=$(echo "$pn $delta_pn" | awk '
        function nint(x) { return (x >= 0) ? int(x + 0.5) : int(x - 0.5) }
        { printf("-pn %d", nint($2 + $3)) }')
        echo "$line $pn_new $half $wflag"
        
    done > jbdfb_pn.tim

    wc -l best.tim
    grep -v jbdfb best.tim > tmp.tim
    sort -nk3 tmp.tim jbdfb_pn.tim > jbdfb_fix.tim

    wc -l jbdfb_fix.tim
    trusted_file="jbdfb_fix.tim"


fi

cd $psrdir



rm -f update.pdf updated.tim update.stats


echo "New files: $newfiles_DFB $newfiles_ROACH"
if [[ -n "$newfiles_DFB" ]] || [[ -n "$newfiles_ROACH" ]] ; then


    wd=$(pwd)
    echo "Checking dlyfix"
    cd dfb_data
    fixfiles_DFB=""
    for i in $newfiles_DFB ; do
        b=$(basename $i)
        fixed=$(basename $b .FT).dlyfix
        if [[ -e $fixed ]] ; then
            rm $fixed
        fi
        echo "DFB data... $i"
        ~/dlyfix/dlyfix $b -e dlyfix > /dev/null
        if [[ -e $fixed ]] ; then
            echo "Not delay fixed $i"
            fixfiles_DFB="$fixfiles_DFB dfb_data/$fixed"
        else
            fixfiles_DFB="$fixfiles_DFB $i"
        fi
    done
    cd $wd
    newfiles="$fixfiles_DFB $newfiles_ROACH"

    mkdir -p updates

    rm updates/*

    pat -A SIS -f tempo2 -s $std $newfiles | sed -e "s:jbdfb:jbdfb -be jbdfb:g" | sed -e "s:roach:roach -be jbroach:g" | grep -v nan > updates/jdfb_update.tim
    cat $trusted_file updates/jdfb_update.tim > updates/extended.tim

    grep -v -F -- "-distrust" $trusted_file > updates/trusted.tim

    cd updates
    

    if [[ -e ../enterprise_log3_sub3/J1941+2525_run.par.post ]] ; then
        cp ../enterprise_log3_sub3/J1941+2525_run.par.post update.par
        tempo2 -f update.par trusted.tim -newpar
        mv new.par update.par
    elif [[ -e ../analysis/final.par ]] ; then
        cp ../analysis/final.par update.par
    else
        echo "No par file to use for pulse number extrapolation. Exiting."
        exit 1
    fi

    if grep -Fq "jbroach" extended.tim; then
        # Check if there are efac/equad and jump parameters for the roach in the par file
        # If not, and we have roach data in the tim file, copy the efac/equad from the dfb
        if ! grep -q "TNEF -be jbroach" update.par; then
            grep "TNEF -be jbdfb" update.par | sed -e "s/TNEF -be jbdfb/TNEF -be jbroach/g" >> update.par
        fi
        if ! grep -q "TNEQ -be jbroach" update.par ; then
            grep "TNEQ -be jbdfb" update.par | sed -e "s/TNEQ -be jbdfb/TNEQ -be jbroach/g" >> update.par
        fi
        if ! grep -q "JUMP -be jbroach" update.par ; then
            grep "JUMP -be jbdfb" update.par | sed -e "s/JUMP -be jbdfb/JUMP -be jbroach/g" >> update.par
        fi
    fi
    ephindex_opt=()
    if [[ -e ../timing_dir/ephindex.dat ]] ; then
        cp ../timing_dir/ephindex.dat .
        ephindex_opt=(--ephindex ephindex.dat)
    fi
    tempo2 -output add_pulseNumber -f update.par extended.tim

    run_id="${pulsar_name}_$(date -u +'%Y%m%d_%H%M%S')"
    stage_root="$psrdir/updates/review_runs"

    # Clean up old runs for this pulsar; they won't be needed anymore and we don't want to accidentally sync them to the web.
    # Validate paths before deletion
    if [[ -z "$stage_root" ]] || [[ -z "$pulsar_name" ]]; then
        echo "Error: stage_root or pulsar_name not set" >&2
        exit 1
    fi

    # Ensure path exists and is what we expect
    target_dir="$stage_root/$pulsar_name"
    if [[ ! -d "$target_dir" ]]; then
        mkdir -p "$target_dir"
    else
        # Use find with -type d to only delete directories (not symlinks)
        find "$target_dir" -maxdepth 1 -mindepth 1 -type d -name "${pulsar_name}*" -exec rm -rf {} \; || {
            echo "Warning: cleanup of old runs failed" >&2
        }
    fi


    stage_dir="$stage_root/$pulsar_name/$run_id"
    mkdir -p "$stage_dir"
    manifest_path="$stage_dir/manifest.json"
    marker_path="$stage_dir/COMPLETE"
    extrapolate_cmd=(
        python ${scriptdir}/extrapolate_pulse_numbers.py
        --par update.par
        --tim trusted.tim
        --newtim withpn.tim
        --wrap-max 2
        --wrap-min -2
        --outlier-prob 0.02
        --particle-limit 128
        --covariance-scale 32
        --mean-poly-order 2
        --run-id "$run_id"
        --pulsar "$pulsar_name"
        --output-dir "$stage_dir"
        --manifest-output "$manifest_path"
        --complete-marker "$marker_path"
        "${ephindex_opt[@]}"
    )
    if [[ "$interactive_flag" == "1" ]]; then
        extrapolate_cmd+=(--interactive)
    fi
    echo "${extrapolate_cmd[@]}"
    "${extrapolate_cmd[@]}"

    # Only sync to web if the extrapolation completed successfully and we're not in no-upload mode
    if [[ -e "$marker_path" ]] && [[ -n "$web_sync_target" ]] && [[ "$upload_flag" == "1" ]]; then
        # Sync stage_root to target, filtering to only this run; rsync creates directories automatically.
        rsync -avP \
          --include="/$pulsar_name/" \
          --include="/$pulsar_name/$run_id/" \
          --include="/$pulsar_name/$run_id/**" \
          --exclude="*" \
          "$stage_root/" "$web_sync_target/"
        echo "rsynced_run $web_sync_target/$pulsar_name/$run_id/"
        
                "$scriptdir/trigger_import.sh" "$pulsar_name" "$run_id"
    else
        echo "Run not uploaded. marker_exists=$([[ -e "$marker_path" ]] && echo "yes" || echo "no") web_sync_target_set=$([[ -n "$web_sync_target" ]] && echo "yes" || echo "no") upload_flag=$upload_flag"
    fi
else
    echo "No new files"
fi
