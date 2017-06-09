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
General Webhooks and simple API routing
"""

import os # for trace
import time
import sys
import traceback
import base64
import re
from rfx import json2data

###############################################################################
SERVER = None

###############################################################################
# pylint: disable=no-self-use
def trace(arg):
    """Trace log debug info"""

    with open("trace", "at") as outf:
        outf.write("[{}] {} {}\n".format(os.getpid(), time.time(), arg))

###############################################################################
def set_DEBUG(module, value): # pylint: disable=invalid-name
    """do_DEBUG() wrapper from rfx.Base object, pivoting off SERVER global"""
    if SERVER:
        if not value:
            del SERVER.debug[module]
            return True
        elif SERVER.debug.get(module, None) is None:
            SERVER.debug[module] = value
            return True
    return False

###############################################################################
def do_DEBUG(*module): # pylint: disable=invalid-name
    """do_DEBUG() wrapper from rfx.Base object, pivoting off SERVER global"""
    if SERVER:
        return SERVER.do_DEBUG(*module)
    return False

def debug(*args, **kwargs):
    """
    debug wrapper for logging
    """
    try:
        if SERVER:
            SERVER.DEBUG(*args, **kwargs)
        else:
            print("{} {}".format(args, kwargs))
    except Exception: # pylint: disable=broad-except
        with open("log_failed", "ta") as out:
            out.write("\n\n--------------------------------------------------\n\n")
            traceback.print_exc(file=out)
            out.write(str(args))
            out.write(str(kwargs))

###############################################################################
def log(*args, **kwargs):
    """
    Log key=value pairs for easier splunk processing

    test borked
    x>> log(test="this is a test", x='this') # doctest: +ELLIPSIS
    - - [...] test='this is a test' x=this
    """
    try:
        if SERVER:
            try:
                if SERVER.conf.get('requestid'):
                    # note: danger: this should be injected by traffic management,
                    # enable it with config requestid=true
                    reqid = SERVER.cherry.request.headers.get('X-Request-Id')
                    if reqid:
                        kwargs['reqid'] = reqid
                    elif SERVER.cherry.serving.request.__dict__.get('reqid'):
                        kwargs['reqid'] = SERVER.cherry.serving.request.reqid
                elif SERVER.cherry.serving.request.__dict__.get('reqid'):
                    kwargs['reqid'] = SERVER.cherry.serving.request.reqid

            except: # pylint: disable=bare-except
                SERVER.NOTIFY("Logging Error: " + traceback.format_exc())
            SERVER.NOTIFY(*args, **kwargs)
        else:
            sys.stdout.write(" ".join(args) + " ")
            for key, value in kwargs.items():
                sys.stdout.write("{}={} ".format(key, value))
            sys.stdout.write("\n")
            sys.stdout.flush()
    except Exception: # pylint: disable=broad-except
        with open("log_failed", "ta") as out:
            out.write("\n\n--------------------------------------------------\n\n")
            traceback.print_exc(file=out)
            out.write(str(args))
            out.write(str(kwargs))

###############################################################################
RX_TOK = re.compile(r'[^a-z0-9-]')
def get_jti(in_jwt):
    """
    Pull the JTI from the payload of the jwt without verifying signature.
    Dangerous, not good unless secondary verification matches.
    """
    payload_raw = in_jwt.split(".")[1]

    missing_padding = 4 - len(payload_raw) % 4
    if missing_padding:
        payload_raw += '=' * missing_padding
    try:
        data = json2data(base64.b64decode(payload_raw))
    except:
        raise ValueError("Error decoding JWT: {}".format(in_jwt))

    token_id = str(data.get('jti', ''))
    if RX_TOK.search(token_id):
        raise ValueError("Invalid User ID: {}".format(token_id))

    return token_id
