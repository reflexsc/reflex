#!/usr/bin/env bash
#
# some parts shamelessly borrowed from NVM
#

{ # this ensures the entire script is downloaded #

msg() {
	echo >&2 "$@"
}

host_has() {
  type "$1" > /dev/null 2>&1
}

download() {
  if host_has "curl"; then
    curl -L -q $*
  elif host_has "wget"; then
    # Emulate curl with wget
    ARGS=$(echo "$*" | command sed -e 's/--progress-bar /--progress=bar /' \
                           -e 's/-L //' \
                           -e 's/-I /--server-response /' \
                           -e 's/-s /-q /' \
                           -e 's/-o /-O /' \
                           -e 's/-C - /-c /')
    wget $ARGS
  fi
}

die() {
	echo >&2 $*
	exit 1
}

#
# Detect profile file if not specified as environment variable
# (eg: PROFILE=~/.myprofile)
# The echo'ed path is guaranteed to be an existing file
# Otherwise, an empty string is returned
#
detect_profile() {
  if [ -n "$PROFILE" -a -f "$PROFILE" ]; then
    echo "$PROFILE"
    return
  fi

  local DETECTED_PROFILE
  DETECTED_PROFILE=''
  local SHELLTYPE
  SHELLTYPE="$(basename "/$SHELL")"

  if [ "$SHELLTYPE" = "bash" ]; then
    if [ -f "$HOME/.bashrc" ]; then
      DETECTED_PROFILE="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
      DETECTED_PROFILE="$HOME/.bash_profile"
    fi
  elif [ "$SHELLTYPE" = "zsh" ]; then
    DETECTED_PROFILE="$HOME/.zshrc"
  fi

  if [ -z "$DETECTED_PROFILE" ]; then
    if [ -f "$HOME/.profile" ]; then
      DETECTED_PROFILE="$HOME/.profile"
    elif [ -f "$HOME/.bashrc" ]; then
      DETECTED_PROFILE="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
      DETECTED_PROFILE="$HOME/.bash_profile"
    elif [ -f "$HOME/.zshrc" ]; then
      DETECTED_PROFILE="$HOME/.zshrc"
    fi
  fi

  if [ ! -z "$DETECTED_PROFILE" ]; then
    echo "$DETECTED_PROFILE"
  fi
}

cmd() {
	label="$1"
	shift

	if [ -n "$label" ]; then
		msg "$label"
	fi

	"$@" || {
		echo >&2 "Unable to run: $@"
		if grep -q "command 'gcc' failed" $log; then
			echo >&2 "Is your python able to compile properly?  Check $log"
			echo >&2 "Try running with: export USE_PYTHON=/path/to/functional/python/bin/python3"
		fi
		exit
	}
}

has_cmd() {
    name="$1"
	expl="$2"
	if ! host_has $name ; then
		cat <<END

--> Pre-Requisite: You need \`$name\`, try:
$expl
END
		let errs++
	fi
}

# if called from within an existing virtual env, strip it out
if [ -n "$VIRTUAL_ENV" ]; then
    p=$(echo "$PATH" | sed -e 's!'${VIRTUAL_ENV}'/bin!!;s/::/:/')
    export PATH=$p
    unset VIRTUAL_ENV
fi

errs=0
has_cmd pip "
    curl -o https://bootstrap.pypa.io/get-pip.py
    sudo $pybin/python get-pip.py
"
has_cmd virtualenv "
    sudo $pybin/pip install virtualenv
"

if [ $errs -gt 0 ]; then
	exit 1
fi

base=~/.reactor
if [ -e $base -a ! -d $base ]; then
	msg "Cannot continue: $base is not a directory"
	exit 1
fi
if [ ! -d $base ]; then
	mkdir $base
fi
cd $base
rm -rf TEMP
mkdir TEMP

gitraw=https://raw.github.com/srevenant/reactor
version=$(download -s $gitraw/master/.pkg/version)
dlurl=https://github.com/srevenant/reactor/archive/$version.tar.gz

cmd "Downloading..." download -s "$dlurl" -o reactor.src.tgz
cmd "Unrolling..." tar -C TEMP --strip 1 -xzf reactor.src.tgz
rm -f reactor.src.tgz 
VERSION=$(cat TEMP/.pkg/version)
if [ -z "$VERSION" ]; then
	echo "Unable to determine version?"
	exit 1
fi
if [ -d $VERSION ]; then
	msg "Replacing version $VERSION"
	rm -rf $VERSION
fi
mv TEMP $VERSION
rm -f current
cmd "" ln -s $VERSION current
cd current
log=$base/install.log
true > $log
QUOTES=(
  Adjusting+the+Chameleon+Circuit
  Configurating
  Transmogrifying
  Looking+for+Pokemon
  Adjusting+the+flux+inducers
)
nbr=$(($RANDOM % ${#QUOTES[@]}))
quote=$(echo "${QUOTES[$nbr]}"|sed -e 's/+/ /g')

echo "$quote..." 
cmd "(log: $log)" ./install.sh local $USE_PYTHON >> $log

profile=$(detect_profile)
sed -io -e '/#REACTOR-PATH/d' $profile
echo "export PATH=\$PATH:$base/current/bin #REACTOR-PATH" >> $profile

if [ ! -f ~/.reactor/cfg ]; then
	./bin/reactor setup wizard
fi

cd $base
version_list() { ls -1 |egrep '^[0-9][0-9][0-9][0-9].'|sort -rn; }

maxver=3
list=$(version_list)
while [ $(echo "$list" | wc -l) -gt $maxver ]; do
	name=$(echo "$list" |tail -1)
	if [ -n "$name" ]; then
		msg "Removing old version $name..."
		rm -rf $name
	fi
	list=$(version_list)
	sleep 1
done

echo ""
echo "Done installing version $VERSION"
echo ""

} # this ensures the entire script is downloaded #

