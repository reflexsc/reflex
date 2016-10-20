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
import requests.exceptions

rcs = rfx.client.Session()
rcs.cfg_load()

def create(func, otype, odata):
    try:
        print("Create " + otype + "." + odata.get('name'))
        res = func(otype, odata)

    except rfx.client.ClientError as err:
        if "already exists" in str(err):
            print(" => " + str(err))
            return False
        raise

    except requests.exceptions.ConnectionError:
        print("Server not available at " + rcs.cfg['REFLEX_URL'])
        sys.exit(1)

    return res

res = create(rcs.create, "group", {
	"name":"admins",
	"group":["master"],
    "type":"Apikey"
})

res = create(rcs.create, "group", {
	"name":"developers",
	"group":["master"],
    "type":"Apikey"
})

res = create(rcs.create, "policy", {
    "name": "admins-all-access",
    "policy": "token_name in groups.admins"
})

res = create(rcs.create, "policy", {
    "name": "developers-read-not-sensitive",
    "policy": "token_name in groups.developers and sensitive == False"
})

res = create(rcs.create, "policy", {
    "name": "developers-sensitive",
    "policy": "token_name in groups.developers and sensitive == True"
})

res = create(rcs.create, "policyscope", {
	"name": "pond-read-configs",
	"policy_id": 101,
	"actions": 'read',
	"type": 'global',
	"matches": 'obj_type == "Config"'
})

