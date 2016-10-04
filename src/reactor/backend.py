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
"""
Common repository for back-end / Reactor Core
"""

import os
import time
import stat # chmod
import subprocess
import re
import logging
import sys
import traceback
from builtins import input # pylint: disable=redefined-builtin
import requests
import ujson
import dictlib
import reactor
import reactor.tabulate
from reactor import NotFoundError, CannotContinueError, threadlock, json4human
import reactor.client

################################################################
# helper wraps around a conf object
def getconf(cfg, *key):
    """Helper for nice error handling"""
    try:
        return _getconf(cfg, *key)
    except KeyError:
        raise ValueError("Cannot find config " + ".".join(key))

def _getconf(cfg, *key):
    """Recursive Version"""
    if len(key) == 1:
        return cfg[key[0]]
    return _getconf(cfg[key[0]], *key[1:])

################################################################
class Core(reactor.Base):
    """
    Reactor Core Class - Reactor Core
    """

    # used for speeding up repeated queries
    cache = {}
    session = None

    ###########################################################################
    def __init__(self, **kwargs):
        super(Core, self).__init__(**kwargs)
        base = kwargs.get('base')
        if base:
            reactor.Base.__inherit__(self, base)

        # adjust requests library logging
        if self.do_DEBUG():
            logging.getLogger("requests").setLevel(logging.DEBUG)
        else:
            logging.getLogger("requests").setLevel(logging.WARNING)

        self.DEBUG("Core.__init__(base={0})".format(base))

        if not 'REACTOR_URL' in self.cfg.keys():
            self.ABORT("Unable to find 'REACTOR_URL' in config")

        self.session = reactor.client.Session(debug=self.debug)
        reactor.Base.__inherit__(self.session, base)

    ###########################################################################
    def list_objects(self, obj_type, apikey=None):
        """Uses build-in environmental connection information"""
        return self.session.list(obj_type)

        #headers = self.apikey_header(apikey)
        res = requests.get(self.get_base_url(obj_type) + "?limit=1000000",
                           auth=(self.cfg['REACTOR_TOKEN'], self.cfg['REACTOR_SECRET']),
                           headers=headers
                          )
        if res.status_code == 200:
            return res.json()
        elif res.status_code == 401:
            raise CannotContinueError("Cannot list {0}: Unauthorized (Is your APIKEY correct?)"
                                      .format(obj_type.capitalize()))
        else:
            raise CannotContinueError("Cannot list {0}: {1}".format(obj_type.capitalize(),
                                                                    res.status_code))

    ###########################################################################
    # pylint: disable=unused-argument,too-many-arguments
    def create_object(self, obj_type, payload, reason=None, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.create(obj_type, payload)

        headers = self.apikey_header(apikey)

        res = requests.post(self.get_base_url(obj_type),
                            auth=(self.cfg['REACTOR_TOKEN'], self.cfg['REACTOR_SECRET']),
                            headers=headers,
                            data=ujson.dumps(payload, escape_forward_slashes=False))
        if res.status_code == 200 or res.status_code == 201:
            if notify:
                self.NOTIFY("Created " + obj_type.capitalize() + " '" + payload['name'] + "'")
            return res.json()
        else:
            raise CannotContinueError("{0} '{1}' cannot create: {2}".format(obj_type.capitalize(),
                                                                            payload['name'],
                                                                            res.status_code))

    ###########################################################################
    def delete_object(self, obj_type, obj_target, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.delete(obj_type, obj_target)

        headers = self.apikey_header(apikey)

        res = requests.delete(self.get_base_url(obj_type) + obj_target,
                              auth=(self.cfg['REACTOR_TOKEN'], self.cfg['REACTOR_SECRET']),
                              headers=headers
                             )
        if res.status_code == 201 or res.status_code == 200:
            if notify:
                self.NOTIFY("Deleted " + obj_type.capitalize() + " '" + obj_target + "'")
            return True
        else:
            self.OUTPUT(res.content)
            raise CannotContinueError("Failed to Delete " + obj_type.capitalize() +
                                      " '" + obj_target + "'")

    ###########################################################################
    # pylint: disable=too-many-arguments
    def delta_update_object(self, obj_type, obj_target, payload, **kwargs):
        """Wraps update_object and makes a dict_union change"""
        return self.session.patch(obj_type, obj_target, payload)

        obj = self.union_dict(self.get_object(obj_type, obj_target, **kwargs), payload)
        self.update_object(obj_type, obj_target, obj, **kwargs)

    ###########################################################################
    # pylint: disable=too-many-arguments
    def update_object(self, obj_type, obj_target, payload, reason=None, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.update(obj_type, obj_target, payload)

        headers = self.apikey_header(apikey)
        payload["name"] = obj_target
        url = self.get_base_url(obj_type)
        update_type = "Create"
        try:
            self.get_object(obj_type, obj_target, notify=notify, apikey=apikey)
            url += obj_target
            update_type = "Update"
        except NotFoundError: # pylint: disable=bare-except
            pass

        self.DEBUG("HTTP POST: " + url, module="http", data=payload)
        res = requests.post(url,
                            auth=(self.cfg['REACTOR_TOKEN'], self.cfg['REACTOR_SECRET']),
                            headers=headers,
                            data=ujson.dumps(payload, escape_forward_slashes=False))
        self.DEBUG("HTTP Response", module="http", data=res)
        msg = update_type + " " + obj_type.capitalize() + ": " + obj_target
        if reason:
            msg = msg + " - " + reason
        if res.status_code == 201 or res.status_code == 200:
            if notify:
                self.NOTIFY(msg)
            return True
        else:
            if notify:
                self.NOTIFY("Failed to " + msg)
            self.OUTPUT("HTTP RESULT={0}: {1}".format(res.status_code, res.content))
            return False

    ###########################################################################
    def get_object(self, obj_type, obj_target, **kwargs): #apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.get(obj_type, obj_target)

        headers = self.apikey_header(kwargs.get('apikey', None))

        res = requests.get(self.get_base_url(obj_type) + obj_target,
                           auth=(self.cfg['REACTOR_TOKEN'], self.cfg['REACTOR_SECRET']),
                           headers=headers)
        if res.status_code == 200:
            if kwargs.get('notify', True):
                self.NOTIFY("Found " + obj_type.capitalize() + " '" + obj_target + "'")
            return res.json()
        else:
            raise NotFoundError(obj_type.capitalize() + " '" + obj_target + "' not found", 'objnf')

    ###########################################################################
    @threadlock
    def cache_get_object(self, obj_type, obj_target, **kwargs):
        """
        Cache wrapper around Core.get_object, return None on failed lookup.
        Optionally re-enable error throwing
        """
        raise_error = kwargs.get('raise_error', True)
        if 'raise_error' in kwargs:
            del kwargs['raise_error']
        if obj_type in self.cache:
            if obj_target in self.cache[obj_type]:
                return self.cache[obj_type][obj_target]
        else:
            self.cache[obj_type] = {}
        try:
            obj = self.get_object(obj_type, obj_target, **kwargs)
        except NotFoundError:
            self.cache[obj_type][obj_target] = None
            if raise_error:
                raise
            return None
        self.cache[obj_type][obj_target] = obj
        return obj

    ###########################################################################
    @threadlock
    def cache_update_object(self, obj_type, obj_target, payload, **kwargs):
        """
        Cache wrapper around Core.update_object, invalidates cache for that object
        """
        value = self.update_object(obj_type, obj_target, payload, **kwargs)
        if value:
            if obj_type in self.cache:
                if obj_target in self.cache[obj_type]:
                    del self.cache[obj_type][obj_target]
        return value

    ###########################################################################
    @threadlock
    def cache_list_objects(self, obj_type, **kwargs):
        """
        Cache wrapper around Core.list_object.
        """
        if not obj_type in self.cache:
            self.cache[obj_type] = {}

        objs = self.list_objects(obj_type, **kwargs)
        for obj in objs:
            oname = obj.get('name')
            if oname:
                self.cache[obj_type][oname] = obj

        return objs

    ###########################################################################
    @threadlock
    def cache_reset(self):
        """Clear the cache"""
        self.cache = {}


###############################################################################
class CoreCli(reactor.Base):
    """Basic CLI for Core Class"""

    ###########################################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if base:
            reactor.Base.__inherit__(self, base)

    ###########################################################################
    # pylint: disable=too-many-locals, too-many-branches, too-many-statements
    def list_cli(self, obj_type, parsed, argv):
        """list an object.  see --help"""
        filter_re = None
        if argv and len(argv[0]):
            filter_re = re.compile(argv[0])
        limit_expr = None
        if parsed.get('--expression'):
            limit_expr = parsed['--expression']

        stdout = list()
        stderr = list()
        show = list() # uppercase header
        cols = list()
        if parsed.get('--stderr'):
            stderr = [x.lower() for x in re.split(r"\s*,\s*", parsed['--stderr'])]
        if parsed.get('--stdout'):
            stdout = [x.lower() for x in re.split(r"\s*,\s*", parsed['--stdout'])]

        if parsed.get('--show'):
            show = [x.lower() for x in re.split(r"\s*,\s*", parsed['--show'])]
            if not stderr and not stdout:
                stderr = [x for x in range(0, len(show)) if show[x] != "name"]
            cols = show
        elif stderr or stdout:
            show = stdout + stderr

        if not show:
            show = ['name', 'id']

        show = [x.upper() for x in show]
        new = [show]

        try:
            rcs = reactor.client.Session(debug=self.debug, base=self)
            objs = rcs.list(obj_type, cols=cols, match=limit_expr)
        except reactor.client.ClientError as err:
            self.ABORT(str(err))

        for obj in objs:
            if filter_re and not filter_re.search(obj['name']):
                continue
            if limit_expr:
                try:
                    context = {'obj': dictlib.Obj(**obj), 'True': True, 'False': False}
                    # pylint: disable=eval-used
                    if not eval(limit_expr, {'__builtins__':{}, 'rx': re}, context):
                        continue
                except KeyError:
                    continue
                except SyntaxError:
                    self.ABORT(traceback.format_exc(0))
                except: # pylint: disable=bare-except
                    self.ABORT(traceback.format_exc())

            # make it insensitive
            objup = dict()
            for key, value in obj.items():
                objup[key.upper()] = value

            row = list()
            for key in show:
                if objup.get(key):
                    row.append(objup[key])
                else:
                    row.append('')
            new.append(row)

        reactor.tabulate.cols(new, stderr=stderr, header=True)

    ###########################################################################
    def copy_cli(self, obj_type, obj_source, obj_dest):
        """copy an object.  see --help"""
        try:
            Core(base=self).get_object(obj_type, obj_dest)
            self.ABORT("Destination object '{0}' already exists!".format(obj_dest))
        except reactor.client.ClientError:
            pass

        try:
            obj = Core(base=self).get_object(obj_type, obj_source)
        except reactor.client.ClientError as err:
            self.ABORT(str(err))

        if 'id' in obj:
            del obj['id']

        self.OUTPUT("Copy {0} {1} to {2}".format(obj_type, obj_source, obj_dest))
        obj['name'] = obj_dest
        parsed = {'--content': json4human(obj), 'name': obj_dest}
        return self.edit_cli(obj_type, parsed, [], pause=False)

    ###########################################################################
    def get_cli(self, obj_type, parsed, argv):
        """get an object.  see --help"""
        obj_name = parsed['name']
        try:
            obj = Core(base=self).get_object(obj_type, obj_name)
            if argv and len(argv[0]):
                key = argv[0]
                if key[:4] == 'obj.':
                    key = key[4:]
                value = dictlib.dig_get(obj, key, '')
                if parsed.get('--format') == 'txt':
                    if isinstance(value, str):
                        self.OUTPUT(value)
                    elif isinstance(value, list):
                        self.OUTPUT(", ".join(value))
                    else:
                        self.OUTPUT(json4human(value))
                else:
                    self.OUTPUT(json4human(value))
            else:
                self.OUTPUT(json4human(obj))
        except reactor.client.ClientError as err:
            self.ABORT(str(err))
        except NotFoundError:
            self.ABORT("Cannot find object '" + obj_name + "'")
        except Exception as err: # pylint: disable=broad-except
            self.DEBUG("Exception: " + str(err))
            self.ABORT("Exception: " + traceback.format_exc())

    ###########################################################################
    # pylint: disable=unused-argument
    def create_cli(self, obj_type, parsed, argv):
        """create an object.  see --help"""
        obj_name = parsed['name']
        dbo = Core(base=self)
        content = self._get_default(obj_type, obj_name, parsed.get('--content'))
        dbo.update_object(obj_type, obj_name, content)
        self.get_cli(obj_type, parsed, [])

    ###########################################################################
    # pylint: disable=unused-argument
    def delete_cli(self, obj_type, parsed, argv):
        """delete an object.  see --help"""
        obj_name = parsed['name']
        try:
            Core(base=self).delete_object(obj_type, obj_name)
        except Exception as err: # pylint: disable=broad-except
            self.ABORT("Cannot delete object '{0}': {1}".format(obj_name, err))

    ###########################################################################
    # pylint: disable=unused-argument
    def edit_cli(self, obj_type, parsed, argv, pause=True):
        """edit a commplete object.  see --help"""
        obj_name = parsed['name']
        if re.search("[^a-z0-9-]", obj_name, flags=re.IGNORECASE):
            self.ABORT("Invalid {0} name ({1}), May only contain a-z0-9-"
                       .format(obj_type, obj_name))
        scratchdir = os.path.expanduser("~") + "/.reactor.scratch"
        if not os.path.exists(scratchdir):
            os.mkdir(scratchdir)
        os.chmod(scratchdir, stat.S_IRWXU)
        localfile = scratchdir + "/" + obj_type + "." + obj_name + ".json"
        dbo = Core(base=self)

        try:
            content = dbo.get_object(obj_type, obj_name)
            for key in ('updated_at', 'updated_by', 'created_at'):
                if key in content:
                    del content[key]
            with open(localfile, 'wt') as write_fd:
                write_fd.write(ujson.dumps(content,
                                           indent=2,
                                           sort_keys=True,
                                           escape_forward_slashes=False))

        except Exception as err: # pylint: disable=broad-except
            self.DEBUG("Exception: " + str(err))
            self.NOTIFY("Object '{0}' does not exist, creating new...".format(obj_name))
            if pause:
                time.sleep(1)
            with open(localfile, 'w') as write_fd:
                data = self._get_default(obj_type,
                                         obj_name,
                                         parsed.get('--content'),
                                         read=False)
                write_fd.write(ujson.dumps(data,
                                           indent=2,
                                           sort_keys=True,
                                           escape_forward_slashes=False))

        data = None
        while data is None:
            try:
                editor = os.environ['EDITOR']
            except: # pylint: disable=bare-except
                editor = 'vim'

            try:
                subprocess.call([editor, localfile])
            except OSError as err:
                self.ABORT("Unable to run editor '{}':\n\t{}"
                           .format(editor, err))

            with open(localfile, 'r') as read_fd:
                data = self._json_load_handle_error(read_fd.read(),
                                                    ask2continue=True,
                                                    cleanfile=localfile)

        os.unlink(localfile)

        answer = input("Commit changes? [y] ")
        if not len(answer) or re.match("^(yes|y)$", answer, flags=re.IGNORECASE):
            try:
                dbo.update_object(obj_type, obj_name, data)
            except reactor.client.ClientError as err:
                self.ABORT(str(err))

    ###########################################################################
    # pylint: disable=unused-argument
    def update_cli(self, obj_type, parsed, argv):
        """update a commplete object.  see --help"""
        obj_name = parsed['name']
        data = self._load_content(obj_name, parsed.get('--content'))
        if data:
            Core(base=self).update_object(obj_type, obj_name, data)
        else:
            self.ABORT("No content to update?")

    ###########################################################################
    # pylint: disable=unused-argument
    def slice_cli(self, obj_type, parsed, argv):
        """slice limit-expression extract-keys (comma delimited). see --help"""

        name_rx = re.compile('^' + parsed['name-filter'])
        limit = parsed['limit-expression']
        extract = parsed['key']
        if extract[:4] == "obj.":
            extract = extract[4:]

        extracted = set()

        for obj in Core(base=self).list_objects(obj_type):
            if not name_rx.search(obj['name']):
                continue
            context = {'obj': dictlib.Obj(**obj), 'True': True, 'False': False}
            try:
                # pylint: disable=eval-used
                print("? {}".format(limit))
                if not eval(limit, {'__builtins__':{}, 'rx': re}, context):
                    print("not for " + obj['name'])
                    continue
            except KeyError:
                print("key error")
                continue
            except: # pylint: disable=bare-except
                traceback.print_exc()

            self.NOTIFY("Matched '{}'".format(obj['name']))

            value = dictlib.dig_get(obj, extract)
            if not value:
                continue

            if isinstance(value, list):
                extracted = extracted.union(value)
            else:
                extracted.add(value)

        if parsed.get('--format', 'txt') == 'txt':
            self.OUTPUT(" ".join(extracted))
        else:
            self.OUTPUT(ujson.dumps(extracted))

    ###########################################################################
    # pylint: disable=unused-argument
    def merge_cli(self, obj_type, parsed, argv):
        """merge data into a current object.  see --help"""
        obj_name = parsed['name']
        data = self._load_content(obj_name, parsed.get('--content'))
        dbo = Core(base=self)
        try:
            data = self.union_dict(dbo.get_object(obj_type,
                                                  obj_name,
                                                  notify=False), data)
        except Exception as err: # pylint: disable=broad-except
            self.DEBUG("Excpeption: " + str(err))
            self.ABORT("Cannot find object '" + obj_name + "'")

        dbo.update_object(obj_type, obj_name, data)

    ###########################################################################
    def _json_load_handle_error(self, content, ask2continue=False, cleanfile=None):
        try:
            return ujson.loads(content)
        except ValueError as err:
            if content:
                linenbr = 1
                for line in content.split("\n"):
                    self.NOTIFY("{0:3}: {1}".format(linenbr, line))
                    linenbr += 1
            self.NOTIFY("Unable to parse: " + str(err))
            if ask2continue:
                answer = input("Return to editor? [y] ")
                if not re.match("^(no|n)$", answer, flags=re.IGNORECASE):
                    return None
            if cleanfile:
                os.unlink(cleanfile)
            self.ABORT("Cannot continue.")

    ###########################################################################
    # pylint: disable=unused-argument
    def _load_content(self, obj_name, obj_content, read=True):
        if not obj_content and read:
            self.NOTIFY("Reading JSON data from stdin...")
            obj_content = ''
            for line in sys.stdin.readlines():
                obj_content += line
        if obj_content:
            return self._json_load_handle_error(obj_content)
        return {}

    ###########################################################################
    # NOTE: merge w/newapp / populate code
    def _get_default(self, obj_type, obj_name, obj_content, read=True):
        try:
            data = getattr(self, "_get_default_" + obj_type)(obj_name)
        except: # pylint: disable=bare-except
            data = {"_r_":{"v":1}}

        content = self._load_content(obj_name, obj_content, read=read)
        if content:
            data = self.union_dict(data, content)
        return data

################################################################################
class Release(reactor.Base):
    """Records of the software build/release/package process"""

    obj = None
    name = ''
    core = None
    statuses = {
        "prep": "skipped",
        "compile": "skipped",
        "test-unit": "incomplete",
        "assemble": "incomplete",
        "test-integration": "incomplete",
        "test-run": "skipped",
        "test-functional": "skipped",
        "finish": "skipped"
    }
    status_vals = {
        "incomplete": 1,
        "started": 1,
        "success": 1,
        "failure": 1,
        "skipped": 1
    }

    ############################################################################
    def __init__(self, *args, **kwargs):
        super(Release, self).__init__(*args, **kwargs)
        if 'base' in kwargs:
            reactor.Base.__inherit__(self, kwargs['base'])
        self.core = Core(base=self)

    ############################################################################
    def can_edit(self, app, version, args):
        """check if the object is changeable or not, ABORT if not"""

        self.load(app, version)

        if not self.obj:
            self.obj = {
                'name': self.name,
                'application': app,
                'version': version,
                'type': args.type or 'deploy-pkg',
                'status': {},
                'state': 'pending'
            }

        if self.obj.get('state', 'pending') in ('failed', 'ok'):
            self.ABORT("Release is complete, cannot be changed")

    ############################################################################
    def load(self, app, version, abort=False):
        """Pull the described app/version onto the object"""
        if not self.name:
            self.name = (app + '-' + version).replace("_", "-").replace('.', '-')

        if not self.obj:
            try:
                self.obj = self.core.get_object("release", self.name, notify=False)
            except reactor.NotFoundError as err:
                if abort:
                    self.ABORT(str(err[0])) # pylint: disable=unsubscriptable-object

    ############################################################################
    def update(self):
        """push an update of the object"""
        if self.obj:
            self.core.update_object('release', self.name, self.obj, notify=False)
        else:
            self.ABORT("No object to update")

    ############################################################################
    def status_cli(self, app, version, args):
        """report or change the status of a release"""
        if not version:
            if args.setkey:
                self.ABORT("Cannot set a status without a version")

            # NOTE: not efficient, fix Reactor Core itself and come fix this
            re_rel = re.compile("^" + app)
            count = 0
            for obj in self.core.list_objects('release'):
                if not re_rel.search(obj['name']):
                    continue
                count += 1
                app = obj.get('application')
                version = obj.get('version')
                state = obj.get('state')
                self.NOTIFY("{:30} {:9} {}".format(app, version, state))
            self.NOTIFY("Total: {}".format(count))
            return

        if args.setkey:
            self.can_edit(app, version, args)
            # parse setkeys, update
            setkeys = []
            errors = 0
            for setkey in args.setkey:
                key, val = (setkey.lower() + "=").split("=")[:2]
                if not key in self.statuses:
                    self.NOTIFY("Invalid status '" + key + "', not one of:\n\t" +
                                ", ".join(self.statuses.keys()))
                    errors += 1
                elif not val in self.status_vals:
                    self.NOTIFY("Invalid status value '{}' for '{}', not one of:\n\t{}"
                                .format(val, key, ", ".join(self.status_vals.keys())))
                    errors += 1
                else:
                    setkeys.append([key, val])
            if errors:
                self.ABORT("Cannot continue")
            if setkeys:
                for key, val in setkeys:
                    self.obj['status'][key] = val
                    self.NOTIFY("set {}={}".format(key, val))
                self.update()
                return

        self.load(app, version, abort=True)
        self.OUTPUT(ujson.dumps(self.obj, indent=2, escape_forward_slashes=False))

    ############################################################################
    def link_cli(self, app, version, args):
        """link to a release"""
        self.can_edit(app, version, args)

        # could parseurl if we wanted...
        self.obj['link'] = args.link
        self.update()
        self.NOTIFY("link=" + args.link)

    ############################################################################
    def data_cli(self, app, version, args):
        """get or specify metadata for a release"""

        data = args.data
        if data == '-':
            self.NOTIFY("Reading JSON data from stdin...")
            data = sys.stdin.read()
        try:
            data = ujson.loads(data)
        except ValueError as err:
            self.ABORT("Unable to parse: " + str(err))

        self.can_edit(app, version, args)

        # could parseurl if we wanted...
        self.obj['data'] = data
        self.update()
        self.NOTIFY("data=" + ujson.dumps(data, indent=2, escape_forward_slashes=False))

    ############################################################################
    def finish_cli(self, app, version, args):
        """finish a release"""

        self.can_edit(app, version, args)
        status = self.obj.get('status', {})
        state = 'ok'
        for status_name in status:
            default = self.statuses.get(status_name)
            if status.get(status_name, default) not in ('ok', 'skipped'):
                state = 'failed'
                break

        self.obj['state'] = state
        self.update()

    ############################################################################
    # pylint: disable=unused-argument
    def delete_cli(self, app, version, args):
        """delete a release"""

        self.load(app, version)
        if self.obj:
            self.core.delete_object('release', self.name)

