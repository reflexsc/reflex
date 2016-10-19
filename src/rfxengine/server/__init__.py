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
boilerplate for CherryPy bits.
plus basic REST handlers.
"""

import logging # for testing
import re
import time
import traceback
import cherrypy
from rfxengine import log, trace, do_DEBUG
from rfxengine import exceptions
from rfxengine.db import objects as dbo

################################################################################
def secureheaders():
    """Establish secure headers"""
    headers = cherrypy.response.headers
    headers['X-Frame-Options'] = 'DENY'
    headers['X-XSS-Protection'] = '1; mode=block'
    headers['Content-Security-Policy'] = "default-src='self'"

cherrypy.tools.secureheaders = cherrypy.Tool('before_finalize', secureheaders, priority=60)

###############################################################################
class Error(Exception):
    """Return an HTTP Error"""
    pass

###############################################################################
# add object because BaseHTTPRequestHandler is an old style class
class Rest(object):
    """
    Quick and dirty REST endpoint.  Create a descendant of this class which
    defines a dictionary of endpoints, where each endpoint has a keyword
    and an rx to match for the endpoint on the URI.

    For each keyword define the relative rest_(keyword)_(CRUD) methods,
    where CRUD is one of: Create, Read, Update, Delete
    """

    exposed = True
    json_body = None
    allowed = {}

    ###########################################################################
    # pylint: disable=no-self-use
    def respond_failure(self, message, status=400):
        """Respond with a failure"""
        if status == 401:
            time.sleep(5) # Future: could add to memcache and increase logarithmically?
        if not message:
            raise Error("Failure", status)
        raise Error(message, status)

    ###########################################################################
    # pylint: disable=dangerous-default-value,unused-argument,no-self-use
    def respond(self, content, status=200):
        """Respond with normal content (or not)"""
        if not content:
            cherrypy.response.status = 204
            return
        cherrypy.response.status = status
        return content

    ###########################################################################
    # pylint: disable=invalid-name
    def _rest_crud(self, method, *args, **kwargs):
        """Called by the relevant method when content should be posted"""
        try:
            return getattr(self, method)(*args, **kwargs)
        except exceptions.PolicyFailed as err:
            if do_DEBUG('auth'):
                log("forbidden", traceback=traceback.format_exc())
            else:
                log("forbidden", traceback=traceback.format_exc(0))
            cherrypy.response.status = 403
            return {"status": "failed", "message": "Forbidden"}
        except (ValueError, dbo.InvalidParameter, Error) as err:
            cherrypy.response.status = err.args[1]
            if isinstance(err.args[0], dict):
                status = err.args[0]
                status.update({'status': 'failed'})
            else:
                status = {"status": "failed", "message": err.args[0]}
            return status
        except Exception as err:
            log("error", traceback=traceback.format_exc())
            raise

    ###########################################################################
    # could decorate these, but .. this is shorter code
    #@cherrypy.tools.accept(media='application/json')
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def POST(self, *args, **kwargs):
        """Wrapper for REST calls"""
        return self._rest_crud('rest_create', *args, **kwargs)

    @cherrypy.tools.json_out()
    def GET(self, *args, **kwargs):
        """Wrapper for REST calls"""
        return self._rest_crud('rest_read', *args, **kwargs)

    #@cherrypy.tools.accept(media='application/json')
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def PUT(self, *args, **kwargs):
        """Wrapper for REST calls"""
        return self._rest_crud('rest_update', *args, **kwargs)

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def PATCH(self, *args, **kwargs):
        """Wrapper for REST calls""" # not working w/CherryPY and json
        return self._rest_crud('rest_patch', *args, **kwargs)

    @cherrypy.tools.json_out()
    def DELETE(self, *args, **kwargs):
        """Wrapper for REST calls"""
        return self._rest_crud('rest_delete', *args, **kwargs)

    def __call__(self, *args, **kwargs):
        self.respond_failure("Not Found", status=404)
