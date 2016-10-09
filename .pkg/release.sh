#!/bin/bash
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
#
# Here we are doing continuous versioning instead of semantic versioning.
# This uses a datestamp of YYMM plus a build number within that month,
# incremented and padded out.
#

if [ ! -f ../README.md ]; then
	echo "Run from reactor base/.pkg"
	exit
fi

if [ -z "$GITHUB_TOKEN" ]; then
	echo "Missing environ \$GITHUB_TOKEN"
	exit 1
fi

lastmonth=$(cat version|cut -d. -f1)
lastbuild=$(cat version|cut -d. -f2)
vmonth=$(date +%y%m)
if [[ $vmonth != $lastmonth ]]; then
	vbuild=0001
else
	vbuild=$(expr $lastbuild + 1)
fi
VERSION=$(printf "%04d.%04d" $vmonth $vbuild)
echo $VERSION > version

echo "RELEASE # $VERSION"

git add version &&
git commit -m "Release # $VERSION" &&
git push origin master

github_api="https://api.github.com/repos/reflexsc/reactor/releases?access_token=$GITHUB_TOKEN"

curl --data '{"tag_name": "'$VERSION'","target_commitish": "master","name": "'$VERSION'","body": "Release of version '"$VERSION"'","draft": false,"prerelease": false}'  $github_api

