#!/bin/bash
#
#$#HEADER-START
#
#   Reactor Configuration Event Engine
#
#   Copyright (C) 2016 Brandon Gillespie
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as published
#   by the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#$#HEADER-END

################################################################################
# utilities
msg() {
    echo 
    echo "==> $@"
}

noerr() {
    "$@"
    if [ "$?" -gt 0 ]; then
        msg Cannot continue
        exit 1
    fi
}

get_ver() {
    local py=$1

    $py --version 2>&1 | sed -e 's/ /-/'|tr '[A-Z]' '[a-z]'
}

do_cleanup() {
    msg "cleanup installation..."
    find . -type f -name "*.pyc" -exec rm {} \;
    find . -type f -name "__pycache__" -exec rm {} \;
    rm -rf python dist virtual-python.spec
}

do_shebang() {
    local path=$1

    msg "Adjusting shebangs to $path"
    for f in bin/reflex bin/reflex-engine $(find test -type f); do
        shebang=$(head -1 $f | grep python)
        if [ -n "$shebang" ]; then
            base=$(basename $f)
            echo -n "$base "

            # sed -i is not uniform on MacOS and Linux, blah
            if cp $f $f.bak; then
                if sed -e "1 s+^.*\$+#"'!'"$path+" $f.bak > $f; then
                    rm -f $f.bak
                fi
            else
                echo ""
                echo "Cannot backup $f!"
            fi
        fi
    done
    echo ""
}

do_missing() {
    local what=$1
    local fix=$2

    cat <<END
Your $vers installation is missing: $what

Try:$fix
END
    exit 1
}

# if called with a pre-existing virtual_env, strip it out
if [ -n "$VIRTUAL_ENV" ]; then
    p=$(echo "$PATH" | sed -e 's!'${VIRTUAL_ENV}'/bin!!;s/::/:/')
    echo "REWROTE PATH=$p" 1>&2
    export PATH=$p
    unset VIRTUAL_ENV
fi

################################################################################
#
# args
#
hosted_python=/app/python-3/bin/python3
reqargs=''
python3=true
clean=false
virtualenv=true
engine=false
action=$1
shift

for x in "$@"; do
    case $x in
        --clean)
            clean=true
            ;;
        --engine|-engine)
            reqargs="-engine $reqargs"
            engine=true
            python3=true
            ;;
        *)
            alt_python=$x
            ;;
    esac
done

################################################################################
#
# validation
#

# to avoid accidental damage
if [ ! -f README.md -a ! -d .reflex ]; then
    echo "Run from reflex root please"
    exit 1
fi

case $action in
    ############################################################################
    hosted|--hosted)
        pypath=/app/local/bin/virtual-python
        install=hosted
        if [ $alt_python ]; then
            python=$alt_python
        else
            python=$hosted_python
        fi
        ;;

    ############################################################################
    local|--local)
        pypath=$(pwd)/bin/virtual-python
        install=local
        if [ $alt_python ]; then
            python=$alt_python
        else
            python=$(which python3)
            echo "python==$python"
        fi
        ;;

    ############################################################################
    root|--root)
        virtualenv=false
        install=local
        if [ $alt_python ]; then
            python=$alt_python
        else
            python=$(which python3)
            echo "python==$python"
        fi
        pypath=$python
        ;;

    ############################################################################
    env)
        base=$(dirname $0)
        base=$(cd $base; pwd)
        echo "export VIRTUAL_ENV=$base/python"
        echo "export PATH=$base/python/bin:\$PATH"
        exit
        ;;
    ############################################################################
    revert)
        do_cleanup
        do_shebang /app/local/bin/virtual-python
        cd bin
        rm -f virtual-python
        ln -s virtual-python.sh virtual-python
        exit
        ;;

    ############################################################################
    *)
        cat <<END
=> Syntax: $0 {action} [options]

=> Example:

    $0 root|local|hosted [python-binary] [--engine] [--clean]
    $0 revert

