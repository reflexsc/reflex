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
    rm -rf python virtual-python.spec
    rm -f .pkg/did_*
}

setup_env() {
    install=$1
    python=$2

	if [ $alt_python ]; then
	    python=$alt_python
	fi
	echo "python==$python"

    ################################################################################
    # validation

    if [ ! -x "$python" ]; then
        echo "Cannot execute $python"
        exit 1
    elif [ "$($python --version 2>&1|grep -i python)" ]; then
        vers=$(get_ver $python)
    else
        echo "$python is not python (?)"
        exit 1
    fi

    if [[ ! $vers =~ ^python-3 ]]; then
        echo "python3 required for this build, you specified $vers"
        exit 1
    fi

    origin_file=python/.origin
    
    if [ -f $origin_file ]; then
        if [ $(cat $origin_file) != $python ]; then
            msg "Cleaning different python version"
            do_cleanup
        fi
    fi

    ################################################################################
    #
    # go for it
    #
    msg "$install install using $vers from $python"

    # virtual environ
    if [ $virtualenv = true ]; then
    	if [ ! -d python ]; then
    		$(dirname $python)/virtualenv -p $python python
    		echo "$python" > $origin_file
    	fi
    	source $(pwd)/python/bin/activate
    	python=$(which python3)
    fi

	export python
}

setup_install() {
    install=$1
    python=$2

	echo "i$install $python"
	setup_env $install $python
	echo "i$install $python"

	pip=$(dirname $python)/pip3

	pip install -U rfxcmd rfxengine
}

setup_development() {
    install=$1
    python=$2

	setup_env $install $python

    owd=$(pwd)
    cd $owd/pypi/rfx; noerr $python setup.py develop
    cd $owd/pypi/rfxcmd; noerr $python setup.py develop
    cd $owd/pypi/rfxengine; noerr $python setup.py develop
	cd $owd
	setup_mysql
}

setup_mysql() {
    ################################################################################
    # reflex engine specific things
	mysql_version=2.1.4

    owd=$(pwd)
	if [ -f $owd/.pkg/did_mysql ]; then
		msg "Skipping MySQL install -- appears to be already there."
		echo "    run $0 clean to reset"
		return
	fi

	$owd/src/rfxengine/db/get_mysql_connector.py

    touch $owd/.pkg/did_mysql
    touch $owd/.pkg/did_engine
}

# if called with a pre-existing virtual_env, strip it out
if [ -n "$VIRTUAL_ENV" ]; then
    deactivate >/dev/null 2>&1

    p=$(echo "$PATH" | sed -e 's!'${VIRTUAL_ENV}'/bin!!;s/::/:/')
    echo "REWROTE PATH=$p" 1>&2
    export PATH=$p
    unset VIRTUAL_ENV
fi

# to avoid accidental damage
if [ ! -f README.md -a ! -d .reflex ]; then
    echo "Run from reflex root please"
    exit 1
fi

################################################################################
# setup default python
python=/app/python-3/bin/python3
if [ ! -x $python ]; then
	python=$(which python3)
fi
virtualenv=false
opt_itype=
action=$1
shift
# pull out optins
for x in "$@"; do
    case $x in
        --root)
            ;;
        --no-virtual)
			virtualenv=false
			;;
        --hosted)
            opt_itype=hosted
            virtualenv=true
            ;;
        --local)
            opt_itype=local
            virtualenv=true
            python=$(which python3)
            ;;
        *)
            alt_python=$x
            ;;
    esac
done

case $action in
    ############################################################################
    dev|develop|devel)
        virtualenv=true
        if [ $opt_itype ]; then
			setup_development $opt_itype $python
		else
			setup_development hosted $python
		fi
        ;;

    ############################################################################
    install)
		echo $python
		if [ $opt_itype ]; then
			setup_install $opt_itype $python
		else
			setup_install root $python
		fi
		setup_mysql
		;;

    ############################################################################
    env)
        base=$(dirname $0)
	    source $base/python/bin/activate
        ;;

    ############################################################################
    revert|clean)
        do_cleanup
        ;;

    ############################################################################
    *)
        cat <<END
=> Syntax: $0 {action} [options]

=> Example:

    $0 env|clean|develop|install [python-binary] [options...]

Options: (order matters):

   --root   = install using the root python libs (default)
   --hosted = install using virtualenv with a known location:
                  $hosted_python
   --local  = install using virtualenv with the local python:
                  $(which python3)
   
Action: \`dev\` or \`develop\`

   Setup using the develop flag for setup.py (links to local source)

Action: \`install\`

   Do a normal install, including mysql for Reflex Engine

Action: \`clean\`

   Cleanup the virtualenv and python pre-compiled bytecode objects.
   Doesn't do much unless there is a virtualenv.

Action: \`env\`

   Run like: source develop.sh env

   Synonymous with: source ./python/bin/activate
END
        exit 1
    ;;
esac

