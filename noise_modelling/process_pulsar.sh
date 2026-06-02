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
        nice -n 10 run_enterprise "${psr}_first.par" "$tim"
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
        nice -n 10 run_enterprise "${psr}_run.par" "$tim"
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
        tempo2
        -f "$post_par" "$tim"
        -outpar "$outpar"
        -qrfit
        -epoch centre
    )
    printf '%q ' "${cmd[@]}"
    echo
    "${cmd[@]}"
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

    if ! (cd "$tmp_dir" && make_pulsar_plots.py "$final_par_base" "$tim_base"); then
        (cd "$tmp_dir" && make_pulsar_plots.py "$final_par_base" "$tim_base" --noerr) || {
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
    "$util_dir/disableparam.py" "$par" START FINISH > "$par_tmp"

    local disable_glitch=0
    if [[ $disable_glitch -eq 1 ]]; then
        "$util_dir/enableparam.py" "$par_tmp" JUMP RAJ DECJ F0 F1 PX PB A1 OM OMDOT XDOT PBDOT ECC T0 TASC EPS1 EPS2 PMRA PMDEC "$dm" DM1 DM2 | grep -v -e '^F[2-9]' > "${psr}_first.par"
    else
        "$util_dir/enableparam.py" "$par_tmp" JUMP RAJ DECJ F0 F1 PX PB A1 OM OMDOT XDOT PBDOT ECC T0 TASC EPS1 EPS2 PMRA PMDEC "$dm" DM1 DM2 -glitch | grep -v -e '^F[2-9]' > "${psr}_first.par"
    fi

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

    make_plots "$psr" "${psr}_run.par.post" "$tim"
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
        if [[ -e "$rootdir/flags/$flag/DONE" ]] ; then
            echo "Skip $psr ... already done"
            continue
        fi
    fi
    mkdir -p "$workdir"

    mkdir -p "$rootdir/flags/$flag"
    echo "Doing $psr"
    echo "$HOSTNAME" $(date)
    log="$rootdir/flags/$flag/log"
    dn="$rootdir/flags/$flag/DONE"

    cd "$workdir"
    if [[ -e "$workdir/$flag" ]] ; then
        rm -rf "$workdir/$flag"
    fi
    mkdir "$workdir/$flag"
    cd "$workdir/$flag"

    echo "$HOSTNAME" > "$log"
    rsync -avP "godrevy:/scratch/mkeith/jbo_dataset/4_ready_for_timing/$psr/best.par" "$psr.par"
    rsync -avP "godrevy:/scratch/mkeith/jbo_dataset/4_ready_for_timing/$psr/best.tim" "$psr.tim"

    run_models_for_pulsar "$psr.par" "$psr.tim" 2>&1 | tee -a "$log"
    cp "$log" .
    rsync -avP --exclude=chains --delete "$workdir/$flag/" "godrevy:/scratch/mkeith/jbo_dataset/4_ready_for_timing/$psr/enterprise_${tspan_opt}_sub$redsub" 2>&1 | tee -a "$log"

    echo "$HOSTNAME" $(date)
    echo "$HOSTNAME" $(date) > "$dn"
    cd "$workdir"
    #rm -rf $workdir/$psr
    #
    #
done
