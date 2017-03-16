#!/bin/bash

VER=$1
if [ -z "$VER" ]; then
    echo "Version is not provided (arg1)"
    exit 1
fi

base=$(pwd)

if [ -n "$PYPI_DIST" ]; then
	PYPI_DIST="-r $PYPI_DIST"
else
	PYPI_DIST="-r pypitest"
fi

for d in rfx rfxcmd rfxengine; do
    echo $d
    cd $base/$d
	pwd
    sed --in-place -e 's/version = .*$/version = "'$VER'",/' setup.py
    rm -rf ./dist rfx.egg-info || exit 1
    python3 setup.py sdist
    find .
    twine upload $PYPI_DIST dist/*
done
