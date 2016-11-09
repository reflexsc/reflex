#!/bin/bash

VER=$1
if [ -z "$VER" ]; then
    echo "Version is not provided (arg1)"
    exit 1
fi

base=$(pwd)

for d in rfx rfxcmd rfxengine; do
    echo $d
    cd $base/$d
    sed -i -e 's/version = .*$/version = "'$VER'",/' setup.py
done
