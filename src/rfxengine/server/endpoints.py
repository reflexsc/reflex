#!/app/local/bin/virtual-python
# vim modeline (put ":set modeline" into your ~/.vimrc)
# vim:set expandtab ts=4 sw=4 ai ft=python:
# pylint: disable=superfluous-parens

"""
Endpoints for Reflex Engine API
"""

import time
import base64
import traceback
import cherrypy
import dictlib
import jwt
from rfx import json2data#, json4human#, json4store #, json2data
from rfxengine import log, get_jti, trace
from rfxengine import server # pylint: disable=cyclic-import
from rfxengine import abac
from rfxengine.db import objects as dbo

################################################################################
def get_json_body():
    """Helper to get JSON content"""
    try:
        body = cherrypy.request.json
    except AttributeError:
        try:
            body = cherrypy.request.body.read()
        except TypeError:
            raise server.Error("Unable to load JSON content", 400)

    if isinstance(body, str): # or isinstance(body, unicode):
        return json2data(body)
    return body

################################################################################
class Attributes(abac.AuthService): # gives us self.auth_fail
    """
    ABAC Attributes class

    Object is stateless, using data on the stack only

    Defined attributes (each are undefined if not authorized)

        cert_cn      <string> -- client certificate via SSL
        user_name    <string> -- HTTP Basic auth user
        ip           <string> -- client IP
        token_nbr    <int>    -- user auth token number
        token_name   <string> -- user auth token name
        http_headers <obj>    -- object of http headers (lowercased, s/-/_/)
        groups      <array of strings>
    """

    ############################################################################
    def abac_gather(self):
        """Gather attributes"""

        attrs = abac.attrs_skeleton()

        # future: figure out a plugin framework for this
        self._abac_token(attrs)
        self._user_login(attrs)
        self._client_cert(attrs)
        self._origin_addr(attrs)
        self._http_headers(attrs)
        self._groups(attrs)

        return attrs

    ############################################################################
    # pylint: disable=no-self-use, unused-argument
    def _http_headers(self, attrs):
        """http headers as dictobj"""

        # Note: Lowercase keys option to dictlib?
        attrs['http_headers'] = dictlib.Obj(cherrypy.request.headers)

    def _user_login(self, attrs):
        """conventional user login"""

        return

    ############################################################################
    # pylint: disable=no-self-use, unused-argument
    def _client_cert(self, attrs):
        """Pull TLS Client certificate information from headers"""
        return

    ############################################################################
    # pylint: disable=no-self-use
    def _origin_addr(self, attrs):
        """Pull the originating IP address"""

        if cherrypy.request.headers.get('X-Forwarded-For'):
            attrs['ip'] = cherrypy.request.headers['X-Forwarded-For']
        else:
            attrs['ip'] = cherrypy.request.remote.ip

        return

    ############################################################################
    # pylint: disable=no-self-use,duplicate-code
    def _abac_token(self, attrs):
        """Pull in an Api Token (requires prior apikey session creatino)"""
        if 'X-ApiToken' not in cherrypy.request.headers:
            return

        try:
            jwt_token = cherrypy.request.headers['X-ApiToken']
            token_name = get_jti(jwt_token)
            session = cherrypy.request.cookie.get('sid', None)
            if not session:
                self.auth_fail("No session defined")
            session_id = session.value
            auth_session = dbo.AuthSession(master=self.server.dbm)
            # pylint: disable=redefined-variable-type
            auth_session = auth_session.get_session(token_name, session_id)
            if not auth_session:
                self.auth_fail("Session not found")

            try:
                jwt_data = jwt.decode(jwt_token, auth_session.obj['secret_raw']) # pylint: disable=no-member
            except jwt.DecodeError: # pylint: disable=no-member
                self.auth_fail("JWT cannot be decoded properly")
            except jwt.ExpiredSignatureError: # pylint: disable=no-member
                self.auth_fail("JWT expired")

            if not jwt_data:
                self.auth_fail("JWT cannot be decoded")
            if not jwt_data.get('exp'):
                self.auth_fail("JWT missing expiration")

            if time.time() - jwt_data['exp'] > 60:
                self.auth_fail("JWT bad expiration (too great)")

            # nbr instead of id, to avoid confusion with token_uuid
            attrs['token_nbr'] = auth_session.obj['data']['token_id'] # pylint: disable=no-member
            attrs['token_name'] = auth_session.obj['data']['token_name'] # pylint: disable=no-member
