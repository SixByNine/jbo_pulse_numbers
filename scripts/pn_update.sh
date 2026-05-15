#!/bin/bash

#set -euo pipefail


usage() {
    echo "Usage: $0 [-3|--cubic] [-2|--no-cubic] [--no-pm] [-i|--interactive] [-n|--no-auto] [--no-upload] [-f|--force] [--clear-manual] [--clear-postponed] [pulsar_directory_or_name]"
}
pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"

interactive_flag="0"
upload_flag="1"
force_flag="0"
auto_flag="1"
clear_manual_flag="0"
clear_postponed_flag="0"
psrdir=""
mean_poly_order=3
fit_pm_flag="--fit-pm"

while [[ $# -gt 0 ]] ; do
    case "$1" in
        -3|--cubic)
            mean_poly_order=3
            shift
            ;;
        -2|--no-cubic)
            mean_poly_order=2
            shift
            ;;
        --no-pm)
            fit_pm_flag=""
            shift
            ;;
        -i|--interactive)
            interactive_flag="1"
            shift
            ;;
        -n|--no-auto)
            upload_flag="0"
            auto_flag="0"
            shift
            ;;
        --no-upload)
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
        --clear-postponed)
            clear_postponed_flag="1"
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

report_processing_error() {
    local error_message="$1"

    if [[ "$upload_flag" == "1" ]]; then
        local error_run_id
        error_run_id="${pulsar_name}_error_$(date -u +'%Y%m%d_%H%M%S')"
        if ! python3 "$scriptdir/log_error_run.py" "$pulsar_name" "$error_run_id" "$error_message"; then
            echo "Warning: failed to report processing error via API for $pulsar_name" >&2
        fi
    else
        echo "Upload disabled; skipping error API reporting (dry run mode)." >&2
    fi
}

if [[ "$clear_manual_flag" == "1" ]]; then
    echo "Clearing manual follow-up status for $pulsar_name"
    "$scriptdir/manual_followup.py" --clear "$pulsar_name"
fi

if [[ "$clear_postponed_flag" == "1" ]]; then
    echo "Clearing postponed status for $pulsar_name"
    "$scriptdir/postponed_followup.py" --clear "$pulsar_name"
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

postponed_status=$(python3 "$scriptdir/postponed_followup.py" "$pulsar_name") || exit $?
if [[ "$postponed_status" == *"postponed_active"* ]] ; then
    if [[ "$force_flag" != "1" ]] ; then
        echo "Postponed state is active for $pulsar_name. Aborting. Use --force to continue without upload." >&2
        exit 1
    fi
    if [[ "$upload_flag" != "0" ]] ; then
        echo "Postponed state is active for $pulsar_name. Continuing because --force was used; upload disabled."
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
    l=$(basename $f .FT)
    if compgen -G "${l}_harmonic_*" > /dev/null ; then
        echo "Skipping $l because a harmonic version exists"
        continue
    fi
    if [[ -e bad.list ]] && grep -q "^${l}" bad.list ; then
        reason=$(grep "^${l}" bad.list)
        echo "Skipping bad file ${reason}"
        continue
    fi
    if [[ "$l" < "J2407" ]] ; then
        if [[ ! -e ${l}.FT ]] ; then
            ln -s $f .
        fi
    fi
done

# get and scrunch the roach data
for f in ../data_dir/roach/2???*.ft ; do
    l=$(basename $f .ft)
    if [[ -e bad.list ]] && grep -q "^${l}" bad.list ; then
        reason=$(grep "^${l}" bad.list)
        echo "Skipping bad file ${reason}"
        continue
    fi
    if [[ "$l" > "202306" ]] ; then
        newf=ROACH_${l}.FT
        # check if a file exists like ${newf} or ${l}_h*.FT
        if [[ ! -e $newf ]] && ! compgen -G "ROACH_${l}_harmonic_*.FT" > /dev/null ; then
            if [[ -e ${f%.ft}.FTp_cf ]] ; then
                ln -s ${f%.ft}.FTp_cf $newf
            else
                pam -FTp -u . -e FTnew $f
                mv ${l}.FTnew $newf
            fi
        fi
    fi
