#!/bin/bash

VER=$1
if [ -z "$VER" ]; then
    echo "Version is not provided (arg1)"
    exit 1
fi
shift
targets="$@"
if [ -z "$targets" ]; then
	targets="rfx rfxcmd rfxengine"
fi

base=$(pwd)

if [ -n "$PYPI_DIST" ]; then
	PYPI_DIST="-r $PYPI_DIST"
else
	PYPI_DIST="-r pypitest"
fi

if [ ! -x ./clean.sh ]; then
    echo "cannot find clean script"
	exit 1
fi

./clean.sh || exit 1

for d in $targets; do
    echo $d
    cd $base/$d
	pwd
    sed --in-place -e 's/version = .*$/version = "'$VER'",/' setup.py
    rm -rf ./dist rfx.egg-info || exit 1
    python3 setup.py sdist || exit 1
    find .
    twine upload $PYPI_DIST dist/* || exit 1
done
