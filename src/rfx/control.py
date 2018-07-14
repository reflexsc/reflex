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

# pylint: disable=missing-docstring

import os
import re
import traceback
import rfx
import rfx.client
from rfx.backend import EngineCli

################################################################################
class ControlCli(rfx.Base):

    rcs = None

    ############################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if base:
            rfx.Base.__inherit__(self, base)
        if 'REFLEX_URL' in self.cfg.keys() and self.cfg['REFLEX_URL']:
            self.base_url = self.cfg['REFLEX_URL']
        self.rcs = rfx.client.Session(debug=self.debug, base=self)

    ############################################################
    def wizard_cli(self, argv, args): # pylint: disable=unused-argument
        for key in 'URL', 'APIKEY':
            if 'SET_REFLEX_' + key in os.environ.keys():
                self.cfg['REFLEX_' + key] = os.environ['SET_REFLEX_' + key]
            else:
                self.cfg['REFLEX_' + key] = input('REFLEX_' + key + ': ')
        self.cfg_save()

    ############################################################
    def set_cli(self, argv, args): # pylint: disable=unused-argument
        match = re.match(r'^([a-zA-Z_-]+)\s*=\s*(.*)$', " ".join(argv))
        if not match:
            self.NOTIFY("set var=value")
            return
        param = match.group(1)
        if match.group(2):
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
        """dump the rfx config"""
        altcfg = self.cfg
        for line in altcfg:
            self.NOTIFY(line + "=" + altcfg[line])

    ############################################################
    def get_cli(self, argv, args): # pylint: disable=unused-argument
        """dump the rfx config"""
        self.timestamp = False
        if argv:
            self.OUTPUT(self.cfg[argv[0]])
        else:
            for line in self.cfg:
                self.OUTPUT("export " + line + "=" + self.cfg[line])

    ############################################################
    def apikey_cli(self, argv, args, cli):
        action = args.get('action')
        target = None
        if argv:
            target = argv[0]

        try:
            getattr(self, "apikey_cli__" + action)(target, cli)
        except rfx.CannotContinueError as err:
            err = str(err)
            if ": 401" in err:
                err = err + " Unauthorized"
            self.ABORT(err)

    ############################################################
    # pylint: disable=no-self-use,unused-argument
    def apikey_cli__delete(self, target, cli):
        self.rcs.delete("apikey", target)

    ############################################################
    def apikey_cli__list(self, target, cli):
        ecli = EngineCli(base=self)
        ecli.list_cli("apikey", {'--show': 'name'}, [])

    ############################################################
    def apikey_cli__create(self, target, cli):
        try:
            self.rcs.create("apikey", {"name":target})
            apikey = self.rcs.get('apikey', target)
            if not apikey:
                raise Exception("Unable to get object!")
            self.NOTIFY("new apikey:\n\n\t{}.{}"
                        .format(apikey.get('name', 'invalid'),
                                apikey.get('secrets', ['invalid'])[0]))
        except Exception: # pylint: disable=broad-except
            self.NOTIFY("Unable to properly create apikey!")
            if self.do_DEBUG():
                self.NOTIFY(traceback.format_exc(0))
                self.NOTIFY("(try --debug=* for more info)")
            else:
                self.NOTIFY(traceback.format_exc())
