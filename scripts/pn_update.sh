#!/bin/bash

#set -euo pipefail

 ~/change_data_symlink_to_vraid1

if [[ -n "$1" ]] ; then
    psrdir=$1
else
    psrdir=$(pwd)
fi

scriptdir=$(dirname "$(readlink -f "$0")")

cd $psrdir
psrdir=$(pwd)
pulsar_name=$(basename "$psrdir")

web_sync_target="${PN_WEB_SYNC_TARGET:-}"
interactive_flag="0"

std=dfb_data/1520.std
if [[ -e dfb_data/dfb1520.std ]] ; then
    std=dfb_data/dfb1520.std
fi

check_dlyfix=false
newfiles=""
cd dfb_data
for f in ../data_dir/dfb/J??????_??????.FT ; do
    l=$(basename $f)
    if [[ "$l" < "J2407" ]] ; then
        if [[ ! -e $l ]] ; then
            newfiles="$newfiles dfb_data/$l"
            ln -s $f .
            check_dlyfix=true
        fi
    fi
done
for f in ../data_dir/roach/2???*.ft ; do
    l=$(basename $f .ft)
    if [[ "$l" > "202306" ]] ; then
        newf=ROACH_${l}.FT
        if [[ ! -e $newf ]] ; then
            newfiles="$newfiles dfb_data/$newf"
            pam -FT -u . -e FTnew $f
            mv ${l}.FTnew $newf
            #ln -s $f ./ROACH_${l}.FT
        fi
    fi
done

if $check_dlyfix ; then

rm *.dlyfix
echo "Checking dlyfix"
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
        pn=`grep $fname best.tim | sed -e 's:.*\(-pn [0-9]*\)[^0-9].*:\1:' | grep pn`
        half=`grep $fname best.tim | sed -e 's:.*\(-halfperiod [A-Z]*\)[^A-Z].*:\1:' | grep halfperiod`
    else
        continue
    fi



    c=$(grep $fname best.tim | awk '{print $1}')
    if [[ "$c" == "C" ]] ; then
        line="C $line"
    fi

    toa1=$3
    line2=$(grep $fname best.tim)
    pn=$(echo $pn $F0 $toa1 $line2 | awk '{printf("-pn %d",($4-$7)*86400.0*$3+$2+0.5)}')
    echo "$line $pn $half"
done > jbdfb_pn.tim
backname=best.tim.`date +'%Y-%m-%dT%H:%M:%S'`

wc -l best.tim

cp best.tim $backname
cp -f $backname best.tim # Deal with files owned by someone else


grep -v jbdfb best.tim > tmp.tim
sort -nk3 tmp.tim jbdfb_pn.tim > best.tim


wc -l best.tim


fi

cd $psrdir


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
newfiles="$newfiles_DFB $newfiles_ROACH"

rm -f update.pdf updated.tim update.stats


echo "New files: $newfiles"
if [[ -n "$newfiles" ]] ; then


    wd=$(pwd)
    cd dfb_data
    echo "Checking dlyfix"
    fixfiles=""
    for i in $newfiles ; do
        b=$(basename $i)
        if [[ $i == "J*" ]] ; then
            fixed=$(basename $b .FT).dlyfix
            if [[ -e $fixed ]] ; then
                rm $fixed
            fi
            echo "DFB data... $i"
            ~/dlyfix/dlyfix $i -e dlyfix > /dev/null
            if [[ -e $fixed ]] ; then
                echo "Not delay fixed $i"
                fixfiles="$fixfiles dfb_data/$fixed"
            else
                fixfiles="$fixfiles $i"
            fi
        else
            fixfiles="$fixfiles $i"
        fi
    done
    cd $wd
    newfiles=$fixfiles

    mkdir -p updates

    rm updates/*

    pat -A SIS -f tempo2 -s $std $newfiles | sed -e "s:jbdfb:jbdfb -be jbdfb:g" | sed -e "s:roach:roach -be jbroach:g" | grep -v nan > updates/jdfb_update.tim
    cat best.tim updates/jdfb_update.tim > updates/extended.tim


    cd updates
    cp ../best.tim current.tim

    if [[ -e ../enterprise_log3_sub3/J1941+2525_run.par.post ]] ; then
        cp ../enterprise_log3_sub3/J1941+2525_run.par.post update.par
        tempo2 -f update.par current.tim -newpar
        mv new.par update.par
    elif [[ -e ../analysis/final.par ]] ; then
        cp ../analysis/final.par update.par
    else
        echo "No par file to use for pulse number extrapolation. Exiting."
        exit 1
    fi
    
    tempo2 -output add_pulseNumber -f update.par extended.tim

    run_id="${pulsar_name}_$(date -u +'%Y%m%d_%H%M%S')"
    stage_root="$psrdir/updates/review_runs"
    stage_dir="$stage_root/$pulsar_name/$run_id"
    mkdir -p "$stage_dir"
    manifest_path="$stage_dir/manifest.json"
    marker_path="$stage_dir/COMPLETE"
    extrapolate_cmd=(
        python ${scriptdir}/extrapolate_pulse_numbers.py
        --par update.par
        --tim current.tim
        --newtim withpn.tim
        --wrap-max 2
        --wrap-min -2
        --outlier-prob 0.1
        --particle-limit 32
        --time-tol 1e-6
        --covariance-scale 32
        --run-id "$run_id"
        --pulsar "$pulsar_name"
        --output-dir "$stage_dir"
        --manifest-output "$manifest_path"
        --complete-marker "$marker_path"
    )
    if [[ "$interactive_flag" == "1" ]]; then
        extrapolate_cmd+=(--interactive)
    fi
    echo "${extrapolate_cmd[@]}"
    "${extrapolate_cmd[@]}"

    if [[ -n "$web_sync_target" ]]; then
        # Sync stage_root to target, filtering to only this run; rsync creates directories automatically.
        rsync -avP \
          --include="/$pulsar_name/" \
          --include="/$pulsar_name/$run_id/" \
          --include="/$pulsar_name/$run_id/**" \
          --exclude="*" \
          "$stage_root/" "$web_sync_target/"
        echo "rsynced_run $web_sync_target/$pulsar_name/$run_id/"
    else
        echo "rsync_skipped_set_PN_WEB_SYNC_TARGET_to_enable"
    fi

else
    echo "No new files"
fi
