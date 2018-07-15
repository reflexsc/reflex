# vim:set expandtab ts=4 sw=4 ai ft=python:
# vim modeline (put ":set modeline" into your ~/.vimrc)
# This is work extracted, drived or from Reflex, and is licensed using the GNU AFFERO License, Copyright Brandon Gillespie

"""
Run actions, designed for use as part of a CI/CD system, so logging is very
visual and supports colors for easier visual browsing.

Commonly we pull the config from Reflex service objects, but this is not a requirement.

Easy usage:

    >>> from action import Action
    >>> Action().run("hello", actions={
    >>>     "hello": {
    >>>         "type": "exec",
    >>>         "cmd": "echo version=%{version}"
    >>> }}, replace={
    >>>     "version": "1712.10"
    >>> })

The current actions: noop, exec, hook and http/https:

# noop: No Operation

It is there, but nothing happens.  An easy way to disable an action is to set it as noop.

# exec: Run a command

    "type": "exec",
    "template": true|false,             # optional. do template substitution (default=true)
    "cmd": "run this command"           # either are supported
    "cmd": ["run", "this", "command"]   # either are supported

Template replacement is run on cmd

Exit status not 0 is an error.

# hook: Run a webhook (simple http GET).

    "type": "hook",
    "template": true|false,   # optional. do template substitution (default=true)
    "url": "http://...",      # string replacement is performed on this
    "expect": {
        "response-codes": [200, 201, 202, 204] # this is the default
    }

Template replacement is run on url

Response code not from set is an error.  If you want more complex web calls, try type=http

# http: Run an http query

    "type": "http"           # or https
    "host": "hostname:port", # port is optional
    "template": true|false   # default is true
    "query": {
        "method": "POST",    # default if unspecified is GET
        "path": "/results",
        "headers": {         # optional
            "Content-Type": "application/json"
        },
        "content": "...."    # required for POST. if content is obj, will auto convert
    },
    "expect": {              # optional
        "response-codes": [200] # default
        "content": "string"  # optional: string must be in resulting content
        "regex": "rx"        # optional: regex must match resulting content
    },
    "timeout": 5             # seconds, default=5

Template replacement is run on the resulting URL (after it is combined),
con content (or content values if it is a dictionary) and on header values

"""

import re
import traceback
import requests
import common
import rfx
import rfx.config
import rfx.client

