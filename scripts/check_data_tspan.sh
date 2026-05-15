#!/bin/bash


usage() {
    echo "Usage: $0 [--check-last-obs] [pulsar list]"
}

pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"

pulsars=()
check_last_obs=false
while [[ $# -gt 0 ]] ; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --check-last-obs)
            check_last_obs=true
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
            pulsars+=("$1")
            shift
            ;;
    esac
done

scriptdir=$(dirname "$(readlink -f "$0")")


for psr in "${pulsars[@]}" ; do
    psrdir=$pulsar_base_dir/$psr
    if [[ ! -d "$psrdir" ]]; then
        echo "Pulsar directory not found: $psrdir" >&2
        exit 2
    fi

    cd "$psrdir"
    
    $scriptdir/change_data_symlink_to_vraid1 > /dev/null
    # $scriptdir/apply_web_updates.sh "$psr"
    # extract first and last ToA from best.tim, skipping comments starting with "C" or "#" and any lines with fewer than 4 columns
    first_last_year=$(grep -v -E "^[C#]" best.tim | awk '
        function mjd_to_year(mjd) {
            return (mjd - 51544) / 365.25 + 2000
        }
        BEGIN {
            first = 999999
            last = -999999
        }
        NF >= 4 {
            if ($3 < first) first = $3
            if ($3 > last) last = $3
        }
        END {
            printf("%.2f %.2f\n", mjd_to_year(first), mjd_to_year(last));
        }
    ')
    first_year=$(echo "$first_last_year" | awk '{print $1}')
    last_year=$(echo "$first_last_year" | awk '{print $2}')
    

    extra_time=""
    if [[ "$check_last_obs" == true ]]; then
        last_obs=$(ls -1 data_dir/roach/2???????_??????_*.FT* | tail -n 1)
        # convert filename to datetime, assuming format like 20230601_123456_*.FT
        last_obs_date=$(echo "$last_obs" | sed -E 's/^.*([0-9]{8})_[0-9]{6}.*$/\1/')
        last_obs_fracyear=$(date -d "$last_obs_date" +"%Y %j" | awk '{
                        year=$1;
                        doy=$2;
                        leap=( (year%4==0 && year%100!=0) || (year%400==0) );
                        days=leap?366:365;
                        printf "%.2f\n", year + (doy-1)/days;
                        }')
        new_data=false
        extra_time="0"
        if (( $(echo "$last_obs_fracyear > $last_year" | bc -l) )); then
            new_data=true
            extra_time=$(printf "%.1f" $(echo "$last_obs_fracyear - $last_year" | bc -l))
        fi
        
    fi

    printf "%-10s %-6s %-6s %-6s %-6s\n" "$psr" "$first_last_year" "$last_obs_fracyear" "$extra_time" "$new_data"
done