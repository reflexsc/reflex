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
Reflex Engine Client

Wrapper handling Reflex Apikey

Future: add SSL client certificates
"""

import urllib
import time
import base64
import requests
import nacl.utils
import jwt
import rfx
from rfx import json2data, json4human, json4store
from rfxengine import trace

################################################################################
class ClientError(Exception):
    """Client Error"""
    pass

################################################################################
class Session(rfx.Base):
    """Session object, which logs into Reflex Engine and manages the session"""
    session_jti = None
    session_sid = None
    session_secret = None
    session_expires = 0
    apikey_name = None
    apikey_secret = None

    def __init__(self, **kwargs):
        super(Session, self).__init__(**kwargs)
        base = kwargs.get('base')
        if base:
            rfx.Base.__inherit__(self, base)

    ############################################################################
    def _login(self, force=False):
        """Use the Apikey to get a session key"""
        if not force and self.session_expires > time.time():
            return

        if not self.apikey_name:
            name, b64_secret = (self.cfg['REFLEX_APIKEY'] + ".").split(".")[0:2]
            self.apikey_name = name
            self.apikey_secret = base64.b64decode(b64_secret)

        key_jwt = jwt.encode(dict(
            jti=self.apikey_name,
            seed=base64.b64encode(nacl.utils.random(256)).decode(),
            exp=time.time() + 300
        ), self.apikey_secret)

        result = requests.get(self.cfg['REFLEX_URL'] + "/token", headers={
            "X-Apikey": key_jwt,
            "Content-Type": "application/json"
        })

        if result.status_code == 200:
            self.DEBUG("Authorized")
            data = json2data(result.content.decode())
            self.session_sid = data['session']
            self.session_jti = data['jti']
            self.session_secret = base64.b64decode(data['secret'])
            self.session_expires = data['expires_at']

        else:
            if self.do_DEBUG():
                self.DEBUG("Failed to authorize:\n\tHTTP {}\n{}\n\n{}\n".format(
                    result.status_code,
                    json4human(dict(result.headers)),
                    json2data(result.content.decode())))

            raise ClientError("Unable to authorize session")

    ############################################################################
    def _call(self, func, target, *args, **kwargs):
        """Call Reflex Engine, wrapped with authentication and session management"""
        try:
            self._login()
        except requests.exceptions.ConnectionError:
            self.ABORT("Unable to connect to REFLEX_URL ({})".format(self.cfg['REFLEX_URL']))

        # enrich the arguments
        if not kwargs.get('headers'):
            kwargs['headers'] = {}
        if not kwargs.get('cookies'):
            kwargs['cookies'] = {}
        if not kwargs['headers'].get('Content-Type'):
            kwargs['headers']['Content-Type'] = "application/json"

        target = self.cfg['REFLEX_URL'] + "/" + target

        # make the call
        result = self._call_sub(func, target, *args, **kwargs)

        # unlikely, as self._login() should take care of this, unless our timing
        # is off from the server's, but just in case...
        if result.status_code == 401:
            self.DEBUG("Unauthorized received, Retrying Login")
            self._login(force=True)
            result = self._call_sub(func, target, *args, **kwargs)

        if result.status_code == 500:
            raise ClientError("Server side error")

        if result.status_code == 404:
            raise ClientError("Endpoint or object not found")

        if "application/json" not in result.headers.get('Content-Type', ''):
            self.DEBUG("error", result.content.decode())
            raise ClientError("Result is not valid content type")

        if result.status_code == 204:
            return {}

        if result.status_code in (200, 201, 202):
            return result.json()

        raise ClientError(result.json()['message'])

    ############################################################################
    def _call_sub(self, func, *args, **kwargs):
        """Subcall of call"""
        auth_jwt = jwt.encode(dict(
            jti=self.session_jti,
            exp=time.time() + 60
        ), self.session_secret)
        dbg = self.do_DEBUG()

        kwargs['cookies']['sid'] = self.session_sid
        kwargs['headers']['X-ApiToken'] = auth_jwt

        if dbg:
            self.DEBUG("auth", jwt=auth_jwt)
            self.DEBUG("call", func=func, args=args, kwargs=kwargs)

        return func(*args, **kwargs)

    ############################################################################
    def get(self, obj_type, obj_target):
        """session GET"""
        return self._call(requests.get, obj_type + "/" + str(obj_target))

    ############################################################################
    def list(self, obj_type, match=None, cols=None):
        """
        session LIST.  Match is a glob pattern (optional), cols is a list
        of column names
        """
        args = []
        if match:
            args.append("match=" + urllib.parse.quote(match))
        if cols:
            args.append("cols=" + ",".join(cols))

        querystr = obj_type + "/"
        if args:
            querystr += "?" + "&".join(args)
        return self._call(requests.get, querystr)

    ############################################################################
    def create(self, obj_type, obj_data):
        """session CREATE"""
        return self._call(requests.post, obj_type, data=json4store(obj_data))

    ############################################################################
    def update(self, obj_type, obj_target, obj_data):
        """session UPDATE"""
        path = obj_type + "/" + str(obj_target)
        return self._call(requests.put, path, data=json4store(obj_data))

    ############################################################################
    def patch(self, obj_type, obj_target, obj_data):
        """session PATCH"""
        path = obj_type + "/" + str(obj_target) + "?merge=true"
        return self._call(requests.put, path, data=json4store(obj_data))

    ############################################################################
    def delete(self, obj_type, obj_target):
        """session DELETE"""
        return self._call(requests.delete, obj_type + "/" + str(obj_target))