done

cd $psrdir || exit 1

echo "Checking for broken symlinks in dfb_data"
broken_symlinks=$(find dfb_data -type l ! -exec test -e {} \; -print)
if [[ -n "$broken_symlinks" ]]; then
    echo "Found broken symlinks in dfb_data:" >&2
    printf '%s\n' "$broken_symlinks" >&2
    report_processing_error "Broken symlink(s) detected in dfb_data; manual intervention required."
    exit 1
fi


echo "Checking for harmonic issues"
# Check for issues where a wrong ephemeris has been installed using the check_harmonic script
"$scriptdir/check_harmonic.py" dfb_data/*.FT
check_ok=$?
if [[ $check_ok -ne 0 ]]; then
    echo "Harmonic check failed. Please investigate the output above and fix any issues before proceeding." >&2

    report_processing_error "Harmonic check failed before extrapolation; manual intervention required."

    exit 1
fi


# Check for an issue in best.tim where commented ToAs have accidently had a space prepended.
sed -i -e "s:^ C :C :" best.tim

last_dfb=$(grep -v "^C" best.tim | grep -e "J......_......" | sed -e "s:FTp:FT:" |  tail -n 1 | awk '{print $1}')
last_roach=$(grep -v "^C" best.tim | grep -e "ROACH_" | grep -v "harmonic" | sed -e "s:FTp:FT:" |  tail -n 1 | awk '{print $1}')
last_roach_harmonic=$(grep -v "^C" best.tim | grep -e "ROACH_.*harmonic" | sed -e "s:FTp:FT:" |  tail -n 1 | awk '{print $1}')
if [[ -z "$last_roach" ]];  then
    last_roach="none"
    newfiles_ROACH=$(ls dfb_data/ROACH_*.FT | grep -v "harmonic")
else
    newfiles_ROACH=$(ls dfb_data/ROACH_*.FT | grep -v "harmonic" | sed "0,\\:$last_roach:d")
fi
if [[ -z "$last_roach_harmonic" ]];  then
    last_roach_harmonic=""
    newfiles_ROACH_harmonic=$(ls dfb_data/ROACH_*harmonic*.FT)
else
    newfiles_ROACH_harmonic=$(ls dfb_data/ROACH_*harmonic*.FT | sed "0,\\:$last_roach_harmonic:d")
fi

if [[ -z "$last_dfb" ]] ; then
    last_dfb='none'
    newfiles_DFB=$(ls dfb_data/J??????_??????.FT )
else
    newfiles_DFB=$(ls dfb_data/J??????_??????.FT  | sed "0,\\:$last_dfb:d")
fi
echo "Last DFB file: $last_dfb"
echo "Last ROACH file: $last_roach"
if [[ -n "$last_roach_harmonic" ]]; then
    echo "Last ROACH harmonic file: $last_roach_harmonic"
fi

trusted_file="best.tim"

# If any new DFB files, we haven't updated since 2023, so the old data should be checked for dlyfix issues.
last_dfb_basename=$(basename "$last_dfb")
if [[ "$last_dfb_basename" < "J24" ]] ; then
    
    echo "The DFB data may be out of date. Checking dlyfix"
    cd dfb_data
    # deliberately avoid harmonic files, i.e. J*_harmonic*.FT
    rm -f J??????_??????.dlyfix
    for i in J??????_??????.FT ; do
        ~/dlyfix/dlyfix $i -e dlyfix > /dev/null
    done

    cd $psrdir
    files=$(for i in dfb_data/J??????_??????.FT ; do d=dfb_data/$(basename $i .FT).dlyfix ; if [[ -e $d ]] ; then echo $d ; else echo $i ; fi ; done )

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
            pn=$(grep $fname best.tim | sed -e 's:.*\(-pn .[0-9]*\).*:\1:' | grep pn)
            half=$(grep $fname best.tim | sed -e 's:.*\(-halfperiod [A-Z0-9]*\).*:\1:' | grep halfperiod)
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
            echo "Warning: large pulse number change for $fname: $delta_pn. Marking as untrusted." >&2
            echo "$pn $F0 $toa1 $line2" >&2
        fi
        pn_new=$(echo "$pn $delta_pn" | awk '
        function nint(x) { return (x >= 0) ? int(x + 0.5) : int(x - 0.5) }
        { printf("-pn %d", nint($2 + $3)) }')
        echo "$line $pn_new $half $wflag"
        
    done > jbdfb_pn.tim

    wc -l best.tim
    grep "jbdfb" best.tim | grep "harmonic" > jdfb_harmonic.tim
    grep -v jbdfb best.tim > tmp.tim
    sort -nk3 tmp.tim jdfb_harmonic.tim jbdfb_pn.tim > jbdfb_fix.tim

    wc -l jbdfb_fix.tim
    trusted_file="jbdfb_fix.tim"

else
    echo "The DFB data appears to be up to date, skipping dlyfix check and pulse number fix for DFB data."
fi

cd $psrdir



rm -f update.pdf updated.tim update.stats


echo "New files: $newfiles_DFB $newfiles_ROACH $newfiles_ROACH_harmonic"
if [[ -n "$newfiles_DFB" ]] || [[ -n "$newfiles_ROACH" ]]  || [[ -n "$newfiles_ROACH_harmonic" ]]; then


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

    pat -A SIS -f tempo2 -s $std $newfiles | sed -e "s:jbdfb:jbdfb -be jbdfb:g" | sed -e "s:roach:roach -be jbroach:g" | grep -v nan > updates/pat.tim

    for f in $newfiles_ROACH_harmonic ; do
        harmonic_number=$(echo "$f" | sed -e 's/.*harmonic_\([0-9]\+\).FT/\1/')
        echo "Harmonic data... $f @ harm = $harmonic_number"
        
        harmonic_template="dfb_data/harm${harmonic_number}_1520.std"
        if [[ ! -e "$harmonic_template" ]]; then
            echo "Harmonic template not found: $harmonic_template, generating..." >&2
            $scriptdir/make_harmonic_template.py $std --harmonic-number $harmonic_number --output "$harmonic_template"
            if [[ ! -e "$harmonic_template" ]]; then
                echo "Failed to generate harmonic template: $harmonic_template, skipping." >&2
                exit 1
            fi
        fi
        pat -FT -A SIS -f tempo2 -s $harmonic_template $f | tail -n 1 | sed -e "s:roach:roach -be jbroach -harmonic ${harmonic_number} -tmplt $harmonic_template:g" | grep -v nan >> updates/pat.tim
    done


    sort -nk3 $trusted_file updates/pat.tim > updates/extended.tim

    grep -v -F -- "-distrust" $trusted_file > updates/trusted.tim

    cd updates

    default_enterprise_run=../enterprise_log3_sub3/
    if [[ -e "${default_enterprise_run}/${pulsar_name}_run.par.post" && -e "${default_enterprise_run}/${pulsar_name}.tim" ]] ; then
        echo "Making 'final.par' par file from $default_enterprise_run for pulse number extrapolation"
        tempo2 -f "${default_enterprise_run}/${pulsar_name}_run.par.post" "${default_enterprise_run}/${pulsar_name}.tim" -newpar 
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
    tempo2 -output add_pulseNumber -f update.par extended.tim -nofit


    if [[ $auto_flag -eq 0 ]]; then
        echo "No automatic processing by user request... Exiting after pulse number extrapolation step. The updated tim file with extrapolated pulse numbers is updates/withpn.tim"
        exit
    fi
        
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
        --mean-poly-order $mean_poly_order
        $fit_pm_flag
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