=> Action is \`root\`, \`local\` or \`hosted\`

   Setup the environment using the specified virtualenv type.

     root   = install using the root python libs
     hosted = install using virtualenv with a known location:
                  $hosted_python
     local  = install using virtualenv with the local python, or python
              as defined with the next argument.

=> Action is \`revert\`

   If \`revert\` specified, revert the code to a state which can be committed.

=> Options:

   --engine   : include building the DSE backend.  Python-3 required.  Default: no
   --clean : cleanup the virtualenv and python pre-compiled bytecode objects

END
        exit 1
    ;;
esac

## is our python functional?
if [ ! -x "$python" ]; then
    echo "Cannot execute $python"
    exit 1
elif [ "$($python --version 2>&1|grep -i python)" ]; then
    vers=$(get_ver $python)
else
    echo "$python is not python (?)"
    exit 1
fi

## should it be python3 ?
if [[ "$python3" = true && ! $vers =~ ^python-3 ]]; then
    echo "python3 required for this build, you specified $vers"
    exit 1
fi

## we need pip or virtualenv
pybin=$(dirname $python)
pip=$pybin/pip3

if [ ! -x $pip ]; then
    do_missing "pip" "
    curl -o https://bootstrap.pypa.io/get-pip.py
    sudo $pybin/python get-pip.py
"
elif [ $virtualenv = true -a ! -x $pybin/virtualenv ]; then
    do_missing "virtualenv" "
    sudo $pip install virtualenv
"
fi

origin_file=python/.origin
new_version=false

if [ "$clean" = "false" -a "$([ -f $origin_file ] && cat $origin_file)" != $python ]; then
    msg "Python versions do not match, forcing --clean"
    do_cleanup
    new_version=true
fi

################################################################################
#
# go for it
#
msg "$install install using $vers from $python"

if [ "$clean" == true ]; then
    do_cleanup
fi

# virtual environ
if [ $virtualenv = true ]; then
    $pybin/virtualenv -p $python python
    export VIRTUAL_ENV=$(pwd)/python
    export PATH=${VIRTUAL_ENV}/bin:${PATH}
	echo "$python" > $origin_file
fi

################################################################################
# requirements
msg "Requirements from src/rfx/requirements.txt"
noerr $pip install -Ur src/rfx/requirements.txt

if [ "$engine" = true ]; then
    msg "Requirements from src/rfxengine/requirements.txt"
    noerr $pip install -Ur src/rfxengine/requirements.txt
fi    

################################################################################
# import our stuff
msg "Linking modules into library path"
sitepkg=$($python -c 'import sys; l=[p for p in sys.path if "site-packages" in p]; print(l[-1])')
base=$(pwd)
for f in $(cat src/libs.txt); do
    echo "Linking $f"
    rm -f $sitepkg/$f
    noerr ln -s $base/src/$f $sitepkg/$f
done

################################################################################
# reflex engine specific things
if [ "$engine" = true ]; then
    echo engine=true > .pkg/did_engine
    owd=$(pwd)

    msg "Manually building mysql connector" # cause most other stuff sucks"

    cd python
    rm -rf mysql-connector-*
    mkdir tmp$$
    latest=tmp$$/mysql-connector-latest.tar.gz
    mysql_url=https://dev.mysql.com/get/Downloads/Connector-Python/mysql-connector-python-2.1.3.tar.gz
    curl -L -o $latest $mysql_url
    tar -xzf $latest

    cd mysql-connector*
    if [ $MYSQL_CAPI ]; then
        noerr python setup.py install --with-mysql-capi=$MYSQL_CAPI
    else
        noerr python setup.py install
    fi
    cd ..
    rm -rf mysql-connector-* tmp$$

    # could also do if we want to link to local mysql and C api (performant)
    # python setup.py install --with-mysql-capi=value

    cd $owd
else
    rm -f .pkg/did_engine
fi

################################################################################
do_shebang $pypath

################################################################################
# and bodger for macs
msg "For ease of use we have a special executable wrapper...  super hack time..."

if [ -x bin/virtual-python -a $new_version = false ]; then
    echo "Using existing virtual-python"
else
    noerr $pip install pyinstaller 2>/dev/null 1>/dev/null
    pyinstaller --onefile --windowed bin/virtual-python.py 2>/dev/null > /dev/null

    if [ $? -gt 0 ]; then
        echo "Using bourne shell virtual-python instead..."
        noerr rm -f bin/virtual-python
        noerr ln -s virtual-python.sh bin/virtual-python
    else
        # remove any existing versions
        noerr rm -f bin/virtual-python
        noerr cp dist/virtual-python bin
    fi

    rm -rf build dist virtual-python.spec
fi
