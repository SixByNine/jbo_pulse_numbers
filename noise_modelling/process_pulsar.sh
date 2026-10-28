#!/bin/bash

util_dir=~/Code/run_enterprise/utilities
export OMP_NUM_THREADS=1

rootdir=/Users/user/scratch/process_jbo
workdir=${rootdir}/work

mode="dynesty"
tspan_opt="log2"
skip_done=0
redsub=2
psrs=()
psr_file=""

usage() {
    echo "Usage: $0 [options] [PSR ...]"
    echo "Options:"
    echo "  --psrlist FILE   Read pulsar names from FILE"
    echo "  --skip           Skip pulsars with DONE marker"
    echo "  --subcubic       Use --red-subpoly 3"
    echo "  --nosubpoly      Disable sub-polynomial subtraction"
    echo "  --dynesty        Use dynesty sampler (default)"
    echo "  --emcee          Use emcee sampler"
    echo "  --tspan1         Use tspan multiplier only"
    echo "  --log2           Use log2 red-noise binning (default)"
    echo "  --log3           Use log3 red-noise binning"
    echo "  --log4           Use log4 red-noise binning"
}

configure_sampler() {
    sampler_opts=()
    if [[ "$mode" == "dynesty" ]] ; then
        sampler_opts=(
            --dynesty
            --dynesty-dynamic
            --nlive 128
            --plotlibrary chainconsumer
            --dynesty-maxiter-per-batch 1000
            --dynesty-maxbatch 10
            --dynesty-maxiter 50000
        )
    elif [[ "$mode" == "emcee" ]] ; then
        sampler_opts=(--emcee -N 64 --nwalkers 64 --plotlibrary chainconsumer)
    else
        echo "No valid mode selected: $mode" >&2
        return 1
    fi
}

configure_tspan() {
    tspan_opts=(--tspan-mult 1)
    case "$tspan_opt" in
        tspan1)
            ;;
        log2)
            tspan_opts+=(--red-nlog-below-tspan 2 --red-nlog-factor 2)
            ;;
        log3)
            tspan_opts+=(--red-nlog-below-tspan 3 --red-nlog-factor 2)
            ;;
        log4)
            tspan_opts+=(--red-nlog-below-tspan 4 --red-nlog-factor 2)
            ;;
        *)
            echo "Unknown tspan option: $tspan_opt" >&2
            return 1
            ;;
    esac
}

run_enterprise_stage1() {
    local psr=$1
    local tim=$2
    local redmax=$3

    local cmd=(
        nice -n 15 run_enterprise "${psr}_first.par" "$tim"
        --Ared-max "$redmax"
        --red-at 10
        --red-prior-log
        --plot-chain
        --emcee
        --nthread 8
        --white-prior-log
        -N 1000
        -j
        --nwalkers 8
        "${tspan_opts[@]}"
        --svd
        --red-subpoly 2
    )

    printf '%q ' "${cmd[@]}"
    echo
    "${cmd[@]}"
}

run_enterprise_stage2() {
    local psr=$1
    local tim=$2
    local redmax=$3
    local pmrange=$4
    local f2range=$5
    shift 5
    local special_opts=("$@")

    local cmd=(
        nice -n 15 run_enterprise "${psr}_run.par" "$tim"
        -j
        --efac-min 0.1
        --efac-max 8
        --plot-chain
        --all-corner
        --red-ncoeff 200
        --Ared-max "$redmax"
        --red-at 10
        --pm
        --pm-range "$pmrange"
        --f2 "$f2range"
        --px
        --nthread 8
        --white-prior-log
        --red-prior-log
        "${sampler_opts[@]}"
        "${tspan_opts[@]}"
        --svd
        --red-subpoly "$redsub"
        --legendre
        --plot-derived
        --dump-chain
        "${special_opts[@]}"
    )

    printf '%q ' "${cmd[@]}"
    echo
    "${cmd[@]}"
}


make_final_par() {
    local post_par=$1
    local tim=$2
    local outpar=$(basename "$post_par" .post).final
    local cmd=(
        nice -n 10 tempo2
        -f "$post_par" "$tim"
        -outpar "$outpar"
        -qrfit
        -epoch centre
    )
    printf '%q ' "${cmd[@]}"
    echo
    run_with_timeout 120 "${cmd[@]}"
}

