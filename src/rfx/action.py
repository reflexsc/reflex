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

import os
import traceback
import subprocess
import sys
import ujson as json
#import boto3
#from boto3.s3.transfer import S3Transfer
import rfx
from rfx import json4store
from rfx.backend import Engine

################################################################################
# evaluate to fully qualified exe, incase env={} and default PATH
def get_executable(exe, path=None): # allow path override
    """logic wrapper to simplify finding execuable"""
    if exe[0][:1] != "/" and exe[0][:2] != "./":
        if path is None:
            path = str(os.environ.get('PATH', ''))
        for part in path.split(os.pathsep):
            fqpath = os.path.join(part, exe[0])
            if os.access(fqpath, os.X_OK):
                exe[0] = fqpath
                return exe
    elif os.access(exe[0], os.X_OK):
        return exe
    raise ValueError("Cannot find executable: {}".format(exe[0]))

################################################################################
# pylint: disable=too-many-instance-attributes
class Action(rfx.Base):
    """
    Do actions, based on data stored in the immutable container
    """

    did = {} # to avoid recursion
    stack = []
    action_fqpath = '.'
    action_dir = ''
    action_file = ''
    action_dirs = ['.pkg/', '.reflex/', '.rfx', '.']
    config_files = ['config.json', 'actions.json']
    config = {}
    dbo = None
    extcfg = None
    s3cfg = None
    env = None

    ############################################################################
    def __init__(self, *args, **kwargs):
        super(Action, self).__init__(*args, **kwargs)
        if 'base' in kwargs: # sorry, we need the base info
            rfx.Base.__inherit__(self, kwargs['base'])

        # so any forked children don't inherit...
        if 'VIRTUAL_ENVIRON' in os.environ:
            virt = os.environ['VIRTUAL_ENVIRON'] + "/bin"
            newpath = []
            for elem in os.environ['PATH'].split(":"):
                if virt != elem:
                    newpath.append(elem)
            os.environ['PATH'] = ":".join(newpath)
            del os.environ['VIRTUAL_ENVIRON']

        self.get_terminal_width()
        self.env = os.environ.copy() # start with our os environ
        if 'PYTHONPATH' in self.env:
            del self.env['PYTHONPATH']

        self.default_environ('APP_RUN_BASE', os.getcwd())
        self.default_environ('APP_RUN_ROOT', self.env['APP_RUN_BASE'])
        self.default_environ('APP_CFG_BASE', os.getcwd())

        self.load_config()
        if 'test' not in self.debug:
            self.colorize = kwargs.get('colorize', True)
            try:
                self.get_my_nameip()
            except: # pylint: disable=bare-except
                pass

        if 'extcfg' in kwargs:
            self.extcfg = kwargs['extcfg']

    ############################################################################
    def default_environ(self, key, default):
        """set an action environ equal to os.environ(key) or the default value"""
        if key in os.environ:
            self.env[key] = os.environ[key]
        else:
            self.env[key] = default

    ############################################################################
    def load_config(self):
        """
        Load in the actions config file.

        Search path:

            ENV[APP_RUN_ROOT]/.pkg/actions.json
            (cwd)/.pkg/actions.json
        """

        paths = [os.getcwd()]
        env_root = self.env.get('APP_RUN_ROOT', '')
        if env_root:
            paths = [env_root + "/"] + paths

        def get_path():
            """Get path as a function so we can return once we find it"""

            for base_path in paths:
                for action_dir in self.action_dirs:
                    for conf_name in self.config_files:
                        base = base_path + "/" + action_dir + "/" + conf_name
                        if os.path.exists(base):
                            os.chdir(base_path)
                            path = base
                            self.action_fqpath = base_path + "/" + action_dir
                            self.action_dir = action_dir
                            self.action_file = conf_name
                            return path
            return None

        path = get_path()

        if not path:
            self.ABORT("No Reflex Actions configured, missing {}actions.json"
                       .format(self.action_dirs[0]))

        try:
            with open(path) as infile:
                self.config = json.load(infile)

        # pylint: disable=broad-except
        except Exception as err:
            self.DEBUG(traceback.format_exc())
            self.ABORT("Unable to load actions.json: {}".format(err))

    ############################################################################
    # pylint: disable=unused-argument
    def verify(self, target, export_meta=False):
        """
        Start at 'action' and walk the configs, verifying it is good.
        """
        stack = " -> ".join(self.stack)
        if target in self.did:
            self.ABORT("Recursive action calls back in on itself! (" + stack + ")")

        actions = self.config['actions']

        if target not in actions:
            self.ABORT("Cannot find target action '" + target + "' (" + stack + ")")

        if 'type' not in actions[target]:
            self.ABORT("Cannot find action type for '" + target + "' (" + stack + ")")

        if actions[target]['type'] == 'run':
            if not os.path.exists(self.action_fqpath + "/" + actions[target]['target']):
                self.ABORT("Cannot find action script {} for '{}' ({})"
                           .format(self.action_fqpath + "/" + actions[target]['target'],
                                   target, stack))

        self.did[target] = True
        self.stack.append(target)
        self.DEBUG("Verified " + target)

        if 'onSuccess' in actions[target]:
            self.verify(actions[target]['onSuccess'])

    ############################################################################
    def _do(self, target, opts=None):
        """
        do an action (recursive)

        Assert: self.verify should already have been run, so we can assume no
        recursion and our data structures are good.
        """

        action = self.config['actions'][target]

        label = action.get('title', target)

        self.banner("Action '" + label + "'")

        env = self._do_setenv(action)

        # could also do exec
        if action['type'] == 'script':
            if 'target' in action:
                path = self.action_fqpath + '/' + action['target']
            else:
                path = self.action_fqpath + '/' + target
            cmd = [path]
            if opts:
                cmd += opts
            return self._do__cmd(target, action, cmd, env=env)
        elif action['type'] == 'system':
            # backwards compatible, deprecated use of "cmd" vs "cmd"
            cmd = action.get("exec", None)
            if not cmd:
                cmd = action.get("cmd")
            if opts:
                cmd += opts
            return self._do__cmd(target, action, cmd, env=env)
        elif action['type'] == 'exec':
            cmd = action.get("cmd")
            if opts:
                cmd += opts
            return self._do__cmd(target, action, cmd, env=env, doexec=True)
        else:
            try:
                func = getattr(self, '_do__' + action['type'].replace('-', '_'))
            except AttributeError:
                self.NOTIFY("Ignoring unrecognized action: " + action['type'])
                return False
            return func(target, action)

    ############################################################################
    def _do__docker_service(self, target, action):
        stype = action.get("system", "systemctl")
        sysaction = action.get("action", "start")
        if stype == "systemctl":
            cmd = ["sudo", "/bin/systemctl", sysaction, "docker"]
        else:
            self.NOTIFY("Ignoring unrecognized system type (systemctl)")
            return False

        return self._do__cmd(target, action, cmd, echo=True)

    def _do__docker_build(self, target, action):
        """
        Store a package somewhere. Currently only supports s3.

        Run after roll_package.  Assumes LAST_PKG_FILENAME is in environ setting.
        """

        args = []
        if self.env.get("NO_DOCKER_CACHE"):
            args = ["--no-cache"]
        version = self.env.get('PKG_VERSION')
        if version:
            version = ":" + version
        image = action.get("image")
        if not image:
            self.NOTIFY("No image name defined!")
            return False
        image = self.sed_env(image, {}, '', env=self.env)
        tags = list()
        for tag in action.get('tags', []):
            tags.append("-t")
            tags.append(image + ":" + tag)

        dockerfile = action.get("dockerfile")
        if not dockerfile:
            if os.path.exists("Dockerfile"):
                dockerfile = "Dockerfile"
            elif os.path.exists(self.action_dir + "/Dockerfile"):
                dockerfile = self.action_dir + "/Dockerfile"

        if not os.path.exists(dockerfile):
            self.ABORT("Unable to fid a Dockerfile")

        return self._do__cmd(target, action,
                             ["docker", "build"] + args +
                             ["-t", image + version] +
                             tags +
                             ["-f", dockerfile, "."], echo=True)

    def _do__docker_push(self, target, action):
        """
        Store a package somewhere. Currently only supports s3.

        Run after roll_package.  Assumes LAST_PKG_FILENAME is in environ setting.
        """

        image = action.get("image")
        if not image:
            self.NOTIFY("No image name defined!")
            return False

        cmd = ["docker", "push", image]
        return self._do__cmd(target, action, cmd, echo=True)

    ############################################################################
    def _do__store_package_s3(self, target, action):
        """
        Store a package somewhere. Currently only supports s3.

        Run after roll_package.  Assumes LAST_PKG_FILENAME is in environ setting.
        """

        fname = self.env.get('LAST_PKG_FILENAME', 'unknown-file')
        try:
            repo = self.config['config']['package-repo']
        except AttributeError:
            self.ABORT("Cannot find 'package-repo' in actions.json->config")

        self.NOTIFY("Storing " + fname + " to " + repo)
        return self._do__cmd(target, action, [
            "s3cmd", "--no-mime-magic", "--no-progress", "--quiet", "put", fname, repo + '/' + fname
        ], env={})

        # more work w/S3 to polish this
