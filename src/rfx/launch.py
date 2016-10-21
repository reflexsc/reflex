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
Launch Control
"""

#import tarfile
#import re
#import io
#try:
#    import StringIO as strio # pylint: disable=import-error
#except: # pylint: disable=bare-except
#    import io as strio # pylint: disable=reimported,ungrouped-imports
import os
#import sys
import traceback
#import ujson

import dictlib
import rfx
from rfx.config import ConfigProcessor
from rfx import json4human, json4store
from rfx.backend import Engine
from rfx.action import Action

################################################################################
# pylint: disable=too-many-instance-attributes
class App(rfx.Base):
    """Manage configuration files for containers"""

    launch_group = {}
    launch_service = {}
    launch_pipeline = {}
    launch_config = None
    launch_action = None
    launch_target = ''
    launch_env = {}
    launch_exec = []
    launch_rundir = ''
    launch_cfgdir = ''
    launch_type = 'exec'

    ############################################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        rfx.Base.__inherit__(self, base)
        self.dbo = Engine(base=base)

    ############################################################################
    def __repr__(self):
        return json4store(self.export()) #, indent=2)

    def export(self):
        """Return a complete object"""
        obj = {}

        if self.launch_pipeline:
            obj = dictlib.union(obj, self.launch_pipeline)
            obj['pipeline'] = obj['name']

        if self.launch_service:
            obj = dictlib.union(obj, self.launch_service)

        if self.launch_config:
            obj = dictlib.union(obj, self.launch_config) #.export())

        obj.pop('id', '')
        obj.pop('name', '')
        obj.pop('createdAt', '')
        obj.pop('updatedAt', '')

        return obj

    ############################################################################
    def launch_service_prep(self, name, commit=True):
        """Prepare a service for launching"""
        name_parts = name.split(":")
        action = None
        service = name_parts[0]
        if len(name_parts) > 1:
            action = name_parts[1]
        self.launch_service = self.dbo.get_object('service', service)
        self.launch_pipeline = self.dbo.get_object('pipeline',
                                                   self.launch_service['pipeline'])
        os.environ["APP_PIPELINE"] = self.launch_pipeline['name']
        os.environ["APP_SERVICE"] = self.launch_service['name']

        # pull cfg from service, before doing an action.. which may override it
        def setcfg(envkey, cfgkey):
            """shortcut"""
            launch = self.launch_pipeline.get('launch', {})
            if not envkey in os.environ and cfgkey in launch:
                value = self.sed_env(launch[cfgkey], {}, '')
        #        os.environ[envkey] = value # may not be necessary
                setattr(self, cfgkey, value)

        setcfg('APP_CFG_BASE', 'cfgdir')
        setcfg('APP_RUN_BASE', 'rundir')

        self.launch_type = self.launch_pipeline.get('launch', {}).get('type', 'exec')
        if action == 'none': # special case
            self._launch_prep_action(action, commit=commit)
            action = None

        elif action or self.launch_type == 'action':
            self.launch_type = 'action'
            self._launch_prep_action(action, commit=commit)

        else:
            self._launch_prep_exec(service, commit=commit)

        # for security, do not expose this to the running app
        for key in ['REFLEX_APIKEY']:
            if key in os.environ:
                del os.environ[key]
            if action and key in self.launch_action.env:
                del self.launch_action.env[key]

        return self

    ############################################################################
    # pylint: disable=unused-argument
    def _load_reflex_engine_config(self, target, commit=False):
        """get a from Reflex Engine"""
        cproc = ConfigProcessor(base=self, engine=self.dbo)
        conf = cproc.flatten(self.launch_service['config'])
        if commit:
            cproc.commit(conf, dest=self.launch_cfgdir)
        else: # normally called by commit
            cproc.prep_setenv(conf, dest=self.launch_cfgdir)
        conf = cproc.prune(conf)
        self.launch_config = conf
        return conf

    ############################################################################
    def _launch_prep_action(self, action_target, commit=True):
        """
        With launch control, local immutable package env configs take precendence
        over remotely defined (Reflex Engine) configs.
        Important: do not set secrets or environmentally unique information in
        local environment--that is only for things relevant to the local immutable
        package, such as the version of the stack required (java/node/etc).
        """

        action = Action(base=self, extcfg=self, colorize=False)

        # set APP_CFG_BASE and APP_RUN_DIR first -- these are not in
        # setenv because they must exist prior to setenv (which can have macro subs)
        local_config = action.config.get('config', {})

        # pylint: disable=unused-argument
        def get_conf(name):
            """Pull some configs based on local first, then service"""
            if name in local_config:
                return local_config[name]
            elif name in self.launch_pipeline.get('launch', {}):
                return self.launch_pipeline['launch'][name]
            raise ValueError("Unable to find '" + name + "' in local or pipeline definition")

        os.environ['APP_CFG_BASE'] = self.sed_env(get_conf('cfgdir'), {}, '')
        self.launch_cfgdir = os.environ['APP_CFG_BASE']
        os.environ['APP_RUN_BASE'] = self.sed_env(get_conf('rundir'), {}, '')
        self.launch_rundir = os.environ['APP_RUN_BASE']

        # Load Reflex Engine config, after ^^ environ changes
        self._load_reflex_engine_config(self.launch_service['config'], commit=commit)

        # load action configs.  pull Reflex Engine config expanded, merge with action config
        # and redo macro expansion
        conf = self.launch_config
        conf.setenv = dictlib.union(conf.setenv,
                                    action.config.get('setenv', {}))
        cproc = ConfigProcessor(base=self, engine=self.dbo)
        conf.setenv = cproc.macro_expand_dict(conf.setenv)
        for key, value in conf.setenv.items():
            value = self.sed_env(str(value), local_config, '')
            conf.setenv[key] = value
            os.environ[key] = value
            action.env[key] = value

        if action_target == 'none': # special case
            return self
        elif action_target:
            self.launch_target = action_target
        elif self.launch_pipeline.get('launch', {}).get('target', None):
            self.launch_target = self.launch_pipeline['launch']['target']
        else:
            raise ValueError("No launch service action target (svc:target)")

        action.verify(self.launch_target)
        self.launch_action = action

        return self

    ############################################################################
    def _launch_prep_exec(self, name, commit=True):
        launch = self.launch_pipeline.get('launch', {})
        self.launch_rundir = self.sed_env(launch.get('rundir', '.'), {}, '')
        self.launch_cfgdir = self.sed_env(launch.get('cfgdir', '.'), {}, '')
        self.launch_exec = [self.sed_env(elem, {}, '') for elem in launch.get('exec', [])]

        os.environ["APP_RUN_BASE"] = self.launch_rundir
        os.environ["APP_CFG_BASE"] = self.launch_cfgdir
        self._load_reflex_engine_config(self.launch_service['config'], commit=commit)

        return self

    ############################################################################
    def _launch_action(self, name):
        """
        Launch a service using the 'action' means, where the launch
        data is stored within the immutable package as an action.
        """

        self.launch_action.do(self.launch_target)

    ############################################################################
    def _launch_exec(self, name):
        """
        Launch a service using the 'exec' means, where the launch
        data is stored on the pipeline object.
        """
        if not os.path.isdir(self.launch_rundir):
            self.ABORT("Unable to find launch rundir (" +
                       self.launch_rundir + "), cannot launch!")
        os.chdir(self.launch_rundir)
        if not os.path.isfile(self.launch_exec[0]):
            self.NOTIFY("Unable to find launch program: " +
                        self.launch_exec[0])
        msg = "Launch Env:\n"
        for key in self.launch_config.setenv: # setenv_expanded:
            if key == 'APP_GRP_SEED':
                value = 'xxxxxx' # keep this out of logs
            else:
                value = self.launch_config.setenv[key]
            msg += "  {0}={1}\n".format(key, value)
        self.NOTIFY(msg) # this keeps it as one 'message' for splunk
        self.NOTIFY("Launch working directory:\n  " + self.launch_rundir)
        self.NOTIFY("Launch exec:\n  '" + "', '".join(self.launch_exec) + "'")

        os.execv(self.launch_exec[0], self.launch_exec)

################################################################################
class LaunchCli(App):
    """CLI interface to Launch"""

    ############################################################################
    def get_target(self, *argv):
        """
        Pull the target from either the first arg, environment, or abort.
        """
        env_service = os.environ.get("REFLEX_SERVICE")
        if argv and len(argv[0]):
            return argv[0]
        elif not env_service:
            self.ABORT("No target defined (either as argument or set as REFLEX_SERVICE)")
        else:
            return env_service

    ############################################################################
    # pylint: disable=unused-argument
    def env_cli(self, argv, args, cli):
        """
        Print bash friendly environment declarations.
        Intended to be used in a manner like:
        ```eval $(launch env name)```
        """

        name = self.get_target(*argv)
        try:
            self.launch_service_prep(name, commit=False)
            for key in sorted(self.launch_config.setenv): #_expanded:
                value = json4store(self.launch_config.setenv[key])
                self.OUTPUT('export {}={}'.format(key, value))
        except rfx.NotFoundError:
            self.ABORT(traceback.format_exc(0))

    ############################################################################
    def service_cli(self, argv, args, cli):
        """
        Launch a service, after preparing the config, replacing current
        process with new process (exec).
        Supports two types: exec and action
        """
        service = self.get_target(*argv)
        try:
            self.launch_service_prep(service, commit=True)
            for key in sorted(self.launch_config.setenv): # .items():
                value = self.launch_config.setenv[key]
                os.environ[key] = value
                self.NOTIFY("export {}={}".format(key, value))
            getattr(self, "_launch_" + self.launch_type)(service)

        except Exception as err: # pylint: disable=broad-except
            self.DEBUG(traceback.format_exc())
            self.ABORT("Unable to launch " + service + ": " + str(err))

    ############################################################################
    def config_cli(self, argv, args, cli):
        """Show just our config"""
        target = self.get_target(*argv)
        try:
            self.launch_service_prep(target, commit=args.get('--commit', False))
            self.OUTPUT(json4human(self.export()))
        except (ValueError, Exception): # pylint: disable=broad-except
            self.DEBUG(traceback.format_exc())
            self.ABORT(traceback.format_exc(0))
