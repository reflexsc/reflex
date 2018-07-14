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
#import uuid
import jwt
import dictlib
import rfx
from rfx import threadlock, json2data, json4human, json4store

################################################################################
class Unauthorized(Exception):
    """Client Error"""
    pass

################################################################################
class ClientError(Exception):
    """Client Error"""
    pass

################################################################################
# pylint: disable=too-many-instance-attributes
class Session(rfx.Base):
    """Session object, which logs into Reflex Engine and manages the session"""
    session_jti = None
    session_sid = None
    session_secret = None
    session_expires = 0
    apikey_name = None
    apikey_secret = None
    _cache = None
    headers = None

    def __init__(self, **kwargs):
        super(Session, self).__init__(**kwargs)
        base = kwargs.get('base')
        if base:
            rfx.Base.__inherit__(self, base)
        self._cache = dict()
        self.headers = dict()

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
            #seed=str(uuid.uuid4()), #base64.b64encode(nacl.utils.random(256)).decode(),
            seed=base64.b64encode(nacl.utils.random(256)).decode(),
            exp=time.time() + 300
        ), self.apikey_secret)

        headers = self.headers.copy()
        headers["X-Apikey"] = key_jwt
        headers["Content-Type"] = "application/json"
        result = requests.get(self.cfg['REFLEX_URL'] + "/token", headers=headers)

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

            raise Unauthorized("Unable to authorize session")

    ############################################################################
    # pylint: disable=too-many-branches
    def _call(self, func, target, *args, **kwargs):
        """Call Reflex Engine, wrapped with authentication and session management"""
        try:
            self._login()
        except Unauthorized as err:
            self.ABORT("Unauthorized: " + str(err))
        except requests.exceptions.ConnectionError:
            self.ABORT("Unable to connect to REFLEX_URL ({})".format(self.cfg['REFLEX_URL']))

        # enrich the arguments
        headers = self.headers.copy()
        if kwargs.get('headers'):
            headers = dictlib.union(headers, kwargs['headers'])
        if not kwargs.get('cookies'):
            kwargs['cookies'] = {}
        if not headers.get('Content-Type'):
            headers['Content-Type'] = "application/json"
        kwargs['headers'] = headers

        query = self.cfg['REFLEX_URL'] + "/" + target
        if self.debug.get('remote-abac'):
            if "?" in query:
                query += "&"
            else:
                query += "?"
            query += "abac=log"

        # make the call
        result = self._call_sub(func, query, *args, **kwargs)

        # unlikely, as self._login() should take care of this, unless our timing
        # is off from the server's, but just in case...
        if result.status_code == 401:
            self.DEBUG("Unauthorized received, Retrying Login")
            self._login(force=True)
            result = self._call_sub(func, query, *args, **kwargs)

        if result.status_code == 500:
            raise ClientError("Server side error")

        if result.status_code == 404:
            raise ClientError("Endpoint or object not found (" + query + ")")

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
        if self.headers:
            kwargs['headers'].update(self.headers)
        kwargs['headers']['X-ApiToken'] = auth_jwt

        if dbg:
            self.DEBUG("auth", jwt=auth_jwt)
            self.DEBUG("call", func=func, args=args, kwargs=kwargs)

        return func(*args, **kwargs)

    ############################################################################
    def get(self, obj_type, obj_target, archive=False):
        """session GET"""
        args = []
        if archive:
            args.append("archive=" + str(archive['start']))
        return self._call(requests.get,
                          obj_type + "/" + str(obj_target) + "?" + "&".join(args))

    ############################################################################
    # pylint: disable=too-many-arguments
    def list(self, obj_type, match=None, cols=None, raise_error=True, archive=False):
        """
        session LIST.  Match is a glob pattern (optional), cols is a list
        of column names
        """
        args = []
        if match:
            try: # stupid python2
                args.append("match=" + urllib.parse.quote(match))
            except: # pylint: disable=bare-except, no-member
                args.append("match=" + urllib.pathname2url(match))
        if archive:
            args.append("archive=" +
                        str(archive['start']) + "~" +
                        str(archive['end']))
        if cols:
            args.append("cols=" + ",".join(cols))

        querystr = obj_type + "/"
        if args:
            querystr += "?" + "&".join(args)
        if raise_error:
            return self._call(requests.get, querystr)
        else:
            try:
                return self._call(requests.get, querystr)
            except: # pylint: disable=bare-except
                return list()

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
    def instance_ping(self, obj_target, obj_data):
        """special instance update ping"""
        path = "instance-ping/" + str(obj_target)
        return self._call(requests.put, path, data=json4store(obj_data))

    ############################################################################
    def delete(self, obj_type, obj_target):
        """session DELETE"""
        return self._call(requests.delete, obj_type + "/" + str(obj_target))

    ############################################################################
    @threadlock
    def cache_get(self, obj_type, obj_target, **kwargs):
        """
        Cache wrapper around .get()
        """
        if obj_type in self._cache:
            if obj_target in self._cache[obj_type]:
                return self._cache[obj_type][obj_target]
        else:
            self._cache[obj_type] = dict()
        try:
            obj = self.get(obj_type, obj_target, **kwargs)
        except ClientError:
            self._cache[obj_type][obj_target] = None
            raise
        self._cache[obj_type][obj_target] = obj
        return obj

    ############################################################################
    @threadlock
    def cache_update(self, obj_type, obj_target, payload, **kwargs):
        """
        Cache wrapper around .update()
        """
        value = self.update(obj_type, obj_target, payload, **kwargs)
        if value:
            if obj_type in self._cache:
                if obj_target in self._cache[obj_type]:
                    del self._cache[obj_type][obj_target]
        return value

    ###########################################################################
    @threadlock
    def cache_list(self, obj_type, **kwargs):
        """
        Cache wrapper around .list()
        """
        if not obj_type in self._cache:
            self._cache[obj_type] = {}

        objs = self.list(obj_type, **kwargs)
        for obj in objs:
            oname = obj.get('name')
            if oname:
                self._cache[obj_type][oname] = obj

        return objs

    ###########################################################################
    @threadlock
    def cache_reset(self):
        """Clear the cache"""
        self._cache = dict()

    ###########################################################################
    def cache_drop(self, obj_type, obj_target):
        """Drop an object from the cache, if it exists"""
        if obj_type in self._cache and obj_target in self._cache[obj_type]:
            del self._cache[obj_type][obj_target]
