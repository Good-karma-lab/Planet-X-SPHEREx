#!/usr/bin/env bash
# the layout audit at eight window sizes, four at a time; merges the reports into $OUT/report.json
OUT=${1:?outdir}; BASE=${2:-http://localhost:5190}
cd "$(dirname "$0")/../.."
for g in 1440,375 1280,844 1024,600 860,700; do SIZES=$g node tests/layout/overlaps.mjs "$BASE" "$OUT/$g" > "$OUT.$g.log" 2>&1 & done
wait
node -e 'const fs=require("fs");const o={};for(const g of ["1440,375","1280,844","1024,600","860,700"])Object.assign(o,JSON.parse(fs.readFileSync(process.argv[1]+"/"+g+"/report.json")));fs.writeFileSync(process.argv[1]+"/report.json",JSON.stringify(o));console.log(Object.keys(o).length+" page×size")' "$OUT"