#            log("abac", token=attrs.token_nbr)

        except: # pylint: disable=bare-except
            if self.server.do_DEBUG():
                self.server.DEBUG(traceback.format_exc())
            self.auth_fail(traceback.format_exc(0))

    ############################################################################
    # pylint: disable=no-self-use, unused-argument
    def _groups(self, attrs):
        """http headers as dictobj"""

        attrs['groups'] = dbo.Group(master=self.server.dbm).get_for_attrs()

################################################################################
class Health(server.Rest, abac.AuthService):
    """
    Health check
    """
    last_stat = None

    # pylint: disable=unused-argument
    def rest_read(self, *args, **kwargs):
        """Health Check"""

        # check stats -- should be incrementing
        stat = self.server.stat.copy()

        errs = []
        detail = {}
        if kwargs.get('detail') == 'true':
            detail = {
                'last-heartbeat': 0,
            }

        if stat.heartbeat.last:
            if stat.heartbeat.last + self.server.conf.heartbeat < time.time():
                errs.append("Have not heard a heartbeat")
            if detail:
                detail['last-heartbeat'] = stat.heartbeat.last

        # keep a static copy of the last run stats
        self.last_stat = stat

        # xODO: check db connection health
        if errs:
            return self.respond_failure(detail, status=503)

        return self.respond(detail)

################################################################################
class Token(server.Rest, abac.AuthService):
    """
    Session Tokens for Apikeys
    """
    last_stat = None

    # pylint: disable=unused-argument,duplicate-code
    def rest_read(self, *args, **kwargs):
        """Receive an Apikey and give a Session Token"""

        # authorize
        if 'X-ApiKey' not in cherrypy.request.headers:
            return self.respond_failure("Unauthorized", status=401)

        try:
            jwt_apikey = cherrypy.request.headers['X-ApiKey']
            token_id = get_jti(jwt_apikey)
            try:
                token = dbo.Apikey(master=self.server.dbm).get(token_id, True)
            except dbo.ObjectNotFound:
                return self.auth_fail("Apikey not found")

            # validate base on array of secrets
            jwt_data = None
            for secret in token.obj.get('secrets', []):
                try:
                    # pylint: disable=no-member
                    jwt_data = jwt.decode(jwt_apikey, base64.b64decode(secret))
                except jwt.exceptions.DecodeError:
                    continue
                except jwt.exceptions.ExpiredSignatureError: # pylint: disable=no-member
                    self.auth_fail("JWT expired")

            if not jwt_data:
                self.auth_fail("JWT cannot be decoded")
            if not jwt_data.get('exp'):
                self.auth_fail("JWT missing expiration")

            # 256 base64 != 256 bytes, but this is close enough to tell; save a few cycles
            if not jwt_data.get('seed') or len(jwt_data.get('seed')) < 256:
                self.auth_fail("JWT seed missing")

            if time.time() - jwt_data['exp'] > self.server.conf.auth.expires:
                self.auth_fail("JWT bad expiration (too great)")

            # the signature matches, it is good
            expires_at = time.time() + self.server.conf.auth.expires

            auth_session = dbo.AuthSession(master=self.server.dbm)
            auth_session.new_session(token, expires_at, {
                'token_id': token.obj['id'],
                'token_name': token.obj['name']
            })

            cookie = cherrypy.response.cookie
            cookie['sid'] = auth_session.obj['session_id']
            cookie['sid']['path'] = self.server.conf.server.route_base
            cookie['sid']['max-age'] = self.server.conf.auth.expires or 300
            cookie['sid']['version'] = 1
            log("login", apikey=token.obj['id'])

        except Exception as err: # pylint: disable=broad-except
            if self.server.do_DEBUG():
                self.server.DEBUG(traceback.format_exc())
            self.auth_fail(str(err)) # traceback.format_exc(0))

        return self.respond({
            "status": "success",
            "session": auth_session.obj['session_id'],
            "secret": auth_session.obj['secret_encoded'],
            "jti": auth_session.obj['token_id'],
            "expires_at": expires_at
        })

