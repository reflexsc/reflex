#!/usr/bin/env python3
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
Commands module.  Intended to be linked to the name of individual commands
Commands are called based on the linked name transformed into the class name
"""

# common includes
import sys
import os
import re
import subprocess
import dictlib
import copy
import ujson as json
try:
    from builtins import input # pylint: disable=redefined-builtin
    get_input = input
except:
    get_input = raw_input
import nacl.utils
import base64
import rfx
from rfx.backend import EngineCli, Engine
from rfx import client
from rfx.control import ControlCli
from rfx.launch import LaunchCli
from rfx.optarg import Args
from rfx.action import Action
from rfxcmd.setup import create, do
import rfxcmd.setup_demo
import rfxcmd.setup_basic

################################################################################
def new_base(args):
    """
    Common code to get the reflex base object

    >>> import json
    >>> b = new_base({'--debug': '*', '--logfmt': 'json', '--notime':True})
    >>> json.dumps(b.debug, sort_keys=True)
    '{"*": true, "basic": false}'
    >>> b.logfmt
    'json'
    >>> b.timestamp
    False
    """
    debug = []
    logfmt = 'txt'
    if args:
        debug = args.get('--debug', [])
        logfmt = args.get('--logfmt', 'txt')
        timestamp = not args.get('--notime')
    base = rfx.Base(debug=debug, logfmt=logfmt).cfg_load()
    base.timestamp = timestamp
    return base

################################################################################
def get_passwords(parsed, obj):
    """used by some of the CLI's to read a password into an client header"""
    words = parsed.get("--password")
    if words:
        if len(words) == 1:
            value = base64.b64encode(words[0].encode()).decode()
            obj.rcs.headers["X-Password"] = value
        else:
            value = base64.b64encode(json.dumps(words).encode()).decode()
            obj.rcs.headers["X-Passwords"] = value

################################################################################
class CliRoot(object):
    """
    Parent object used for CLI commands
    """
    cmd = ''
    args = None
    opts = None
    _args = None
    _opts = None

    ############################################################################
    # pylint: disable=unused-argument
    def __init__(self, cmd):
        if self.args:
            self._args = copy.copy(self.args.args)
            self._opts = copy.copy(self.args.opts)

    ############################################################################
    def syntax(self):
        """should be overridden by children, this is a poor default"""
        return """
Usage: """ + self.cmd + " " + "|".join(self.args.args[0][1]["set"]) + """

"""

    ############################################################################
    def fail(self, *reason):
        """Fail with a syntax message"""
        print(self.syntax())
        if reason:
            print(">> {}\n".format(*reason))
        sys.exit(1)

    ############################################################################
    def start(self, argv=None, opts=None):
        """Child objects override this to define behavior"""
        pass

    ############################################################################

################################################################################
class CliReflex(CliRoot):
    """reflex command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "scope", {
                    "type":"from-set",
                    "set": ["setup", "apikey", "policy",
                            "launch", "app", "engine|rxe", "action|act", "*"]
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ], [
                "--h?elp|-h", {
                    "type": "set-true",
                }
            ]
        )
        super(CliReflex, self).__init__(cmd)

    ###########################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + """ {scope} [...]

=> """ + self.cmd + """ setup {args}                   - setup local environ
=> """ + self.cmd + """ apikey {args}                  - manage apikeys
=> """ + self.cmd + """ password {args}                - manage group passwords
=> """ + self.cmd + """ policy {args}                  - manage policies
=> """ + self.cmd + """ launch env|app|config {args}   *
=> """ + self.cmd + """ action|act run|verify {action} *
=> """ + self.cmd + """ action|act list|ls             *
=> """ + self.cmd + """ app {args}                     *
=> """ + self.cmd + """ engine|rxe {args}              *
=> """ + self.cmd + """ monitor {args}

   Try --help with one of the scopes, for additional information.
   * these scopes are available as standalone commands