run_with_timeout() {
    local timeout_secs=$1
    shift

    local timeout_marker
    timeout_marker=$(mktemp)

    if command -v setsid >/dev/null 2>&1 ; then
        setsid "$@" &
    else
        perl -e 'setpgrp(0,0); exec @ARGV' "$@" &
    fi
    local cmd_pid=$!
    local cmd_pgid
    cmd_pgid=$(ps -o pgid= -p "$cmd_pid" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$cmd_pgid" ]] && cmd_pgid="$cmd_pid"

    (
        sleep "$timeout_secs"
        if kill -0 "$cmd_pid" 2>/dev/null ; then
            echo 1 > "$timeout_marker"
            kill -TERM -- "-$cmd_pgid" 2>/dev/null || kill "$cmd_pid" 2>/dev/null
            sleep 2
            kill -KILL -- "-$cmd_pgid" 2>/dev/null || kill -9 "$cmd_pid" 2>/dev/null
        fi
    ) </dev/null >/dev/null 2>&1 &
    local watcher_pid=$!

    wait "$cmd_pid"
    local cmd_status=$?

    kill "$watcher_pid" 2>/dev/null
    wait "$watcher_pid" 2>/dev/null

    if [[ -s "$timeout_marker" ]] ; then
        rm -f "$timeout_marker"
        return 124
    fi

    rm -f "$timeout_marker"
    return "$cmd_status"
}

make_plots(){
    local psr=$1
    local final_par=$2
    local tim=$3

    local src_dir
    src_dir=$(pwd)
    local tmp_dir
    tmp_dir=$(mktemp -d)

    cp "$final_par" "$tmp_dir/"
    cp "$tim" "$tmp_dir/"

    local final_par_base
    final_par_base=$(basename "$final_par")
    local tim_base
    tim_base=$(basename "$tim")

    if ! (cd "$tmp_dir" && nice -n 10 make_pulsar_plots.py "$final_par_base" "$tim_base"); then
        (cd "$tmp_dir" && nice -n 10 make_pulsar_plots.py "$final_par_base" "$tim_base" --noerr) || {
            rm -rf "$tmp_dir"
            return 1
        }
    fi

    # Preserve only the desired plot outputs.
    local outputs=(
        "combined_${psr}.pdf"
        "nudot.asc"
        "deltanu.asc"
        "white.par"
        "white_ifunc.par"
    )

    local output
    for output in "${outputs[@]}" ; do
        if [[ -e "$tmp_dir/$output" ]] ; then
            cp "$tmp_dir/$output" "$src_dir/"
        fi
    done

    rm -rf "$tmp_dir"
}

run_models_for_pulsar() {
    local par=$1
    local tim=$2

    "$util_dir/sort_tim.py" "$tim" | grep -ve "^C" > sorted.tim
    mv sorted.tim "$tim"

    local psr
    psr=$(grep PSRJ "$par" | awk '{print $2}')

    local dm
    dm=$(grep -e "^ " "$tim" | awk '{if ($2 < 1200 || $2 > 2000) print "DM"}' | head -n 1)

    local par_tmp="${par}.tmp"
    local par_tmp2="${par}.tmp2"
    "$util_dir/disableparam.py" "$par" START FINISH  > "$par_tmp"
    cat $par_tmp

    local disable_glitch=0
    if [[ $disable_glitch -eq 1 ]]; then
        "$util_dir/enableparam.py" "$par_tmp" JUMP RAJ DECJ F0 F1 PX PB A1 OM OMDOT XDOT PBDOT ECC T0 TASC EPS1 EPS2 PMRA PMDEC "$dm" DM1 DM2 | grep -v -e '^F[2-9]' > "${psr}_first.par"
    else
        "$util_dir/enableparam.py" "$par_tmp" JUMP RAJ DECJ F0 F1 PX PB A1 OM OMDOT XDOT PBDOT ECC T0 TASC EPS1 EPS2 PMRA PMDEC "$dm" DM1 DM2 -glitch | grep -v -e '^F[2-9]' > "${psr}_first.par"
    fi

    grep -v "FORMAT\|MODE\|^C" "$tim" | sort -nk3 | head -n 1 | awk '{print "START", $3-0.5}' >> "${psr}_first.par"
    grep -v "FORMAT\|MODE\|^C" "$tim" | sort -nk3 | tail -n 1 | awk '{print "FINISH", $3+0.5}' >> "${psr}_first.par"

    backend_priority=("jbdfb" "jbafb" "jbroach")

    # Get unique backends found in the current tim file (macOS bash has no mapfile).
    backends=()
    while IFS= read -r backend ; do
        [[ -n "$backend" ]] && backends+=("$backend")
    done < <(awk '{for (i=1; i<=NF; i++) if ($i == "-be" && (i+1) <= NF) print $(i+1)}' "$tim" | sort -u)

    # Remove only backend JUMPs (those with -be) from the generated par file.
    sed -i.bak "/^JUMP.*-be/d" "${psr}_first.par"

    # Choose exactly one backend to leave un-jumped: first available in priority order.
    local ref_backend=""
    local p
    for p in "${backend_priority[@]}" ; do
        local backend
        for backend in "${backends[@]}" ; do
            if [[ "$backend" == "$p" ]] ; then
                ref_backend="$backend"
                break 2
            fi
        done
    done

    # Fallback if no priority backend is present: keep the first available backend un-jumped.
    if [[ -z "$ref_backend" && ${#backends[@]} -gt 0 ]] ; then
        ref_backend="${backends[0]}"
    fi

    echo "All backends found: ${backends[*]}"

    # Add backend JUMPs for all but the selected reference backend.
    local backend
    for backend in "${backends[@]}" ; do
        if [[ "$backend" == "$ref_backend" ]] ; then
            echo "Skipping JUMP for backend $backend (reference backend)"
            continue
        fi
        echo "JUMP -be $backend 0 1" >> "${psr}_first.par"
    done

    local f2
    f2=$(grep -e '^F2 ' "$par" | awk '{print $2}')
    if [[ -z "$f2" ]] ; then
        f2=0
    fi
    echo "F2 $f2 1" >> "${psr}_first.par"

    if grep -q "PMDEC" "$par" ; then
        echo "PM already included"
    else
        echo "PMRA 0 1" >> "${psr}_first.par"
        echo "PMDEC 0 1" >> "${psr}_first.par"
    fi

    local redmax=-5
    # Increase initial redmax for J2229+6114 and J0205+6449 which have very high red noise levels.
    if [[ "$psr" == "J2229+6114" || "$psr" == "J0205+6449" ]] ; then
        redmax=-3
    fi
    run_enterprise_stage1 "$psr" "$tim" "$redmax"

    mv "${psr}.corner.pdf" "${psr}.corner_first.pdf"
    mv "${psr}.chain.pdf" "${psr}.chain_first.pdf"
    echo TNSubtractPoly 1 >> "${psr}_first.par.post"

    tempo2 -f "${psr}_first.par.post" "$tim" -outpar "$par_tmp2" -qrfit -epoch centre

    f2=$(grep -e '^F2 ' "$par_tmp2" | awk '{print $2}')
    local f2e
    f2e=$(grep -e '^F2 ' "$par_tmp2" | awk '{print $4}')
    local f2range
    f2range=$(echo "$f2 $f2e" | awk '{print 5*$2 + 5*sqrt($1*$1)}')

    local pmrange
    pmrange=$(grep -e '^PMDEC ' "$par_tmp2" | awk '{print $4*20}')

    redmax=$(grep TNRedAmp_T10.0 "${psr}_first.par.results" | awk '{print $9+$4}')
    # check for override redmax in override_redmax.txt
    local override_file=${rootdir}/override_redmax.txt
    if [[ -f $override_file ]] ; then
        local override_redmax
        override_redmax=$(grep "^$psr " "$override_file" | awk '{print $2}')
        if [[ -n "$override_redmax" ]] ; then
            echo "Overriding redmax for $psr: $redmax -> $override_redmax"
            redmax="$override_redmax"
        fi
    fi
    local override_file=${rootdir}/override_f2.txt
    if [[ -f $override_file ]] ; then
        local override_f2
        override_f2=$(grep "^$psr " "$override_file" | awk '{print $2}')
        override_f2range=$(grep "^$psr " "$override_file" | awk '{print $3}')
        if [[ -n "$override_f2" ]] ; then
            echo "Overriding F2 for $psr: $f2 -> $override_f2"
            f2="$override_f2"
        fi
        if [[ -n "$override_f2range" ]] ; then
            echo "Overriding F2 range for $psr: $f2range -> $override_f2range"
            f2range="$override_f2range"
        fi
    fi


    "$util_dir/disableparam.py" "$par_tmp2" F2 PMRA PMDEC | grep -v "^PX" | grep -ve '^TN' > "${psr}_run.par"

    rm "$par_tmp" "$par_tmp2"

    echo "F2 range is: $f2 +/- $f2range"
    echo "PM range is: $pmrange"
    echo "Red Max is : $redmax"

    echo "F2 range is: $f2range" >> "${psr}_first.par.results"
    echo "PM range is: $pmrange" >> "${psr}_first.par.results"
    echo "Red Max is : $redmax" >> "${psr}_first.par.results"

    local special_opts=()
    if [[ "$psr" == "B1828-11" ]] ; then
        echo "---"
        echo "SPECIAL B1828-11 options"
        special_opts=(--qp --qp-p-min 300 --qp-p-max 650)
        echo " >>>> ${special_opts[*]}"
        echo "---"
    fi

    run_enterprise_stage2 "$psr" "$tim" "$redmax" "$pmrange" "$f2range" "${special_opts[@]}"
    
    make_final_par "${psr}_run.par.post" "$tim"
    local final_par_status=$?
    if [[ $final_par_status -ne 0 ]] ; then
        local err_msg
        if [[ $final_par_status -eq 124 ]] ; then
            err_msg="ERROR: make_final_par timed out after 120 seconds for $psr"
        else
            err_msg="ERROR: make_final_par failed for $psr (exit $final_par_status)"
        fi
        echo "$err_msg" | tee -a "$log"
        echo "$err_msg" >> ERROR
        return 1
    fi

    make_plots "$psr" "${psr}_run.par.final" "$tim"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --psrlist)
            shift
            psr_file=$1
            shift
            ;;
        --skip)
            skip_done=1
            shift
            ;;
        --subcubic)
            redsub=3
            shift
            ;;
        --nosubpoly)
            redsub=0
            shift
            ;;
        --dynesty)
            mode="dynesty"
            shift
            ;;
        --emcee)
            mode="emcee"
            shift
            ;;
        --tspan1)
            tspan_opt="tspan1"
            shift
            ;;
        --log2)
            tspan_opt="log2"
            shift
            ;;
        --log3)
            tspan_opt="log3"
            shift
            ;;
        --log4)
            tspan_opt="log4"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --) # end of options
            shift
            psrs+=("$@")
            break
            ;;
        --*) # unknown option
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *) # positional argument
            psrs+=("$1")
            shift
            ;;
    esac
