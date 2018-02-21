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
Powerfully easy argument parsing
"""

import sys
import copy
# subprocess and input are used by password
import subprocess
# pylint: disable=duplicate-code
try:
    from builtins import input # pylint: disable=redefined-builtin
    get_input = input # pylint: disable=invalid-name
except: # pylint: disable=bare-except
    get_input = raw_input # pylint: disable=invalid-name, undefined-variable

################################################################################
class ArgParse(Exception):
    """Error when processing args"""
    pass


############################################################################
def opt_key(optname, optinfo):
    """
    Return the key name to use for an option

    >>> opt_key("bo?bo", {'key': 'nono'})
    'nono'
    >>> opt_key("bo?bo", {})
    'bobo'
    """
    if optinfo.get('key'):
        return optinfo.get('key')
    return strip_opt(optname)

################################################################################
def arg_key(argname, arginfo):
    """
    Return the key name to use for an argument

    >>> arg_key("bobo", {'key': 'nono'})
    'nono'
    >>> arg_key("bobo", {})
    'bobo'
    """
    if arginfo.get('key'):
        return arginfo.get('key')
    return argname

################################################################################
def from_set(name, info, value):
    """
    Select a value from a set, using option matching

    >>> from_set("thegang", {'set': ["scoob?y", "fred", "vel?ma"]}, "shaggy")
    Traceback (most recent call last):
    ...
    optarg.ArgParse: Invalid option for thegang (shaggy), must be one of: scoob?y, fred, vel?ma
    >>> from_set("thegang", {'set': ["scoob?y", "fred", "vel?ma"]}, "velm")
    'velma'
    """
    vals = info.get('set')
    if not vals:
        raise ArgParse("No set defined for arg type=from-set")
    if isinstance(vals, list):
        for opt in vals:
            opt = str(opt)
            if opt == "*":
                return value
            if optmatch(opt, value):
                return strip_opt(opt)
        raise ArgParse("Invalid option for " + name + " (" + value +
                       "), must be one of: " + ", ".join(vals))
    else:
        raise ArgParse("Invalid definition, 'set' value is not list") # or Args")

################################################################################
def strip_opt(name):
    """
    strip special characters from an option definition

    >>> strip_opt("fr?ed|sc?ooby|shaggy")
    'fred'
    """
    return name.split("=", 1)[0].split("|", 1)[0].replace("?", "")

################################################################################
def optmatch(pattern, match):
    """
    Easier argument specs than argparse

    >>> optmatch("l?ist|ls", "lis")
    True
    >>> optmatch("l?ist|ls", "ls")
    True
    >>> optmatch("l?ist|ls", "l")
    True
    >>> optmatch("l?ist|ls", "loo")
    False
    """
    match = str(match).lower().split("=", 1)[0]
    for variant in pattern.split("|"):
        req, opt = (variant + "?").split("?")[0:2]
        if req == match:
            return True
        for char in opt:
            req = req + char
            if req == match:
                return True
    return False

################################################################################
class Args(object):
    # pylint: disable=line-too-long
    """
    Define an argument set.  Provide a list of argument definitions.
    Arg definitions are a two array element:

        [ "name", {info} ]

    Order is important, as it relates to the order of sys.argv.  Arguments
    which are not processed are preserved, and may be later passed to a
    secondary args object for futher processing (such as with forked command
    sets).

    {info} has "type" defined, as one of:
        "from-set", "set-value", "set-add", "set-true", "set-password"
    if type is from-set, the additional key "set" is defined as an array,
    with strings being any match strings for optmatch()

    Result of calling args.handle_parse() is a dictionary of matched values.

    May call subsequent parsers and pass along already parsed information by
    passing along argv=args.argv and ops={result of previous parse call}

    >>> import json
    >>> def dump(data): return json.dumps(data, sort_keys=True)
    >>> args = Args(
    ...     [
    ...         "action", {
    ...             "type": "from-set",
    ...             "set": ["d?rive|a?ccelerate", "stop|b?rake"]
    ...         }
    ...     ], [
    ...         "where", {
    ...             "type": "set-value"
    ...         }
    ...     ], [
    ...         "--over?ride|-o", {
    ...             "type": "store-true"
    ...         }
    ...     ]
    ... )
    >>> dump(args.args)
    '[["action", {"set": ["d?rive|a?ccelerate", "stop|b?rake"], "type": "from-set"}], ["where", {"type": "set-value"}]]'
    >>> dump(args.opts)
    '{"--h?elp|-h": {"type": "syntax"}, "--over?ride|-o": {"type": "store-true"}}'
    >>> dump(args.handle_parse(argv=["drive", "north", "south", "--overr", "-p"]))
    '{"--override": true, "action": "drive", "where": "north"}'
    >>> args.argv
    ['south', '--overr', '-p']
    """
    args = None
    opts = None
    argv = None
    out = None

    ############################################################################
    def __init__(self, *arginput):
        self.args = list()
        self.opts = dict()
        self.opts["--h?elp|-h"] = {"type":"syntax"}

        for opt, dat in arginput:
            if opt[0] == '-':
                self.opts[opt] = dat
            else:
                self.args.append([opt, dat])

    ############################################################################
    def handle_parse(self, caller=None, argv=None, out=None, opts=None):
        """
        Top level method called to start parsing.
        Traps the ArgParse exception with a nice print.
        """
        try:
            return self.parse(caller, argv=argv, out=out, opts=opts)
        except ArgParse as err:
            print(err)
            sys.exit(1)

    ############################################################################
    # pylint: disable=too-many-branches
    def parse(self, caller=None, argv=None, out=None, opts=None):
        """
        Lower level parser, may be called at multiple levels
        """
        if isinstance(out, dict):
            self.out = out
        else:
            self.out = dict()


        # inherit options from higher levels
        if isinstance(opts, dict):
            for opt, val in opts.items():
                if opt[0] == '-':
                    self.out[opt] = val

        if isinstance(argv, list):
            self.argv = copy.copy(argv)
        else:
            self.argv = copy.copy(sys.argv[1:])

        unparsed = list()
        while self.argv:
            arg = self.argv.pop(0)

            if arg[0] == '-':
                matched = False
                for opt in self.opts:
                    if optmatch(opt, arg):
                        matched = True
                        self._set_opt(caller, opt, arg)
                        break
                if not matched:
                    unparsed.append(arg)
            else:
                if self.args:
                    self._set_arg(arg)
                else:
                    unparsed.append(arg)
                    continue

        if self.args:
            if caller:
                caller.fail("Missing argument: {" + self.args[0][0] + "}")
            else:
                sys.exit("Missing argument: {" + self.args[0][0] + "}")

        self.argv = unparsed
        return self.out

    ############################################################################
    def _set_opt(self, caller, opt, arg):
        """
        Set an option
        """
        optinfo = self.opts[opt]
        opttype = optinfo.get('type', 'set-true')
        optkey = opt_key(opt, optinfo)
        if opttype in ('set-value', 'set-add', 'from-set'):
            if '=' in arg:
                arg, value = arg.split("=", 1)
            elif self.argv:
                value = self.argv.pop(0)
            else:
                raise ArgParse("Unable to get value for " + opt)
            opttype = optinfo.get('type', 'set-true')
            if opttype == 'from-set':
                value = from_set(opt, optinfo, value)
            elif opttype == 'set-add':
                if self.out.get(optkey):
                    self.out[optkey].append(value)
                    value = self.out[optkey]
                else:
                    value = [value]

        elif opttype == 'set-password':
            if '=' in arg:
                raise ArgParse(opt + " may only be queried via stdin")
            # ugly hack, could do this better
            current = self.out.get(optkey) or []
            clen = len(current) + 1
            subprocess.call(["stty", "-echo"])
            try:
                sys.stderr.write("Password {}: ".format(clen))
                sys.stderr.flush()
                pwd = get_input()
                sys.stderr.write("\n")
            except:
                raise
            finally:
                subprocess.call(["stty", "echo"])

            if current:
                self.out[optkey].append(pwd)
                value = self.out[optkey]
            else:
                value = [pwd]

        elif opttype == 'syntax':
            if caller:
                caller.fail()
            else:
                print("Help not supported")

        else:
            value = True # pylint: disable=redefined-variable-type

        self.out[optkey] = value

    ############################################################################
    def _set_arg(self, value):
        """
        Set an argument
        """
        argname = self.args[0][0]
        arginfo = self.args[0][1]
        self.args = self.args[1:]

        argtype = arginfo.get('type', 'set-value')
        if argtype == 'from-set':
            value = from_set(argname, arginfo, value)

        self.out[arg_key(argname, arginfo)] = value

