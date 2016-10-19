#!/app/local/bin/virtual-python
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
import dictlib
import rfx
from rfx.backend import EngineCli, Engine
from rfx import client
from rfx.control import ControlCli
from rfx.launch import LaunchCli
from rfx.optarg import Args
from rfx.action import Action

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
            self._args = self.args.args.copy()
            self._opts = self.args.opts.copy()

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
                    "set": ["setup", "update|upgrade", "apikey",
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

=> """ + self.cmd + """ setup {args}
=> """ + self.cmd + """ apikey {args}
=> """ + self.cmd + """ update|upgrade
=> """ + self.cmd + """ launch env|app|config {args}    *
=> """ + self.cmd + """ action|act run|verify {action}  *
=> """ + self.cmd + """ action|act list|ls              *
=> """ + self.cmd + """ app {args}                      *
=> """ + self.cmd + """ engine|rxe {args}               *

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
            print("default action would be triggered")
        sys.exit(0)

################################################################################
# pylint: disable=missing-docstring
class CliUpdate(CliRoot):

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        control = ControlCli(base=new_base(opts))
        control.update_cli()

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
                    "set": ["l?ist|ls", "set", "unset",
                            "wiz?ard", "update|upgrade"]
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
       """ + self.cmd + """ unset key
       """ + self.cmd + """ wiz?ard

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail() # "no arguments specified")

        action = args.get('action')
        control = ControlCli(base=new_base(args))
        getattr(control, action + "_cli")(self.args.argv, args)

################################################################################
class CliApp(CliRoot):
    """app command"""

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
                "product", {
                    "type":"set-value",
                }
            ], [
                "service", {
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
Usage: """ + self.cmd + """ {action} {product} {service} [options]

  {action} is one of del?ete|rm|cre?ate|add
  {product} is the top level product to be configured
  {service} is the service within the product

Options:

  --r?egions={region(s)} - comma list of regions
  --l?anes={lanes}       - list of service environments (i.e. prd, stg)
  --t?enant={name}       - name of tenanant (a-z only)

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        base = rfx.Base().cfg_load()
        core = Engine(base=base)
        mstrcfg = dictlib.Obj(core.get_object('config',
                                              'reflex',
                                              notify=False)['config'])

        if args.get('action') == 'delete':
            self._delete(args, base, core, mstrcfg)
        else:
            self._create(args, base, core, mstrcfg)

    ############################################################################
    # pylint: disable=unused-argument
    def _delete(self, args, base, core, mstrcfg):
        print(self)
        print("Delete here")

    ############################################################################
    # pylint: disable=unused-argument
    def _create(self, args, base, core, mstrcfg):
        regions = []
        lanes = []
        if args.get('--regions'):
            for region in re.split(r'\s*,\s*', args['--regions']):
                region = region.lower()
                if region not in mstrcfg.regions:
                    self.fail("Invalid region: {}, Must be one of: {}"
                              .format(region, ", ".join(mstrcfg.regions.keys())))
                regions.append(region)

        if args.get('--lanes'):
            for lane in re.split(r'\s*,\s*', args['--lanes']):
                if lane not in mstrcfg.lanes:
                    self.fail("Invalid lane: {}, Must be one of: {}"
                              .format(lane, ", ".join(mstrcfg.lanes.keys())))
                lanes.append(lane)

        if not lanes:
            self.fail("Must specify at least one lane")

        if not regions:
            self.fail("Must specify at least one region")

        for region in regions:
            for lane in lanes:
                if lane not in mstrcfg.regions[region].lanes:
                    continue
                match = re.search(r'([0-9]+)$', region)
                shortcode = mstrcfg.lanes[lane].short + match.group(1)
                print("Populate {} {} {} {} {}".format(args['product'], args['service'],
                                                       region, shortcode, args.get('--tenant', '')))

################################################################################
class CliEngine(CliRoot):
    """core command"""

    ############################################################################
    def __init__(self, cmd):
        self.cmd = cmd
        self.args = Args(
            [
                "object", {
                    "type": "from-set",
                    "set": ["pi?peline", "se?rvice|svc",
                            "co?nfig|cfg", "re?lease", "in?stance", "api?key", "policy", "policyscope", "group"]
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
                "--stdout|--out", {
                    "type":"set-value",
                }
            ], [
                "--s?how|-s", {
                    "type":"set-value",
                }
            ], [
                "--f?ormat|-f", {
                    "type":"from-set",
                    "set": ["txt", "json"]
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

=> """ + self.cmd + """ {object} li?st|ls [name-filter] [-e=expr] [-s=col,col]
   [name-filter] is a regex to limit name matches
   --ex?presssion|-e provides a logical expression referencing obj.{key} in
       dot notation (i.e. obj.stage="STG").  python expression syntax.
   --sh?ow|-s is a comma list of available columns: name, title, id, updated

=> """ + self.cmd + """ {object} cr?eate {name} [-c=json]
   If --c?ontent|-c is not specified, reads content from stdin.

=> """ + self.cmd + """ {object} get {name} [key]
   {name} is the absolute name of the object
   [key] is an optional key in dot notation (.e. obj.name)

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
   create a cross sectinoal set-union of {key} values on all objects matching
   {name-filter} and {limit-expression}

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        parsed = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not parsed or parsed.get('--help') and not self.args.argv:
            self.fail()

        base = new_base(parsed)

        cli = EngineCli(base=base)
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
=> """ + self.cmd + """ run ACTION
=> """ + self.cmd + """ ACTION

Where ACTION is one of the defined reflex actions in .pkg/actions.json

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
        target = self.args.argv
        if target:
            target = target[0]

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
            action.do(target, export_meta=args.get('--export-meta'))
        elif cmd in action.config['actions'].keys():
            action.do(cmd, export_meta=args.get('--export-meta'))
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
=> """ + self.cmd + """ create {name}={role}

   Where NAME identifies the user, and scope is one of: super, sensitive, write, read

"""

    ############################################################################
    # pylint: disable=missing-docstring
    def start(self, argv=None, opts=None):
        args = self.args.handle_parse(caller=self, argv=argv, opts=opts)
        if not args or args.get('--help') and not self.args.argv:
            self.fail()

        ControlCli(base=new_base(args)).apikey_cli(self.args.argv, args, self)

################################################################################
def main():
    try:
        cmd = os.path.basename(__file__)
        cli_class = "Cli" + cmd.title()
        globals()[cli_class](cmd).start()
    except KeyboardInterrupt:
        pass

    sys.exit(0)

################################################################################
if __name__ == "__main__":
    main()