class Action(common.Core):
    """
    Wrap common actions.
    """

    rfxcfg = None

    ############################################################################
    def __init__(self, **kwargs):
        """init"""
        super(Action, self).__init__(**kwargs)
        base = kwargs.get("base")
        if not base:
            base = rfx.Base() # no .cfg_load() -- we don't need it
        rcs = kwargs.get('rcs')
        if not rcs:
            rcs = rfx.client.Session(base=base)

        self.rfxcfg = rfx.config.ConfigProcessor(base=base, rcs=rcs)

    ############################################################################
    def run_svc_action(self, name, replace=None, svc=None):
        """
        backwards compatible to reflex service object. This looks for hooks on
        current object as well as in the actions sub-object.
        """
        actions = svc.get('actions')
        if actions and actions.get(name):
            return self.run(name, actions=actions, replace=replace)
        if svc.get(name + "-hook"):
            return self.run(name, actions={
                name: {
                    "type": "hook",
                    "url": svc.get(name + "-hook")
                }
            }, replace=replace)
        self.die("Unable to find action {name} on service {svc}",
                 name=name, svc=svc.get('name', ''))

    ############################################################################
    def run(self, name, replace=None, actions=None):
        """
        Do an action.

        If `replace` is provided as a dictionary, do a search/replace using
        %{} templates on content of action (unique to action type)
        """
        action = actions.get(name)
        if not action:
            self.die("Action not found: {}", name)
        action['name'] = name
        action_type = action.get('type', "none")
        try:
            func = getattr(self, '_run__' + action_type)
        except AttributeError:
            self.die("Unsupported action type " + action_type)
        try:
            return func(action, replace)
        except Exception as err: # pylint: disable=broad-except
            if self._debug:
                self.debug(traceback.format_exc())
            self.die("Error running action name={} type={} error={}",
                     name, action_type, err)

    ############################################################################
    def _run__hook(self, action, replace):
        """Simple webhook"""
        url = action.get("url")
        expected = action.get("expect", {}).get("response-codes", (200, 201, 202, 204))
        if replace and action.get("template", True):
            url = self.rfxcfg.macro_expand(url, replace)
        self.logf("Action {} hook\n", action['name'])
        self.logf("{}\n", url, level=common.log_msg)
        result = requests.get(url)
        self.debug("Result={}\n", result.status_code)
        if result.status_code not in expected:
            self.die("Hook failed name={} result={}", action['name'], result.status_code)
        self.logf("Success\n", level=common.log_msg)

    ############################################################################
    def _run__noop(self, action, replace):
        """Run a command"""

        return

    ############################################################################
    def _run__exec(self, action, replace):
        """Run a command"""

        cmd = action.get('cmd')
        shell = False
        if isinstance(cmd, str):
            shell = True

        if replace and action.get("template", True):
            if shell:
                cmd = self.rfxcfg.macro_expand(cmd, replace)
            else:
                cmd = [self.rfxcfg.macro_expand(x, replace) for x in cmd]

        self.logf("Action {} exec\n", action['name'])
        self.logf("{}\n", cmd, level=common.log_cmd)
        if self.sys(cmd):
            self.logf("Success\n", level=common.log_msg)
        self.die("Failure\n")

    ############################################################################
    def _run__https(self, action, replace):
        return self._run__http(action, replace)

    def _run__http(self, action, replace):
        """More complex HTTP query."""

        query = action['query']
        url = '{type}://{host}{path}'.format(path=query['path'], **action)
        content = None
        method = query.get('method', "get").lower()
        self.debug("{} {} url={}\n", action['type'], method, url)
        if method == "post":
            content = query['content']
        headers = query.get('headers', {})

        if replace and action.get('template'):
            self.rfxcfg.macro_expand(url, replace)
            if content:
                if isinstance(content, dict):
                    for key, value in content.items():
                        content[key] = self.rfxcfg.macro_expand(value, replace)
                else:
                    content = self.rfxcfg.macro_expand(content, replace)
            for key, value in headers.items():
                headers[key] = self.rfxcfg.macro_expand(value, replace)

        self.debug("{} headers={}\n", action['type'], headers)
        self.debug("{} content={}\n", action['type'], content)

        self.logf("Action {name} {type}\n", **action)
        self.logf("{}\n", url, level=common.log_msg)
        result = getattr(requests, method)(url, headers=headers, timeout=action.get('timeout', 5))
        expect = action.get('expect', {})
        expected_codes = expect.get("response-codes", (200, 201, 202, 204))
        self.debug("{} expect codes={}\n", action['type'], expected_codes)
        self.debug("{} status={} content={}\n", action['type'], result.status_code, result.text)
        if result.status_code not in expected_codes:
            self.die("Unable to make {} call, unexpected result ({})",
                     action['type'], result.status_code)

        if 'content' in expect:
            self.debug("{} expect content={}\n", action['type'], expect['content'])
            if expect['content'] not in result.text:
                self.die("{} call to {} failed\nExpected: {}\nReceived:\n{}",
                         action['type'], url, expect['content'], result.text)


        if 'regex' in expect:
            self.debug("{} expect regex={}\n", action['type'], expect['regex'])
            if not re.search(expect['regex'], result.text):
                self.die("{} call to {} failed\nRegex: {}\nDid not match:\n{}",
                         action['type'], url, expect['regex'], result.text)

        self.logf("Success, status={}\n", result.status_code, level=common.log_msg)
        return True
