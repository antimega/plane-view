#!/bin/sh
# Build the three example forecasts, their pages, PDF guides and flight packs.
#
#   sh examples/build.sh           the forecasts and standard packs
#   sh examples/build.sh --hd      ... and a high-detail pack of the Innsbruck flight (a long render)
#
# Needs flight history for the routes first: see examples/README.md, "Making them yourself".
# Run from the repository folder. Results go to out/, site/ and out/packs/.
set -e
cd "$(dirname "$0")/.."

uv run planeview predict --date 2027-03-14 --from BRU --to AGP --dep 10:20
uv run planeview predict --date 2027-01-16 --from LGW --to INN --dep 11:35
uv run planeview predict --date 2027-06-19 --from LHR --to PHX --dep 15:05

uv run planeview pack out/2027-03-14_BRU-AGP out/2027-01-16_LGW-INN out/2027-06-19_LHR-PHX

if [ "$1" = "--hd" ]; then
    uv run planeview pack out/2027-01-16_LGW-INN --detail high
fi

echo
echo "Page:  site/index.html   (uv run planeview serve)"
echo "Packs: out/packs/"