#        if repo[:5] == 's3://':
#            repo = repo[5:]
#        s3 = S3Transfer(boto3.client('s3'))
#        response = s3.upload_file(fname, repo, fname)
#        print(response)

    ############################################################################
    def _do__roll_package(self, target, action):
        """
        Based on provided include/exclude files, create package.
        """

        try:
            name = "deploy_stage." + \
                   self.env.get("REFLEX_PRODUCT", 'NO_REFLEX_PRODUCT') + "_" + \
                   self.env.get("REFLEX_MODULE", 'NO_REFLEX_MODULE')
            include = self.action_fqpath + action['include']
        except KeyError as err:
            self.ABORT("Unable to roll package '{}', missing attribute: {}".format(target, err))

        try:
            pkgfile = name + '.' + self.env.get('PKG_VERSION', 'NO_PKG_VERSION') + '.tgz'
        except KeyError as err:
            self.ABORT("Unable to roll package '{}', missing setenv: {}".format(target, err))

        cmd = self._sh("rm -f " + name + "*.tgz")
        if cmd['code'] > 0:
            self.OUTPUT(cmd['out'])

        if not os.path.exists(include):
            self.ABORT("Cannot find include file: " + include)

        exc = ["tar", "-cvzf", pkgfile]

        if action.get('chdir', False):
            if os.path.exists(action['chdir']):
                exc += ["--directory=" + action['chdir']]

        # order matters -- must come after chdir
        exc += ["--files-from=" + include]

        if action.get('gitignore', False):
            if os.path.exists(".gitignore"):
                exc += ["--exclude-from=" + os.getcwd() + "/.gitignore"]

        if action.get('exclude', None):
            exc += ["--exclude-from=" + self.action_fqpath + action['exclude']]

        cmd = self._run(exc)
        if cmd['code'] > 0:
            self.ABORT("Cannot continue:\n" + cmd['out'])

        cmd = self._run(['gzip', '-t', pkgfile])
        if cmd['code'] > 0:
            self.ABORT("Unable to verify package:\n" + cmd['out'])

        # change the default environ
        self.env['LAST_PKG_FILENAME'] = pkgfile

        return self._do_success(target, action)

    ############################################################################
    def _sh(self, cmd, spliterr=False):
        """
        Wrap Popen with common usable features.  Similar to 'check_output' but
        the way I want it.

        >>> self = Action()
        >>> import json
        >>> json.dumps(self._sh("echo ohhi"), sort_keys=True)
        '{"code": 0, "err": "", "out": "ohhi\\\\n"}'
        """
        return self._run(cmd, spliterr=spliterr, shell=True)

    ############################################################################
    def _run(self, *args, **kwargs): #spliterr=False):
        """
        Wrap Popen with common usable features.  Similar to 'check_output' but
        the way I want it.

        Output from python 2.7: {'code': 0, 'err': '', 'out': 'hi\\n'}

        >>> self = Action()
        >>> import json
        >>> json.dumps(self._run(["echo", "hi"]), sort_keys=True)
        '{"code": 0, "err": "", "out": "hi\\\\n"}'
        """
        spliterr = kwargs.get('spliterr', False)
        if spliterr:
            stderr = subprocess.PIPE
        else:
            stderr = subprocess.STDOUT
        shell = False
        if 'shell' in kwargs:
            shell = kwargs['shell']

        exc = list(args)[0]
        self.DEBUG("run: {}".format(exc))
        self.notifyfd.flush()
        self.outputfd.flush()
        sub = subprocess.Popen(exc, shell=shell, stdout=subprocess.PIPE,
                               stderr=stderr, env=self.env)
        output, outerr = sub.communicate()
        if isinstance(output, bytes): # grr python 2/3
            output = output.decode()
        if isinstance(outerr, bytes): # grr python 2/3
            outerr = outerr.decode()

        return {'code': sub.returncode, 'out': output, 'err': outerr or ''}

    ############################################################################
    def _do__group(self, ignore, action):
        """
        Run a group of actions in sequence.
        """

        for target in action.get('actions', []):
            self.do(target)

    ################################################################################
    # evaluate to fully qualified exe, incase env={} and default PATH
    def _executable(self, exe):
        return get_executable(exe, path=self.env.get('PATH', None))

    ############################################################################
    # pylint: disable=too-many-arguments,dangerous-default-value,too-many-branches,inconsistent-return-statements
    def _do__cmd(self, target, action, exc, env=dict(), echo=False, doexec=False):
        """
        Execute a sub process as part of a action, and handle the subsequent step

        if env=False, do not use the reflex environ settings, just the default os
        """

        if not env:
            env = self.env

        # convert any ${vars} in exe list
        env_exc = []
        for elem in exc:
            env_exc.append(self.sed_env(elem, {}, '', env=env))

        # pull first arg from env{PATH}
        fqexc = self._executable(env_exc)
        if not fqexc:
            raise ValueError("Cannot execute: {}".format(exc[0]))

        extcfg = ''
        if action.get('config', "n/a") == "stdin" and self.extcfg:
            if isinstance(self.extcfg, str):
                extcfg = json4store(self.extcfg)
            else: # the App object exports a dictionary
                extcfg = json4store(self.extcfg.export())

        if echo:
            formatted = []
            for arg in fqexc:
                if ' ' in arg:
                    formatted.append(json.dumps(arg))
                else:
                    formatted.append(arg)
            self.NOTIFY("Execute:\n\n\t" + " ".join(formatted) + "\n")

        self.DEBUG("command: " + str(fqexc))
        self.notifyfd.flush()
        self.outputfd.flush()
        if doexec:
            # pylint: disable=inconsistent-return-statements
            # this is the end
            os.execv(fqexc[0], fqexc)
        else:
            proc = subprocess.Popen(fqexc, stdin=subprocess.PIPE, env=env)
            if extcfg:
                proc.stdin.write(extcfg.encode())
            proc.stdin.close()
            proc.wait()
            if proc.returncode > 0:
                return self._do_failure(target, action, proc.returncode)
            return self._do_success(target, action)

    ############################################################################
    # pylint: disable=unused-argument
    def _do_failure(self, target, action, exitstatus):
        """
        Handle a failure
        """
        self.ABORT("Unable to continue after failure on '" + target + "'")

    ############################################################################
    # pylint: disable=unused-argument,inconsistent-return-statements
    def _do_success(self, target, action):
        """
        Handle a success
        """
        if 'onSuccess' in action:
            return self._do(action['onSuccess'])

    ############################################################################
    # pylint: disable=unused-argument
    def _do_setenv(self, cfg, output=False, export_meta=False):
        """
        Process a 'setenv' block, substituting $VAL from environment.

        if output=True or debug, print export output
        """
        if export_meta:
            for key in 'APIKEY', 'URL':
                key = 'REFLEX_' + key
                if key not in self.env:
                    self.env[key] = self.cfg[key]

        if not 'setenv' in cfg:
            return

        # use sys.stdout directly so the i/o order is correct
        # should consider changing to macro_expand (or making the % vs $ be variable
        # on sed_env.
        mdict = self.union_dict(cfg['setenv'], cfg.get('config', {}))
        for key in sorted(cfg['setenv']):
            self.env[key] = self.sed_env(str(cfg['setenv'][key]), mdict, key, env=self.env)
            if output:
                self.OUTPUT("export {}=\"{}\"".format(key, self.env[key]))
            elif self.do_DEBUG(module='test'):
                sys.stdout.write("export {}=\"{}\"\n".format(key, self.env[key]))
        sys.stdout.flush()
        return

    ############################################################################
    def banner(self, msg):
        """
        Print a distinguishable banner
        """

        if self.logfmt == 'txt':
            if self.do_DEBUG(module="test"):
                width = 80
            else:
                width = self.term_width
            if self.timestamp:
                width -= len(self.TIMESTAMP())
            banner_fmt = "{:=>" + str(width) + "s}"
            self.NOTIFY(banner_fmt.format(''), color='blue')
            padlen = width - (len(msg) + 6) # can overrun
            if padlen < 0:
                padlen = 0
            self.NOTIFY("==[ {} ]{:=>{len}s}".format(msg, '', len=padlen), color='blue')
            self.NOTIFY(banner_fmt.format(''), color='blue')
        else:
            self.NOTIFY(msg)

    ############################################################################
    # pylint: disable=invalid-name,inconsistent-return-statements
    def do(self, target, opts=None, export_meta=False):
        """
        do an action
        """

        # this is true if we have verified it
        if target not in self.did:
            self.verify(target, export_meta=export_meta)

        rfx.unbuffer(self.notifyfd)
        rfx.unbuffer(self.outputfd)

        # reset after ^^
        self.did = {}
        self.stack = []

        msg = "Starting Action '" + target + "'"
        if self.my_ip:
            msg += " on " + self.my_ip
        self.banner(msg)

        self.env['REFLEX_ACTION_BASE'] = self.action_fqpath

        # build environment, reset default to this
        self._do_setenv(self.config, export_meta=export_meta)

        try:
            return self._do(target, opts=opts)
        except KeyboardInterrupt:
            return

    ############################################################################
    def db_connect(self):
        """
        Open a connection to the back-end reflex-engine
        """
        if self.dbo:
            return
        self.dbo = Engine(base=self)