"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self)
        scope = args.get('scope')
        if not args or args.get('--help') and not scope:
            self.fail("no arguments specified")

        # this pulls a list of Cli target classes from the globals
        # list comprehensions are efficient, but ugly
        rex = re.compile(r'^Cli([A-Z][a-zA-Z]+)$')
        targets = [m.group(1).lower() for i in globals() for m in [rex.search(i)] if m]

        if scope in targets:
            obj = globals()["Cli" + scope.title()]
            obj(self.cmd + " " + scope).start(argv=self.args.argv, opts=args)
        else:
            self.fail() #print("default action would be triggered")
        sys.exit(0)

################################################################################
class CliMonitor(CliRoot):
    """monitor agent"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type": "from-set",
                    "set": ["start", "stop"]
                }
            ], [
                "--cfgin", {
                    "type": "set-true",
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ]
        )
        super(CliMonitor, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + """ start|stop

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        try:
            import rfxmon
        except:
            self.fail("Reflex Monitor module is not installed! Try:\n\n\tpip install rfxmon\n")
        method = args.get('action') + "_agent"
        callargs = {"cfgin":args.get("--cfgin")}

        getattr(rfxmon.Monitor(base=new_base(args)), method)(**callargs)


################################################################################
class CliSetup(CliRoot):
    """setup command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type": "from-set",
                    "set": ["l?ist|ls", "set", "get", "unset",
                            "wiz?ard",
                            "demo", "basic"]
                }
            ], [
                "--confirm", {
                    "type": "set-true",
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ]
        )
        super(CliSetup, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + """ l?ist|ls
       """ + self.cmd + """ set key=
       """ + self.cmd + """ set key=value
       """ + self.cmd + """ get key
       """ + self.cmd + """ unset key
       """ + self.cmd + """ wiz?ard
       """ + self.cmd + """ demo
       """ + self.cmd + """ basic    -- use this to initialize your engine

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail() # "no arguments specified")

        action = args.get('action')
        control = ControlCli(base=new_base(args))
        control.timestamp = False
        if action == 'demo':
            self.setup_demo(args)
        elif action == 'basic':
            self.setup_basic(args)
        else:
            getattr(control, action + "_cli")(self.args.argv, args)

    def setup_demo(self, args):
        if not args.get('--confirm'):
            get_input("This will populate your engine with demo data.\n" +
                      "Press [Enter/Return] to continue...")
        rfxcmd.setup_demo.setup()

    def setup_basic(self, args):
        if not args.get('--confirm'):
            get_input("This will populate your engine with basic a schema.\n" +
                      "Press [Enter/Return] to continue...")
        rfxcmd.setup_basic.setup()

