#!/bin/bash

mjd1="${1:?Error: mjd1 (argument 1) is required. Usage: $0 <mjd1> <length>}"
length="${2:?Error: length (argument 2) is required. Usage: $0 <mjd1> <length>}"

# Script continues here only if both arguments are provided
echo "Processing mjd1=$mjd1 with length=$length"

psr=$(basename $(pwd))
echo "PSR=$psr"

mjd2=$(echo "$mjd1 + $length" | bc -l)

tempo2 -f tempo1.par -tempo1 -polyco "$mjd1 $mjd2 60 12 12 coe 1520"
mv polyco_new.dat ${psr}_polyco.dat


std=dfb_data/1520.std
if [[ -e dfb_data/dfb1520.std ]] ; then
    std=dfb_data/dfb1520.std
fi

last=$(psrtrash dfb_data/ROACH_202*.FT | sort | tail -n 1 | awk '{print $2}')
echo psrsh ~/install_tempo1_dd1520.psrsh $last -O . -e dd
psrsh ~/install_tempo1_dd1520.psrsh $last -O . -e dd

pav -FTD $(basename $last .FT).dd $std -N 1,2
pdv -FT -t $std > ${psr}_std.txt
pdv -FT -t $(basename $last .FT).dd > ${psr}_obs.txt

echo "-----"
head ${psr}_polyco.dat
echo "-----"
tail ${psr}_polyco.dat
echo "-----"

echo "cp ${psr}_std.txt ${psr}_obs.txt ${psr}_polyco.dat ~/vlbi_export/"
cp ${psr}_std.txt ${psr}_obs.txt ${psr}_polyco.dat ~/vlbi_export/