done

if [[ -n "$psr_file" ]] ; then
    while IFS= read -r line ; do
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^# ]] && continue
        psrs+=("$line")
    done < "$psr_file"
fi

if [[ ${#psrs[@]} -eq 0 ]] ; then
    echo "No pulsars provided" >&2
    usage
    exit 1
fi

configure_sampler || exit 1
configure_tspan || exit 1

echo "Pulsars: ${psrs[*]}"
echo "Sampler mode: $mode"
echo "Tspan option: $tspan_opt"
echo "Red-subpoly: $redsub"

for psr in ${psrs[@]} ; do

    if [[ -e STOP ]] ; then
        echo "STOP due to STOP file"
        break
    fi
    flag="${psr}_${tspan_opt}_sub$redsub"
    if [[ $skip_done -eq 1 ]] ; then
        if [[ -e "$workdir/$flag/DONE" ]] ; then
            echo "Skip $psr ... already done"
            continue
        fi
    fi
    echo "Doing $psr"
    echo "$HOSTNAME" $(date)
    mkdir -p "$workdir"
    cd "$workdir"
    if [[ -e "$workdir/$flag" ]] ; then
        rm -rf "$workdir/$flag"
    fi

    mkdir -p "$workdir/$flag"
    
    log="$workdir/$flag/log"
    dn="$workdir/$flag/DONE"

    
    
    cd "$workdir/$flag"

    echo "$HOSTNAME" > "$log"
    remote_base="godrevy:/scratch/mkeith/jbo_dataset/4_ready_for_timing/$psr"
    copy_ok=0
    for attempt in 1 2 ; do
        rm -f "$psr.par" "$psr.tim" best.par best.tim
        if rsync -avP --include="best.par" --include="best.tim" --exclude="*" \
            "$remote_base/" . \
            && [[ -s best.par && -s best.tim ]] ; then
            mv best.par "$psr.par"
            mv best.tim "$psr.tim"
            if [[ -s "$psr.par" && -s "$psr.tim" ]] ; then
                copy_ok=1
                break
            fi
        fi

        echo "Failed to copy timing files for $psr (attempt $attempt/2)" | tee -a "$log"
    done

    if [[ $copy_ok -ne 1 ]] ; then
        echo "ERROR: Unable to copy best.par and best.tim for $psr after 2 attempts" | tee -a "$log"
        echo "ERROR: Unable to copy best.par and best.tim for $psr after 2 attempts" > ERROR
        continue
    fi

    run_models_for_pulsar "$psr.par" "$psr.tim" 2>&1 | tee -a "$log"
    model_status=${PIPESTATUS[0]}
    if [[ $model_status -ne 0 ]] ; then
        echo "ERROR: Model processing failed for $psr" | tee -a "$log"
        [[ -e ERROR ]] || echo "ERROR: Model processing failed for $psr" > ERROR
        continue
    fi

    cp "$log" .
    rsync -avP --exclude=chains --delete "$workdir/$flag/" "godrevy:/scratch/mkeith/jbo_dataset/4_ready_for_timing/$psr/enterprise_${tspan_opt}_sub$redsub" 2>&1 | tee -a "$log"

    echo "$HOSTNAME" $(date)
    echo "$HOSTNAME" $(date) > "$dn"
    cd "$workdir"
    #rm -rf $workdir/$psr
    #
    #
done
