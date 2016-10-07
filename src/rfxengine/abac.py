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
Attribute Based Access Controls

Uses python 'eval' to evaluate expressions.  Generally this can be highly dangerous,
however we only accept policies from administrators.  We should still be careful.

Policies should only call locals defined by us.  Lambda's are disallowed.

Be very careful.
"""

import re
import dictlib
import cherrypy
from rfx import json4human
from rfxengine import log, server, trace
from rfxengine import exceptions

################################################################################
# classes

# pylint: disable=too-few-public-methods,too-many-instance-attributes
class Policy(object):
    """
    Generic policy object
    """
    policy_expr = None
    policy_exec = None
    policy_id = 0
    policy_type = None
    policy_name = ''
    policy_data = None
    policy_timestamp = 0
    policy_expires = 0

    disallowed_rx = re.compile("lambda ", flags=re.IGNORECASE)

    # pylint: disable=too-many-arguments
    def __init__(self, ptype, pid, pname, pexpr, pdata, ptimestamp, target_id):
        # matches array, as pulled from db.objects.RCObject._get_policy
        self.policy_type = ptype
        self.policy_id = pid
        self.policy_name = pname
        self.policy_expr = self.compile(pexpr)
        self.policy_data = pdata
        self.policy_timestamp = ptimestamp
        self.target_id = target_id

    ############################################################################
    def compile(self, expression):
        """
        Take an human readable expression and make it something python can run
        """
        if self.disallowed_rx.search(expression):
            raise exceptions.InvalidPolicy("Policy is invalid")

        expression = re.sub(r'^rx\(', 'rx.search(', expression)
        expression = re.sub(r'\s+rx\(', 'rx.search(', expression)

        return expression

    ############################################################################
    def allowed(self, attrs, raise_error=True):
        """
        process an ABAC policy expression.  Context is provided as a dict
        to add to the namespace of the expression.

        policy is always added to the context

        >>> p=Policy()
        >>> attrs=attrs_skeleton(token_nbr=1, token_name='sinatra')
        >>> p.compile("__import__('os').system('echo oops')").allowed({})
        Traceback (most recent call last):
        ...
        NameError: name '__import__' is not defined
        >>> p.compile("token_name == 'frank'").allowed(attrs)
        Traceback (most recent call last):
        ...
        exceptions.PolicyFailed: Policy failed to evaluate (token_name == 'frank')
        >>> p.compile("rx('^sin', token_name)").allowed(attrs)
        True
        >>> p.compile("token_name == 'sinatra'").allowed(attrs)
        True
        >>> p.compile("token_nbr == 0").allowed(attrs)
        Traceback (most recent call last):
        ...
        exceptions.PolicyFailed: Policy failed to evaluate (token_nbr == 0)
        >>> p.compile("token_nbr > 0").allowed(attrs)
        True
        """

        if not isinstance(attrs, dict):
            raise exceptions.InvalidContext("Context is not a dictionary")

        attrs['policy'] = self

        trace("{} {}".format(self.policy_id, self.policy_expr))
        # pylint: disable=eval-used
        try:
            if eval(self.policy_expr, {'__builtins__':{}, 'rx': re}, attrs):
                return True
        except KeyError:
            trace(json4human(attrs))
        if raise_error:
            raise exceptions.PolicyFailed("Policy failed to evaluate ({})".format(self.policy_expr))
        return False

################################################################################
def _attrs_skeleton(**kwargs):
    attrs = dictlib.Obj(
        cert_cn='',
        user_name='',
        ip='',
        token_nbr=0,
        token_name='',
        http_headers=dictlib.Obj(),
        groups=[]
    )
    if kwargs:
        attrs = dictlib.union(attrs, kwargs)
    return attrs

def attrs_skeleton(**kwargs):
    """Create a common format dictionary for attributes"""
    attrs = _attrs_skeleton(**kwargs)
    cherrypy.serving.request.login = attrs
    return attrs

MASTER_ATTRS = _attrs_skeleton(token_nbr=100, token_name='master')

################################################################################
# pylint: disable=too-few-public-methods
class AuthService(object):
    """AuthService base across all reflex engine service objects"""
    conf = None
    _slack = None
    server = None

    def __init__(self, conf, **kwargs):
        self.conf = conf
        if not kwargs.get('server'):
            raise ValueError("Missing server=x definition to init")

        self.server = kwargs.get('server')
        del kwargs['server']

        super(AuthService, self).__init__(**kwargs)

    def auth_fail(self, reason):
        """Log the reason, and report a failure neutrally"""

        def _rmatch(match):
            return "\n\t" + match.group(1)

        reason = re.sub(r'\n(\s*)', _rmatch, reason)

        log(reason, type="authfail")
        if self.server.conf.test_mode:
            raise server.Error("Unauthorized: " + reason, 401)
        else:
            raise server.Error("Unauthorized", 401)