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
	for f in bin/cored bin/reactor $(find test -type f); do
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
hosted_python=/app/python3-latest/bin/python
reqargs=''
python3=true
clean=false
core=false
action=$1
shift

for x in "$@"; do
	case $x in
		--clean)
			clean=true
			;;
		--core|-core)
			reqargs="-core $reqargs"
			core=true
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
if [ ! -f README.md -a ! -d .reactor ]; then
	echo "Run from reactor root please"
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

	$0 local|hosted [python-binary] [--core] [--clean]
	$0 revert

=> Action is \`local\` or \`hosted\`

   Setup the environment using the specified virtualenv type. Hosted virtualenv
   is used on our servers, so generally you want local.  The second argument is
   the path to use for python. 

	 * If unspecified and local, it defaults to your local environment python.
	 * If unspecified and hosted, it defaults to /app/py... like on our servers.

=> Action is \`revert\`

   If \`revert\` specified, revert the code to a state which can be committed.

=> Options:

   --core   : include building the DSE backend.  Python-3 required.  Default: no
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

if [ ! -x $pybin/pip ]; then
	do_missing "pip" "
    curl -o https://bootstrap.pypa.io/get-pip.py
    sudo $pybin/python get-pip.py
"
elif [ ! -x $pybin/virtualenv ]; then
	do_missing "virtualenv" "
    sudo $pybin/pip install virtualenv
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
$pybin/virtualenv -p $python python
echo "$python" > $origin_file

export VIRTUAL_ENV=$(pwd)/python
export PATH=${VIRTUAL_ENV}/bin:${PATH}

################################################################################
# requirements
for req in "" $reqargs; do
	if [ -f src/requirements$req.txt ]; then
		msg "Requirements from src/requirements$req.txt"
		noerr pip install -Ur src/requirements$req.txt
	fi
done

################################################################################
# import our stuff
msg "Linking modules into library path"
sitepkg=$(find python/ -name site-packages)
nested=$(echo $sitepkg |sed -e 's:[^/][^/]*:..:g')
for f in $(cat src/libs.txt); do
	echo "Linking $f"
	rm -f $sitepkg/$f
	noerr ln -s $nested/src/$f $sitepkg/$f
done

################################################################################
# reactor core specific things
if [ "$core" = true ]; then
	echo core=true > .pkg/did_core
#	msg "Installing cored (reactor core daemon)"
	owd=$(pwd)
#	cd bin
#	ln -sf ../src/core/cored cored
#	cd $owd

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
	rm -f .pkg/did_core
fi

################################################################################
do_shebang $pypath

################################################################################
# and bodger for macs
msg "For ease of use we have a special executable wrapper...  super hack time..."

if [ -x bin/virtual-python -a $new_version = false ]; then
	echo "Using existing virtual-python"
else
	noerr pip install pyinstaller 2>/dev/null 1>/dev/null
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
