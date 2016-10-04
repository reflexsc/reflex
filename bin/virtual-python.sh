#!/bin/bash
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

# wrapper for virtual python in "local" folder

base=$(dirname $1)
path=

# look up one or two levels
for d in .. ../..; do
	if [ -x $base/$d/python/bin/python ]; then
		base=$(cd $base/$d/python; /bin/pwd)
		export VIRTUAL_ENV=$base
		export PATH=$VIRTUAL_ENV/bin:$PATH
		exec $VIRTUAL_ENV/bin/python "$@"
	fi
done

echo "Unable to find virtual python"
exit 1

