#!/bin/bash
#$#HEADER-START
# vim:set expandtab ts=4 sw=4 ai ft=python:
#
#     Reflex Configuration Event Engine
#
#     Copyright (C) 2016 Brandon Gillespie
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU Affero General Public License as published
#     by the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public License
#     along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#$#HEADER-END

#(cd $TESTROOT/../; eval $(./install.sh env))

export BINDIR=$(cd $TESTROOT/..; pwd)/bin
#if [ -d ../bin ]; then
#    bindir=$(cd ../bin; pwd)
#elif [ -d ../../bin ]; then
#    bindir=$(cd ../../bin; pwd)
#fi

if [ "$1" = "verbose" ]; then
    verbose=1
fi

testlog=$(pwd)/test.log
testnbr=0
testbad=0
testgood=0

banner() {
    log "================================================================================"
}

ok() {
    local label=$1
    let testnbr++
    shift
    banner
    log "====== $label"
    banner
    log "...... $@"
    [ $verbose ] && printf "======"
    LAST_OUTPUT=$("$@" 2>&1)
    stat=$?
    msg="ok $testnbr $label"
    if [ $stat -gt 0 ]; then
        echo "not $msg"
        log "### not $msg"
        let testbad++
    else
        echo "$msg"
        log "### $msg"
        let testgood++
    fi
    log "$LAST_OUTPUT"
    export LAST_OUTPUT
    return $stat
}

log() {
    echo "$@" >> $testlog
    [ $verbose ] && echo "$@"
}

crud() {
    local expect=$1
    local method=$2
    local path=$3
    local content=$4
    curl_grep "$expect" -X $method  \
        -H "Accept: application/json" \
        -H "Content-type: application/json" \
        -d "$content" \
        $agent_host:$agent_port$path
    return $?
}

curl_grep() {
    local rx=$1
    shift

    curl_args="--max-time 1 -skv"
    log "### curl $curl_args $@"
    out=$(curl $curl_args "$@" 2>&1)
    curl_stat=$?
    echo "$out" >> $testlog
    log "### grep -qe \"$rx\""
    echo "$out" | grep -qe "$rx"
    grep_stat=${PIPESTATUS[1]}
    if [ $curl_stat -gt 0 -o $grep_stat -gt 0 ]; then
        log "### ERROR ($curl_stat / $grep_stat)"
        return 1
    else
        log "### SUCCESS"
    fi
    return 0
}

# pkill isn't finding this :(
kill_matching() {
    name=$1
    pids=$(ps -o pid,cmd|grep $name|grep -v grep|awk '{print $1}')
    if [ -n "$pids" ]; then
        kill $pids >/dev/null 2>&1
    fi
    trap "" 0 15
}

start_svc() {
    name="$1"
    args="$2"
    config="$3"
    echo "$config" | $BINDIR/$name $args >> $testlog 2>&1 &
    trap "kill_matching reflex-$name; exit 1" 0 15
}

waitfor_svc() {
    name=$1
    address=$2

    giveup=30
    while ! curl $address >/dev/null 2>&1; do
        log "waiting for $name to come online at $address..."
        sleep 1
        let giveup--
        if [ "$giveup" -le 0 ]; then
            echo "Aborting!  $name didn't come online!"
            exit 
        fi
    done
}

echo "output to $testlog"
rm -f $testlog


