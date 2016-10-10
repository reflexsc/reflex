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

errs=0
has_cmd docker-compose "

	https://docs.docker.com/compose/install/

"
if [ $errs -gt 0 ]; then
	exit 1
fi

file=reflex-engine-demo.yml
gitraw=https://raw.github.com/reflexsc/reflex
dlurl=$gitraw/master/.pkg/$file

cmd "Pulling Docker Compose file as $file..." download -s -O "$dlurl"
cmd "Starting Engines: docker-compose -f $file up -d"
docker-compose -f $file up -d
APIKEY=
echo "Waiting for engine to come online..."
while [ -z "$APIKEY" ]; do
	APIKEY=$(docker-compose logs | grep REFLEX_APIKEY |sed -e 's/^.*REFLEX_APIKEY=//')
	sleep 1
    echo -n "."
done

echo ""
echo "Available, use APIKEY:"
echo ""
echo "    export REFLEX_APIKEY=$APIKEY"
echo ""

} # this ensures the entire script is downloaded #

