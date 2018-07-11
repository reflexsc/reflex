#!/app/local/bin/virtual-python
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

import sys
import rfx.client
import dictlib
import requests.exceptions

################################################################################
def create(func, otype, odata):
    """Wrapper to handle errors"""
    try:
        sys.stdout.write("Create " + otype + "." + odata.get('name'))
        res = do(func, otype, odata)
        if res.get('status') != 'created':
            msg = res.get('message')
            print(" - " + res.get('message'))
        else:
            print("")
        return dictlib.Obj(res)

    except rfx.client.ClientError as err:
        errmsg = str(err)
        if "already exists" in errmsg or "Duplicate entry" in errmsg:
            print(" - already exists")
            return False
        else:
            print(" - error:\n       " + errmsg)
            if 'Unable to authorize session' in errmsg:
                sys.exit(0)

def do(func, *args):
    """Wrapper to handle errors"""
    try:
        return func(*args)
    except requests.exceptions.ConnectionError:
        print("Server not available at " + rcs.cfg['REFLEX_URL'])
        sys.exit(1)

