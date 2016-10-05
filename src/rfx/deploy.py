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

"""
Deploy Control
"""

import os
#import subprocess
#import traceback
#import sys
import socket # gethostbyname
#import ujson as json

import rfx
from rfx.backend import Engine

############################################################################
# heuristically determint our node name
def my_instance_name():
    """docstr"""
    # os environ trumps anything else; but this is not reflected by
    # how launch control handles it...
    port = os.environ.get('PORT')
    #if not port:
        # lookup on config
        # is cfg->sensitive->parameters->PORT or cfg->setenv->PORT defined?

    name = socket.gethostname().split(".")[0]
    if port:
        name += "." + str(port)

    return name

################################################################################
class Deploy(rfx.Base):
    """
    Manage Deployment
    """
    engine = None

    ############################################################################
    def __init__(self, *args, **kwargs):
        super(Deploy, self).__init__(*args, **kwargs)
        if 'base' in kwargs: # sorry, we need the base info
            rfx.Base.__inherit__(self, kwargs['base'])
        self.engine = Engine(base=self)

    ############################################################################
    def engine_get(self, otype, oname):
        """helper function"""
        return self.engine.cache_get_object(otype, oname, notify=False)

    def engine_put(self, otype, oname, obj):
        """helper function"""
        return self.engine.cache_update_object(otype, oname, obj, notify=False)


    ############################################################################
    def target_cli(self, service, args):
        """
        if version specified, set it as the target
        otherwise print existing target
        """
        obj = self.engine_get("service", service)
        if args.version:
            # FUTURE: Check for valid Build object first
            ver = args.version.strip()

            obj['target'] = ver
            self.engine_put("service", service, obj)

            # verify and bypass cache
            obj = self.engine_get("service", service) # bypass cache

            if obj['target'] != ver:
                self.ABORT("Unable to set target version?  Bad error.")

            self.NOTIFY("set target {} = {}".format(service, args.version))
        else:
            self.OUTPUT("target {} = {}".format(service, obj.get('target')))

    ############################################################################
    # pylint: disable=unused-argument
    def status_cli(self, service, args):
        """
        report instance states
        if --verify specified, deeply query each instance to verify
        """
        obj = self.engine_get("service", service)
        target = obj.get('target', '')
        nodes = set(obj.get('instances', [])).union(obj.get('active-instances'))
        harmony = True
        self.OUTPUT("target {} = {}".format(service, target))
        for node_name in nodes:
            node = self.engine_get("instance", node_name)
            vers = node.get('version', None)
            if vers != target:
                harmony = False
            self.OUTPUT("version {} = {}".format(node.get('name'), vers))

        if not harmony:
            self.ABORT("Service is not in harmony")

    ############################################################################
    # pylint: disable=unused-argument
    def update_cli(self, service, args):
        """
        Make local version match target version and restart (if necessary)
        """
        svc = self.engine_get("service", service)
        pipe = self.engine_get("pipeline", svc.get("pipeline"))
        region = pipe.get("region", None)
        if not region:
            self.ABORT("service {} -> pipeline {} has no region defined!"
                       .format(svc, pipe))
        cfg = self.engine_get("config", "ops-deploy").get("config", {}).get(region, None)
        if not cfg:
            self.ABORT("region {} for service {} is not defined on ops-deploy!"
                       .format(region, svc))

#        node_name = svc.get('name') + '.' + hostname(base) + port
        #node = self.engine_get("instance",

        # what type?  Should only be run on server based objects
        # cfg-build should use Build object... perhaps cfg object for now
        # get path
        # boto pull object
        # down service in Engine
        # -- remove nginx
        # -- verify no traffic
        # pre-activate
        # unroll
        # post-activate
        # up service in Engine
        # -- add nginx

#        - query Engine to identify target local service should be at
#        -- if it is not matching, then make it match and update instance status
#        -- instance may be defined as 'migration' host

    ############################################################################
#    def harmonize_cli(self, service, args):
#        """
#   - pull service target
#   - pull instance definitions from server
#     instance: type=persistent / container
#     if type=persistent:
#         login and run update on each instance (pull from s3)
#         in sequence, restart (indicate first), test for healthy and update
#     if type=container:
#         launch container
#         start new container, term old after it is healthy
#
#        """
#        self.NOTIFY("Harmonize")