################################################################################
class Object(server.Rest, Attributes):
    """
    General object interface
    """
    obj = ''

    def __init__(self, *args, **kwargs):
        self.obj = getattr(dbo, kwargs['obj'].capitalize())
        del(kwargs['obj'])
        super(Object, self).__init__(*args, **kwargs)

    ############################################################################
    # pylint: disable=unused-argument
    def rest_read(self, *args, **kwargs):
        """
        read
        """
        attrs = self.abac_gather()
        if not attrs.token_nbr: # check policy instead
            self.auth_fail("Unauthorized")

        obj = self.obj(master=self.server.dbm)
        if not args:
            if kwargs.get('cols'):
                cols = list(set(kwargs['cols'].split(',')))
                errs = []
                if errs:
                    raise server.Error(",".join(errs), 400)
            # todo: sanitize match
                data = obj.list_cols(attrs, cols, match=kwargs.get('match'))
            else:
                data = obj.list_buffered(attrs, match=kwargs.get('match'))
        else:
            target = args[0]
            try:
                data = obj.get(target, attrs).dump()
            except dbo.ObjectNotFound as err:
                self.respond_failure({"status":"failed", "message": str(err)}, status=404)
            except dbo.InvalidParameter as err:
                self.respond_failure({"status":"failed", "message": str(err)}, status=400)

        return self.respond(data, status=200)

    ############################################################################
    # pylint: disable=unused-argument
    def rest_create(self, *args, **kwargs):
        """
        create
        """
        attrs = self.abac_gather()
        if not attrs.token_nbr: # check policy instead
            self.auth_fail("Unauthorized")

        body = get_json_body()
        try:
            obj = self.obj(master=self.server.dbm)
            obj.load(body)
            warnings = obj.create(attrs)
        except (dbo.ObjectExists, dbo.InvalidParameter) as err:
            self.respond_failure({"status":"failed", "message": str(err)}, status=400)
        if warnings:
            return self.respond({"status":"created", "warning":"; ".join(warnings)},
                                status=201)
        return self.respond({"status":"created"}, status=201)

    ############################################################################
    # pylint: disable=unused-argument
    def rest_update(self, *args, **kwargs):
        """
        update
        """
        attrs = self.abac_gather()
        if not attrs.token_nbr: # check policy instead
            self.auth_fail("Unauthorized")

        if not args:
            return self.respond({"status":"failed"}, status=404)

        body = get_json_body()
        target = args[0]
        if target and target[:1] in "0123456789":
            body['id'] = int(target)
        else:
            body['name'] = target
        obj = self.obj(master=self.server.dbm)
        try:
            # would prefer to do PATCH, but tired of fighting w/CherryPy
            if 'merge' in kwargs and kwargs['merge'].lower() == 'true':
                data = obj.get(target, attrs).dump()
                obj.load(dictlib.union(data, body))
            else:
                obj.load(body)
            warnings = obj.update(attrs)
        except dbo.ObjectNotFound as err:
            self.respond_failure({"status":"failed", "message": str(err)}, status=404)
        except dbo.InvalidParameter as err:
            self.respond_failure({"status":"failed", "message": str(err)}, status=400)
        except dbo.NoChanges as err:
            self.respond_failure({"status":"unknown", "message": str(err)}, status=202)
        if warnings:
            return self.respond({"status":"updated", "warning":"; ".join(warnings)},
                                status=201)
        return self.respond({"status":"updated"}, status=201)

    ############################################################################
    # pylint: disable=unused-argument
    def rest_delete(self, *args, **kwargs):
        """
        delete an object
        """
        attrs = self.abac_gather()
        if not attrs.token_nbr: # check policy instead
            self.auth_fail("Unauthorized")
        if not args:
            return self.respond({'status': 'failed'}, status=404)
        target = args[0]
        try:
            if self.obj(master=self.server.dbm).delete(target, attrs):
                return self.respond({'status': 'deleted'}, status=200)
            return self.respond({'status': 'failed'}, status=401)
        except dbo.ObjectNotFound as err:
            self.respond_failure({'status': 'failed', "message": str(err)}, status=404)