################################################################################
class CliPolicy(CliRoot):
    """app command"""

    cfg = None

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type": "from-set",
                    "set": ["list"]
                }
            ]
        )
        super(CliPolicy, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + """ list

List policies and scopes in one view

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        self.base = rfx.Base().cfg_load()
        self.engine = Engine(base=self.base)

        cfg = self.engine.TRAP(self.engine.get_object, 'config', 'reflex', notify=False)
        if not cfg:
            self.fail("Missing config.reflex.  Try setting up demo data `reflex setup demo`")

        self.cfg = dictlib.Obj(cfg['config'])

#        if args.get('action') == 'list':
        self._list(args)

    ############################################################################
    # pylint: disable=unused-argument
    def _list(self, args):
        policies = self.engine.session.list("policy", cols=["name", "order", "policy", "result", "id"])
        scopes = dict()
        for scope in self.engine.session.list("policyscope", cols=["name", "actions", "matches","policy_id","type", "objects"]):
            policy_id = scope['policy_id']
            if scopes.get(policy_id):
                scopes[policy_id].append(scope)
            else:
                scopes[policy_id] = [scope]

        results = list()
        for policy in policies:
            print("\n<POLICY={name}> order={order} result={result}".format(**policy))
            count = 1
            for scope in scopes.get(policy['id']) or []:
                print("\n {count}: <SCOPE={name}> {type}:".format(count=count, **scope))

                if scope['type'] == 'global' and scope['matches'] == 'True':
                    print("  FOR:  ALL")
                else:
                    print("  FOR:  " + scope['matches'])
                print(" {uaction}:  {policy}".format(uaction=scope['actions'].upper(), **policy))
                count = count + 1

################################################################################
class CliApp(CliRoot):
    """app command"""

    cfg = None

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type": "from-set",
                    "set": ["del?ete|rm", "cre?ate|add"]
                }
            ], [
                "pipeline", {
                    "type":"set-value",
                }
            ], [
                "--r?egions|-r", {
                    "type": "set-value",
                }
            ], [
                "--l?anes|-l", {
                    "type": "set-value",
                }
            ], [
                "--t?enant|-t", {
                    "type": "set-value",
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ]
        )
        super(CliApp, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + """ {action} {pipeline} [options]

  {action} is one of del?ete|rm|cre?ate|add
  {pipeline} is the top level pipeline

Options:

  --r?egions={region(s)} - comma list of regions <required>
  --l?anes={lanes}       - list of service environments (i.e. prd, stg) <required>
  --t?enants={name}      - name of tenanant (a-z only) <optional> - as list allowed

Regions and Lanes are configured in the config:reflex object.

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        self.base = rfx.Base().cfg_load()
        self.engine = Engine(base=self.base)

        cfg = self.engine.TRAP(self.engine.get_object, 'config', 'reflex', notify=False)
        if not cfg:
            self.fail("Missing config.reflex.  Try setting up demo data `reflex setup demo`")

        self.cfg = dictlib.Obj(cfg['config'])

        if args.get('action') == 'delete':
            self._delete(args)
        else:
            self._create(args)

    ############################################################################
    # pylint: disable=unused-argument
    def _delete(self, args):
        print("Delete not finished")

    ############################################################################
    # pylint: disable=unused-argument
    def _create(self, args):
        regions = []
        lanes = []
        if args.get('--regions'):
            for region in re.split(r'\s*,\s*', args['--regions']):
                region = region.lower()
                cfgregions = self.cfg.regions
                if region not in cfgregions:
                    found = [x for x in cfgregions if cfgregions[x]['nbr'] == region]
                    if found:
                        region = found[0] # don't assign the same nbr to multiple regions
                    else:
                        self.fail("Invalid region: {}, Must be one of: {}"
                                  .format(region, ", ".join(self.cfg.regions.keys())))
                regions.append(region)

        tenants = []
        if args.get('--tenants'):
            for tenant in re.split(r'\s*,\s*', args['--tenants']):
                tenant = tenant.lower().strip()
                tenants.append(tenant)
        if not tenants:
            tenants = ['']

        if args.get('--lanes'):
            for lane in re.split(r'\s*,\s*', args['--lanes']):
                if lane not in self.cfg.lanes:
                    self.fail("Invalid lane: {}, Must be one of: {}"
                              .format(lane, ", ".join(self.cfg.lanes.keys())))
                lanes.append(lane)

        if not lanes:
            self.fail("Must specify at least one lane:\n\n\t" + 
                      ", ".join(self.cfg.lanes.keys()) + "\n\n" +
                      "Change options with: `engine config edit reflex`")

        if not regions:
            self.fail("Must specify at least one region:\n\n\t"+
                      ", ".join(self.cfg.regions.keys()) + "\n\n" +
                      "Change options with: `engine config edit reflex`")

        pipeline = args.get('pipeline').lower()

        # create prod apikey and policies
        rcs = self.engine.session
        read_grps = []
        if "prd" in lanes:
            name_p = pipeline + "-prd"
            apikey_p = self._tcreate('apikey', name_p, { "name": name_p })
            print("apikey {}.{}".format(apikey_p['name'], apikey_p['secrets'][0]))
            self._tcreate('group', 'svc-' + name_p, {
                "name": 'svc-' + name_p,
                "group": [apikey_p['name']],
                "type": "Apikey"
            })
            read_grps.append("token_name in groups['svc-" + name_p + "']")

            name_sp = pipeline + "-sensitive-prd"
            policy_d = self._tcreate('policy', name_sp, {
                "name": name_sp,
                "order": 1000,
                "policy": "sensitive and token_name in groups['svc-" + name_p + "']",
                "result": "pass"
            })
            lane_rx_p = '-[' + ''.join([self.cfg.lanes[lane]['short'] for lane in self.cfg.lanes if lane == 'prd']) + '][0-9]'
            scope_d = self._tcreate('policyscope', name_sp, {
                "name": name_sp,
                "policy": name_sp,
                "actions": 'read',
                "type": "targeted",
                "matches": "obj_type in ('Config', 'Pipeline', 'Service') and rx(r'^" + pipeline + "(-[a-z]+)?(" + lane_rx_p + ")?$', obj['name'])",
            })

        # create non-prod apikey and policies
        if lanes != ["prd"]: # if there is more than just prd
            name_d = pipeline + "-nonprd"
            apikey_d = self._tcreate('apikey', name_d, { "name": name_d })
            print("apikey {}.{}".format(apikey_d['name'], apikey_d['secrets'][0]))

            self._tcreate('group', 'svc-' + name_d, {
                "name": 'svc-' + name_d,
                "group": [apikey_d['name']],
                "type": "Apikey"
            })
            read_grps.append("token_name in groups['svc-" + name_d + "']")

            name_snp = pipeline + "-sensitive-nonprd"
            policy_d = self._tcreate('policy', name_snp, {
                "name": name_snp,
                "order": 1000,
                "policy": "sensitive and token_name in groups['svc-" + name_d + "']",
                "result": "pass"
            })
            lane_rx_np = '-[' + ''.join([self.cfg.lanes[lane]['short'] for lane in self.cfg.lanes if lane != 'prd']) + '][0-9]'
            scope_d = self._tcreate('policyscope', name_snp, {
                "name": name_snp,
                "policy": name_snp,
                "actions": 'read',
                "type": "targeted",
                "matches": "obj_type in ('Config', 'Pipeline', 'Service') and rx(r'^" + pipeline + "(-[a-z]+)?(" + lane_rx_np + ")?$', obj['name'])",
            })

        # create a non-sensitive reading policy
        name_pall = pipeline + "-non-sensitive-all"
        policy_d = self._tcreate('policy', name_pall, {
            "name": name_pall,
            "order": 1000,
            "policy": "not sensitive and (" + " or ".join(read_grps) + ")",
            "result": "pass"
        })
        name_len = str(len(pipeline))
        scope_d = self._tcreate('policyscope', name_pall, {
            "name": name_pall,
            "policy": name_pall,
            "actions": 'read',
            "type": "targeted",
            "matches": "obj_type in ('Config', 'Pipeline', 'Service') and obj['name'][:" + name_len + "] == '" + pipeline + "'",
        })

        # create an Instances read/write policy
        name_inst = pipeline + "-instance-read"
        policy_d = self._tcreate('policy', name_inst, {
            "name": name_inst,
            "order": 1000,
            "policy": " or ".join(read_grps),
            "result": "pass"
        })
        scope_d = self._tcreate('policyscope', name_inst, {
            "name": name_inst,
            "policy": name_inst,
            "actions": 'read',
            "type": "global",
            "matches": "obj_type in ('Instance')"
        })

        name_inst = pipeline + "-instance-write"
        # we move the object service check into the policy, out of the object, for auto-adding (but slower)
        policy_d = self._tcreate('policy', name_inst, {
            "name": name_inst,
            "order": 1000,
            "policy": "(" + " or ".join(read_grps) + ") and obj['service'][:" + name_len + "] == '" + pipeline + "'",
            "result": "pass"
        })
        scope_d = self._tcreate('policyscope', name_inst, {
            "name": name_inst,
            "policy": name_inst,
            "actions": 'write',
            "type": "targeted",
            "matches": "obj_type in ('Instance')",
        })

        for region in regions:
            for lane in lanes:
                if lane not in self.cfg.regions[region].lanes:
                    continue
                for tenant in tenants:
                    self._create_for(pipeline, region, lane, lanes, tenant)

    ################################################################################
    def _cget(self, otype, target):
        return self.engine.cache_get_object(otype, target)

    ################################################################################
    def _tcreate(self, otype, name, template):
        obj = None
        try:
            obj = self.engine.session.get(otype, name)
        except rfx.client.ClientError as err:
            if str(err) == "Forbidden":
                sys.exit("ABORT: You do not have permission to create or view APIkeys")
            if "object not found" not in str(err): #[:28] != "Endpoint or object not found":
                raise
        if obj:
            print("Keeping existing {} {}".format(otype, name))
        else:
            print("Creating {} {}".format(otype, name))
            self.engine.session.create(otype, template)
        return self.engine.TRAP(self.engine.session.get, otype, name)

    ################################################################################
    def _template(self, otype, target, template, default):
        obj = self._cget(otype, target)
        if obj:
            print("Using existing {} {}".format(otype, target))
        elif template:
            obj = self._cget(otype, template)
            if obj:
                print("Using template {} {}: {}".format(otype, target, template))
            else:
                print("Using defaults for {} {}".format(otype, target))
                obj = default
        else:
            print("Using defaults for {} {}".format(otype, target))
            obj = default
        return obj
    
    ################################################################################
    # todo: allow for templates in config
    def _create_for(self, pipeline, region, lane, lanes, tenant):

        shortlane = self.cfg.lanes[lane].short + str(self.cfg.regions[region].nbr)

        svc_name = pipeline + '-' + shortlane
        env_name = svc_name
        common_name = pipeline + '-' + lane.lower()

        print("Populate {} {} {} {} {}".format(pipeline, lane, svc_name, common_name, tenant))
    
        tenant = tenant.lower()
        top_config = ''
        if not tenant:
            tenant = 'multitenant'
    
        if tenant == 'multitenant':
            multitenant = False
            top_config = env_name
        else:
            multitenant = True
            top_config = env_name + '-' + tenant
            svc_name = top_config
            common_name += '-' + tenant
    
        ######################################
        ## start with the pipeline
        pipe_o = self._template('pipeline', pipeline, '', {
            "launch": {
                "cfgdir": ".",
                "exec": [
                    "/app/" + pipeline + "/launch"
                ],
                "rundir": "."
            },
            "name": pipeline,
            "title": pipeline.capitalize() + " " + svc_name
        })
    
        ######################################
        # and a pipeline base config object
        cfg_o = self._template('config', pipeline, '', {
            "name": pipeline,
            "sensitive": {
                "parameters": {
                }
            },
            "setenv": {},
            "type": "parameter"
        })
    
        ######################################
        ## a service config object
        grp_key = base64.b64encode(nacl.utils.random(64))
    
        cfg_env_o = self._template('config', env_name, '', {
            "name": env_name,
            "extends": [pipeline],
            "sensitive": {
                "parameters": {
                    "SEED": grp_key,
                    "ENVIRON-NAME": lane.upper(),
                    "LANE": lane.lower(),
                    "LANE-SUFFIX": "-%{LANE}"
                }
            },
            "setenv": {},
            "type": "parameter"
        })
    
        ######################################
        ## define the service
        svc_o = self._template('service', svc_name, '', {
            "actions":{
              "deploy":{
                "type":"noop"
              }
            },
            "name": svc_name,
            "region": region,
            "region-nbr": self.cfg.regions[region].nbr,
            "pipeline": pipeline,
            "common-name": common_name.lower(),
            "config": svc_name,
            "tenant": tenant, # will be multitenant if not single tenant
            "lane": lane.lower()
        })
    
        ######################################
        ## if multitenant, also a tenant config
        if multitenant:
            cfg_tenant_o = self._template('config', top_config, '', {
                'extends': [env_name],
                'sensitive':{'parameters':{}},
                'type':'parameter',
                'setenv':{}
            })
    
        ######################################
        # store changes
        svc_o['config'] = top_config

        # top_config = env_name
        trap = self.engine.TRAP
        trap(self.engine.cache_update_object, 'pipeline', pipeline, pipe_o)
        trap(self.engine.cache_update_object, 'config', pipeline, cfg_o)
        trap(self.engine.cache_update_object, 'config', env_name, cfg_env_o)
        trap(self.engine.cache_update_object, 'service', svc_name, svc_o)

        if multitenant:
            trap(self.engine.cache_update_object, 'config', top_config, cfg_tenant_o)

        return {
            'pipeline': pipeline,
            'service': svc_name,
            'config': top_config
        }

################################################################################
class CliEngine(CliRoot):
    """engine command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "object", {
                    "type": "from-set",
                    "set": ["pi?peline", "se?rvice|svc",
                            "co?nfig|cfg", "bu?ild", "in?stance", "api?key", "pol?icy", "policyscope|psc?ope|sco?pe", "gr?oup|grp", "state"]
                }
            ], [
                "action", {
                    "type":"from-set",
                    "set": ["li?st|ls", "cr?eate", "get", "ed?it", "up?date",
                            "merge|set", "del?ete|rm", "co?py|cp", "sl?ice"]
                }
            ], [
                "--c?ontent|-c", {
                    "type": "set-value",
                }
            ], [
                "--e?xpression|-e", {
                    "type":"set-value",
                }
            ], [
                "--stderr|--err", {
                    "type":"set-value",
                }
            ], [
                "--p?assword|--pwd|-p", {
                    "type":"set-password",
                }
            ], [
                "--stdout|--out", {
                    "type":"set-value",
                }
            ], [
                "--s?how|-s", {
                    "type":"set-value",
                }
            ], [
                "--archive|-a", {
                    "type":"set-value",
                }
            ], [
                "--f?ormat|-f", {
                    "type":"from-set",
                    "set": ["txt", "json", "list", "csv", "tsv"]
                }
            ], [
                "--d?ebug|-d", {
                    "type": "set-add"
                }
            ]
        )
        super(CliEngine, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        objs = "|".join(self._args[0][1]["set"])
        acts = "|".join(self._args[1][1]["set"])
        return """
Usage: """ + self.cmd + """ {object} {action} [args & options]

    {object} is one of: 
        """ + objs + """

    {action} is one of: 
        """ + acts + """

Arguments and options vary by action:

=> """ + self.cmd + """ {object} li?st|ls [name-filter] [-e=expr] [-s=col,col] [--archive]
   [name-filter] is a regex to limit name matches
   --ex?presssion|-e provides a logical expression referencing obj.{key} in
       dot notation (i.e. obj.stage="STG").  python expression syntax.
   --sh?ow|-s is a comma list of available columns: name, title, id, updated
   --archive=[FROM[~TO]] directs it to show archived copies, not current. FROM and
       TO date is optional, but will limit results within range (default is all time,
       and TO defaults to now).  A tight scope on the name filter is helpful to
       reduce the set of objects returned. Dates for FROM and TO are allowed
       in many formats, including relative time (5 minutes ago).

=> """ + self.cmd + """ {object} cr?eate {name} [-c=json]
   If --c?ontent|-c is not specified, reads content from stdin.

=> """ + self.cmd + """ {object} get {name} [key] [--archive=DATE]
   {name} is the absolute name of the object
   [key] is an optional key in dot notation (.e. obj.name)
   --archive=DATE get a specific version (matching date) from archive

=> """ + self.cmd + """ {object} ed?it {name}
   edit object named {name} in your environment's $EDITOR.  If $EDITOR is
   undefined, defaults to vim

=> """ + self.cmd + """ {object} up?date {name} [-c=json]
   Updates {name} with full json object
   If --c?ontent|-c is not specified, reads content from stdin.

=> """ + self.cmd + """ {object} merge|set {name} [-c=json]
   Updates {name} with a dictionary merge of content
   If --c?ontent|-c is not specified, reads content from stdin.

=> """ + self.cmd + """ {object} del?ete {name}

=> """ + self.cmd + """ {object} co?py|cp {from-name} {to-name}

=> """ + self.cmd + """ {object} slice {name-filter} {limit-expression} {key}
   create a cross sectional set-union of {key} values on all objects matching
   {name-filter} and {limit-expression}

Additionally:

   --p?assword|--pwd|-p may be provided, to prompt for a password
       which is sent as an additional attribute for ABAC policies.  Multiple passwords
       may be prompted for by including --password multiple times

   --f?ormat|-f={txt,list,csv,tsv,json} for output format changes
"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        parsed = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not parsed or parsed.get('--help') and not self.args.argv:
            self.fail()

        base = new_base(parsed)
        cli = EngineCli(base=base)

        get_passwords(parsed, cli)

        objtype = parsed['object']
        action = parsed['action']
        if action == "list":
            cli.list_cli(objtype, parsed, self.args.argv)
        elif action == "copy":
            args2 = Args(["name-filter", {"type": "set-value"}],
                         ["limit-expression", {"type": "set-value"}])
            parsed2 = args2.handle_parse(caller=self,
                                         argv=self.args.argv,
                                         opts=parsed)
            cli.copy_cli(objtype,
                         parsed2['name-filter'],
                         parsed2['limit-expression'])
        elif action == 'slice':
            args2 = Args(["name-filter", {"type": "set-value"}],
                         ["limit-expression", {"type": "set-value"}],
                         ["key", {"type": "set-value"}])
            parsed2 = args2.handle_parse(caller=self,
                                         argv=self.args.argv,
                                         opts=parsed)
            getattr(cli, action + "_cli")(objtype, parsed2, args2.argv)
        else:
            args2 = Args(["name", {"type": "set-value"}])
            parsed2 = args2.handle_parse(caller=self,
                                         argv=self.args.argv,
                                         opts=parsed)
            getattr(cli, action + "_cli")(objtype, parsed2, args2.argv)

# short name
class CliRxe(CliEngine):
    pass

################################################################################
class CliAction(CliRoot):
    """action command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "cmd", {
                    "type":"from-set",
                    "set": ["verify", "env", "l?ist|ls", "run", "*"]
                }
            ], [
                "--export?-meta|--expose?-meta", {
                    "type": "set-true",
                }
            ], [
                "--config", {
                    "type": "set-value",
                }
            ], [
                "--notime", {
                    "type": "set-true",
                }
            ], [
                "--logfmt", {
                    "type": "from-set",
                    "set": ["json", "txt"]
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ]
        )
        super(CliAction, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage:

=> """ + self.cmd + """ verify|env|list
=> """ + self.cmd + """ run [options] ACTION [args...]
=> """ + self.cmd + """ [options] ACTION [args...]

Where ACTION is one of the defined reflex actions in .pkg/actions.json

[options] can be any of:

  --export-meta      -- if set, REFLEX_APIKEY/URL will be sent to sub processes
  --config=f         -- use action config file f
  --notime           -- do not include timestamps
  --logfmt=txt|json  -- log format as json or txt
  --debug=module     -- debug output for module, or * for all

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        base = new_base(args)
        base.timestamp = False
        action = Action(base=base, extcfg=args.get('--config'))
        cmd = args.get('cmd')
        opts = self.args.argv
        target = None
        if opts:
            target = opts.pop(0)

        if cmd == 'list':
            actions = action.config['actions']
            print("{:30s} {}".format("ACTION", "TYPE"))
            for name in sorted(actions.keys()):
                print("{:30s} {}".format(name, actions[name].get('type', '')))
        elif cmd == 'env':
            # pylint: disable=protected-access
            action._do_setenv(action.config, output=True)
        elif cmd == 'verify':
            action.verify(target)
        elif cmd == 'run' and len(target):
            action.do(target, export_meta=args.get('--export-meta'), opts=opts)
        elif cmd in action.config['actions'].keys():
            if target:
                opts = [target] + opts
            action.do(cmd, export_meta=args.get('--export-meta'), opts=opts)
        else:
            if cmd:
                self.fail("'" + cmd + "' is not a valid action, try one of:\n" +
                          "   " + ", ".join(action.config['actions'].keys()) + "\n")
            else:
                self.fail("No ACTION target specified\n")
            sys.exit(1)

################################################################################
class CliLaunch(CliRoot):
    """launch command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type":"from-set",
                    "set": ["service|app", "env", "config|cfg"]
                }
            ], [
                "--noexport", {
                    "type": "set-true",
                }
            ], [
                "--commit", {
                    "type": "set-true",
                }
            ], [
                "--notime", {
                    "type": "set-true",
                }
            ], [
                "--logfmt", {
                    "type": "from-set",
                    "set": ["json", "txt"]
                }
            ], [
                "--debug|-d", {
                    "type": "set-add",
                }
            ]
        )
        super(CliLaunch, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        return """
Usage: """ + self.cmd + " " + "|".join(self._args[0][1]["set"]) + """ [..args]

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        base = new_base(args)
        base.timestamp = not args.get('--notime')
        method = args.get('action') + "_cli"
        getattr(LaunchCli(base=base), method)(self.args.argv, args, self)

# short name
class CliAct(CliAction):
    pass

################################################################################
class CliApikey(CliRoot):
    """apikey command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "action", {
                    "type":"from-set",
                    "set": ["cre?ate|add", "del?ete", "l?ist|ls"]
                }
            ], [
                "--p?assword|--pwd|-p", {
                    "type":"set-password",
                }
            ]
        )
        super(CliApikey, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        """not always the right default"""
        return """
Usage: """ + self.cmd + " " + "|".join(self._args[0][1]["set"]) + """ [..args]

=> """ + self.cmd + """ list
=> """ + self.cmd + """ delete {name}
=> """ + self.cmd + """ create {name}

   Where NAME identifies the user, and scope is one of: super, sensitive, write, read

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        parsed = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not parsed or parsed.get('--help') and not self.args.argv:
            self.fail()

        cli = ControlCli(base=new_base(parsed))
        get_passwords(parsed, cli)
        cli.apikey_cli(self.args.argv, parsed, self)

################################################################################
class CliPassword(CliRoot):
    """password command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "group", {
                    "type":"set-add",
                }
            ], [
                "name", {
                    "type":"set-add",
                }
            ], [
                "--p?assword|--pwd|-p", {
                    "type":"set-password",
                }
            ]
        )
        super(CliPassword, self).__init__(cmd)

    ############################################################################
    # pylint: disable=missing-docstring
    def syntax(self):
        """not always the right default"""
        return """
Usage: """ + self.cmd + """ {group} {name}

   Add/Change a password for {name} to {group}

Example:

    """ + self.cmd + """ custodians susan

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        parsed = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not parsed or parsed.get('--help') and not self.args.argv:
            self.fail()

        cli = ControlCli(base=new_base(parsed))
        get_passwords(parsed, cli)

        try:
            grp = cli.rcs.get("group", parsed.get('group'))
        except rfx.client.ClientError:
            self.fail("Unable to find group: " + parsed.get('group'))

        if grp.get('type') != 'password':
            self.fail("Group " + parsed.get('group') + " is not a password type group")

        subprocess.call(["stty", "-echo"])
        try:
            pwd1 = None
            pwd2 = False
            while pwd1 != pwd2:
                pwd1 = get_input("New Password: ")
                print("")
                pwd2 = get_input("New Password (again): ")
                print("")
                if pwd1 != pwd2:
                    cli.NOTIFY("Passwords do not match")
        except:
            raise
        finally:
            subprocess.call(["stty", "echo"])

        newname = parsed.get('name').lower()
        elems = list()
        for elem in grp['group']:
            name = (elem + ":").split(":")[0].lower()
            if name != newname:
                elems.append(elem)

        elems.append(newname + ":" + pwd1)
        grp['group'] = elems

        cli.rcs.update("group", grp['name'], grp)

################################################################################
def main():
    try:
        cmd = os.path.basename(sys.argv[0])
        cli_class = "Cli" + cmd.title()
        globals()[cli_class](cmd).start()
    except KeyboardInterrupt:
        pass

    sys.exit(0)

################################################################################
if __name__ == "__main__":
    main()

