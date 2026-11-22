#!/usr/bin/env bash
# Run a whole TEST (several runs with the same settings) with run_experiment.py, then analyse it with analyze.py.
#
# Usage (from anywhere, with the project venv active and the lab running):
#   ./experiments/topology-comparison/run_test.sh <test> <number of runs> [options for run_experiment.py]
#
# Examples:
#   ./experiments/topology-comparison/run_test.sh baseline 5
#   ./experiments/topology-comparison/run_test.sh size_1m 5 --page 1m.bin
#   ./experiments/topology-comparison/run_test.sh smoke 2 --requests 5 --pause 1 2
#
# The runs are called run_01, run_02, ... and saved in results/onion/<test>/ next to this script.
# Every run of a test gets the same options, as analyze.py requires.
#
# Resuming: a run that is already complete (its folder has run_info.json) is skipped, so after an interruption
# the same command continues where it stopped. A run that was interrupted halfway has a folder without
# run_info.json: run_experiment.py refuses to overwrite it, so delete that folder first.
# The first run that fails stops the whole test, so a broken lab does not produce a series of broken runs.
set -euo pipefail

if [ $# -lt 2 ] || ! [[ "$2" =~ ^[1-9][0-9]*$ ]]; then
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
test_name=$1
runs=$2
shift 2

for i in $(seq 1 "$runs"); do
    run=$(printf 'run_%02d' "$i")
    if [ -f "$HERE/results/onion/$test_name/$run/run_info.json" ]; then
        echo "== $test_name/$run already done, skipped"
        continue
    fi
    echo
    echo "######## $test_name: $run of $runs ########"
    python "$HERE/run_experiment.py" --test "$test_name" --run "$run" "$@"
done

echo
echo "######## $test_name: analysis ########"
python "$HERE/analyze.py" "$HERE/results/onion/$test_name"
