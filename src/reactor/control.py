#$#HEADER-START
# vim:set expandtab ts=4 sw=4 ai ft=python:
#
#     Reactor Configuration Event Engine
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

# pylint: disable=missing-docstring

import sys
import os
import re
#from builtins import input # pylint: disable=redefined-builtin
import reactor
from reactor.backend import Core
from reactor.action import Action

################################################################################
class ControlCli(reactor.Base):

    ############################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if base:
            reactor.Base.__inherit__(self, base)
        if 'REACTOR_URL' in self.cfg.keys() and len(self.cfg['REACTOR_URL']):
            self.base_url = self.cfg['REACTOR_URL']

    ############################################################
    def update_cli(self): # pylint: disable=unused-argument
        # todo: current version printed
        answer = input("Update to latest version of reactor? [yes] ")
        if answer.lower() not in ["yes", "y", ""]:
            sys.exit(0)
        if not reactor.BASEDIR:
            sys.exit("Unable to find reactor basedir!")
        os.chdir(reactor.BASEDIR)
        action = Action(base=self)
        action.do("update")

    ############################################################
    def wizard_cli(self, argv, args): # pylint: disable=unused-argument
        for key in 'URL', 'APIKEY', 'TOKEN':
            if 'SET_REACTOR_' + key in os.environ.keys():
                self.cfg['REACTOR_' + key] = os.environ['SET_REACTOR_' + key]
            else:
                self.cfg['REACTOR_' + key] = input('REACTOR_' + key + ': ')
        # do this one without terminal echo
        os.system("stty -echo")
        if 'SET_REACTOR_SECRET'in os.environ.keys():
            self.cfg['REACTOR_SECRET'] = os.environ['SET_REACTOR_SECRET']
        else:
            self.cfg['REACTOR_SECRET'] = input('REACTOR_SECRET: ')
        os.system("stty echo")
        self.NOTIFY("")
        self.cfg_save()

    ############################################################
    def set_cli(self, argv, args): # pylint: disable=unused-argument
        match = re.match(r'^([a-zA-Z_-]+)\s*=\s*(.*)$', " ".join(argv))
        if not match:
            self.NOTIFY("set var=value")
            return
        param = match.group(1)
        if len(match.group(2)):
            value = match.group(2)
        else:
            value = input("Input Value: ")
        self.cfg[param] = value
        self.cfg_save()

    ############################################################
    def unset_cli(self, argv, args): # pylint: disable=unused-argument
        try:
            key = argv[0]
            del self.cfg[key]
            self.cfg_save()
        except KeyError:
            self.NOTIFY("parameter '" + key + "' not found.")
        except IndexError:
            self.NOTIFY("missing parameter name to unset.")

    ############################################################
    def list_cli(self, argv, args): # pylint: disable=unused-argument
        """dump the reactor config"""
        altcfg = self.cfg
        #altcfg['REACTOR_SECRET'] = 'xxxxxxxxxxxxx'
        #altcfg['REACTOR_APIKEY'] = 'xxxxxxxxxxxxx'
        for line in altcfg:
            self.NOTIFY(line + "=" + altcfg[line])

    ############################################################
    def apikey_cli(self, argv, args, cli):
        self.NOTIFY("Specify Administrative Token (end with newline):")
        admkey = input("Admin API Token: ")
        self.NOTIFY("")

        action = args.get('action')
        target = " ".join(argv)

        dbo = Core(base=self)

        try:
            if action in ('delete', 'create'):
                if not len(target):
                    cli.fail("No target specified for " + action)
                getattr(self, "apikey_cli__" + action)(dbo, admkey, target, cli)
            else:
                self.apikey_cli__list(dbo, admkey, cli)
        except reactor.CannotContinueError as err:
            err = str(err)
            if ": 401" in err:
                err = err + " Unauthorized"
            self.ABORT(err)

    ############################################################
    # pylint: disable=no-self-use,unused-argument
    def apikey_cli__delete(self, dbo, admkey, target, cli):
        dbo.delete_object("apikey", target, apikey=admkey)

    ############################################################
    def apikey_cli__list(self, dbo, admkey, cli): # pylint: disable=unused-argument
        self.NOTIFY("{0:20} {1:24} {2:24} {3}".format("Name", "Id", "CreatedAt", "Scope"))
        for obj in dbo.list_objects("apikey", apikey=admkey):
            self.NOTIFY("{name:20} {id:24} {createdAt:24} {scope}".format(**obj))

    ############################################################
    def apikey_cli__create(self, dbo, admkey, target, cli):
        keyval = target.split("=", 1)
        if len(keyval) != 2:
            cli.fail("Must make assignment as {name}={scope}")
        (name, scope) = keyval
        if scope in ["super", "sensitive", "write", "read"]:
            res = dbo.create_object("apikey",
                                    {"name":name, "scope":scope},
                                    apikey=admkey)
            self.NOTIFY("Created name={0}, id={1}, secret apikey:\n\n{2}\n"
                        .format(name, res['id'],
                                res.get('apikey', res.get('token', 'n/a'))))
        else:
            cli.fail("Scope is missing or wrong")
