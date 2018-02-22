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
Common repository for back-end / Reflex Engine
"""

import os
import time
import stat # chmod
import subprocess
import re
import logging
import sys
import traceback
import datetime
import dateparser
try:
    from builtins import input # pylint: disable=redefined-builtin
    get_input = input # pylint: disable=invalid-name
except: # pylint: disable=bare-except
    get_input = raw_input # pylint: disable=invalid-name, undefined-variable

import ujson
import dictlib
import rfx
import rfx.tabulate
from rfx import NotFoundError, threadlock, json4human
import rfx.client

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

def parse_dates(input_str):
    """
    Accept a string of two posix dates separated by ~,
    Return two datetime objects
    """
    if not input_str:
        return None
    split = re.split(r'\s*~\s*', input_str + "~")[0:2]
    start = dateparser.parse(split[0])
    if not start:
        raise ValueError("Invalid date: " + split[0])
    end = dateparser.parse(split[1] or 'now')
    if not end:
        raise ValueError("Invalid date: " + split[1])

    return dict(start=start.timestamp(), end=end.timestamp())

################################################################
class Engine(rfx.Base):
    """
    Reflex Engine Class - Reflex Engine
    """

    # used for speeding up repeated queries
    cache = {}
    session = None

    ###########################################################################
    def __init__(self, **kwargs):
        super(Engine, self).__init__(**kwargs)
        base = kwargs.get('base')
        if base:
            rfx.Base.__inherit__(self, base)

        # adjust requests library logging
        if self.do_DEBUG():
            logging.getLogger("requests").setLevel(logging.DEBUG)
        else:
            logging.getLogger("requests").setLevel(logging.WARNING)

        self.DEBUG("Engine.__init__(base={0})".format(base))

        if not 'REFLEX_URL' in self.cfg.keys():
            self.ABORT("Unable to find 'REFLEX_URL' in config")

        self.session = rfx.client.Session(debug=self.debug)
        rfx.Base.__inherit__(self.session, base)

    ###########################################################################
    # pylint: disable=unused-argument
    def list_objects(self, obj_type, apikey=None):
        """Uses build-in environmental connection information"""
        return self.session.list(obj_type)

    ###########################################################################
    # pylint: disable=unused-argument,too-many-arguments
    def create_object(self, obj_type, payload, reason=None, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.create(obj_type, payload)

    ###########################################################################
    def delete_object(self, obj_type, obj_target, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.delete(obj_type, obj_target)

    ###########################################################################
    # pylint: disable=too-many-arguments
    def delta_update_object(self, obj_type, obj_target, payload, **kwargs):
        """Wraps update_object and makes a dict_union change"""
        return self.session.patch(obj_type, obj_target, payload)

    ###########################################################################
    # pylint: disable=too-many-arguments
    def update_object(self, obj_type, obj_target, payload, reason=None, apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        return self.session.update(obj_type, obj_target, payload)

    ###########################################################################
    def get_object(self, obj_type, obj_target, **kwargs): #apikey=None, notify=True):
        """Uses build-in environmental connection information"""
        if kwargs.get('raise_error') is True:
            return self.session.get(obj_type, obj_target)
        else:
            try:
                return self.session.get(obj_type, obj_target)
            except: # pylint: disable=bare-except
                return None

    ###########################################################################
    @threadlock
    def cache_get_object(self, obj_type, obj_target, **kwargs):
        """
        Cache wrapper around Engine.get_object, return None on failed lookup.
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
        Cache wrapper around Engine.update_object, invalidates cache for that object
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
        Cache wrapper around Engine.list_object.
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
def _valid_name(obj_name):
    if not obj_name:
        obj_name = ''
    if re.search(r'[^a-z0-9_.-]', obj_name, flags=re.IGNORECASE):
        return "Invalid name ({}), May only contain a-z0-9_-" \
               .format(obj_name)
    return None

###############################################################################
class EngineCli(rfx.Base):
    """Basic CLI for Engine Class"""

    ###########################################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if base:
            rfx.Base.__inherit__(self, base)
        self.rcs = rfx.client.Session(debug=self.debug, base=self)

    ###########################################################################
    # pylint: disable=too-many-locals, too-many-branches, too-many-statements
    def list_cli(self, obj_type, parsed, argv):
        """list an object.  see --help"""
        try:
            archive = parse_dates(parsed.get('--archive'))
        except ValueError as err:
            self.ABORT(str(err))

        # if it is a real regex, we must reduce scope on our side
        filter_re = None
        name = None
        if argv and argv[0]:
            if re.search(r'[^a-z0-9.-]', argv[0]):
                try:
                    filter_re = re.compile(argv[0])
                except Exception as err: # pylint: disable=broad-except
                    self.ABORT("Invalid regular expression: {}\n\t{}".format(argv[0], str(err)))
            else:
                name = argv[0]

        limit_expr = None
        if parsed.get('--expression'):
            limit_expr = parsed['--expression']

        stdout = list()
        stderr = list()
        show = list() # uppercase header
        if parsed.get('--stderr'):
            stderr = [x.lower() for x in re.split(r"\s*,\s*", parsed['--stderr'])]
        if parsed.get('--stdout'):
            stdout = [x.lower() for x in re.split(r"\s*,\s*", parsed['--stdout'])]

        if parsed.get('--show'):
            show = [x.lower() for x in re.split(r"\s*,\s*", parsed['--show'])]
            if not stderr and not stdout:
                stderr = [x for x in range(0, len(show)) if show[x] != "name"]
        elif stderr or stdout:
            show = stdout + stderr

        if not show:
            show = ['name', 'id']
            if archive:
                show += ['updated_at']

        cols = show.copy()
        ncols = len(cols)

        # find out any sub.key references -- we can only request the top level
        subs = dict()
        for pos in range(len(cols)): # pylint: disable=consider-using-enumerate
            if '.' in cols[pos]:
                elem = cols[pos]
                top = elem[0:elem.index('.')]
                sub = elem[elem.index('.')+1:]
                subs[pos] = sub
                cols[pos] = top

        cols = [x.lower() for x in cols]
        results = [[x.upper() for x in show]]

        try:
            objs = self.rcs.list(obj_type, cols=cols, match=name, archive=archive)
        except rfx.client.ClientError as err:
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
            obj_lower = dict()
            for key, value in obj.items():
                obj_lower[key.lower()] = value

            row = list()
            for key_x in range(0, ncols):
                if cols[key_x] == 'updated_at':
                    posix_time = int(obj_lower[cols[key_x]])
                    val = "{} ({})".format(posix_time,
                                           datetime.datetime.fromtimestamp(posix_time))
                    row.append(val)
                else:
                    try:
                        if subs and subs.get(key_x):
                            row.append(dictlib.dig(obj_lower[cols[key_x]], subs[key_x]))
                        else:
                            row.append(obj_lower[cols[key_x]])
                    except (KeyError, TypeError):
                        row.append('')
            results.append(row)

        rfx.tabulate.cols(results, stderr=stderr, header=True, fmt=parsed.get('--format', 'txt'))

    ###########################################################################
    def copy_cli(self, obj_type, obj_source, obj_dest):
        """copy an object.  see --help"""
        try:
            self.rcs.get(obj_type, obj_dest)
            self.ABORT("Destination object '{0}' already exists!".format(obj_dest))
        except rfx.client.ClientError:
            pass

        try:
            obj = self.rcs.get(obj_type, obj_source)
        except rfx.client.ClientError as err:
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
        try:
            archive = parse_dates(parsed.get('--archive'))
        except ValueError as err:
            self.ABORT(str(err))
        obj_name = parsed['name']
        try:
            obj = self.rcs.get(obj_type, obj_name, archive=archive)
            if argv and argv[0]:
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
        except rfx.client.ClientError as err:
            #self.ABORT("Exception: " + traceback.format_exc())
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
        content = self._get_default(obj_type, obj_name, parsed.get('--content'))
        self.rcs.update(obj_type, obj_name, content)
        self.get_cli(obj_type, parsed, [])

    ###########################################################################
    # pylint: disable=unused-argument
    def delete_cli(self, obj_type, parsed, argv):
        """delete an object.  see --help"""
        obj_name = parsed['name']
        try:
            result = self.rcs.delete(obj_type, obj_name)
            if result['status'] == 'deleted':
                self.NOTIFY("Deleted " + obj_type.capitalize() + " '" + obj_name + "'")
            else:
                self.NOTIFY("Unable to delete: {}".format(result))
        except Exception as err: # pylint: disable=broad-except
            self.ABORT("Cannot delete object '{0}': {1}".format(obj_name, err))

    ###########################################################################
    # pylint: disable=unused-argument
    def edit_cli(self, obj_type, parsed, argv, pause=True):
        """edit a commplete object.  see --help"""
        obj_name = parsed['name']
        msg = _valid_name(obj_name)
        if msg:
            self.ABORT(msg)

        #if re.search("[^a-z0-9_-]", obj_name, flags=re.IGNORECASE):
        #    self.ABORT("Invalid {0} name ({1}), May only contain a-z0-9_-"
        #               .format(obj_type, obj_name))
        scratchdir = os.path.expanduser("~") + "/.rfx.scratch"
        if not os.path.exists(scratchdir):
            os.mkdir(scratchdir)
        os.chmod(scratchdir, stat.S_IRWXU)
        localfile = scratchdir + "/" + obj_type + "." + obj_name + ".json"

        try:
            content = self.rcs.get(obj_type, obj_name)
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
            if str(err) == "Forbidden":
                self.ABORT(str(err))
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

        answer = get_input("Commit changes? [y] ")

        # incase of a rename
        data_name = data.get('name', None)
        if data_name is None:
            data['name'] = obj_name
        else:
            obj_name = data_name

        if not answer or re.match("^(yes|y)$", answer, flags=re.IGNORECASE):
            try:
                self.rcs.update(obj_type, obj_name, data)
            except rfx.client.ClientError as err:
                self.ABORT(str(err))

    ###########################################################################
    # pylint: disable=unused-argument
    def update_cli(self, obj_type, parsed, argv):
        """update a commplete object.  see --help"""
        obj_name = parsed['name']
        data = self._load_content(obj_name, parsed.get('--content'))
        if data:
            self.rcs.update(obj_type, obj_name, data)
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
        extracted_objs = dict()

        for obj in Engine(base=self).session.list(obj_type, cols='*'):
            if not name_rx.search(obj['name']):
                continue
            context = {'obj': dictlib.Obj(**obj), 'True': True, 'False': False}
            try:
                # pylint: disable=eval-used
                if not eval(limit, {'__builtins__':{}, 'rx': re}, context):
                    continue
            except KeyError:
                print("key error")
                continue
            except: # pylint: disable=bare-except
                traceback.print_exc()

            value = dictlib.dig_get(obj, extract)
            if not value:
                continue

            if isinstance(value, list):
                extracted = extracted.union(value)
            else:
                extracted.add(value)

            extracted_objs[obj['name']] = value

        if parsed.get('--format', 'txt') == 'txt':
            self.OUTPUT(" ".join(extracted) + "\n")
        if parsed.get('--format', 'txt') == 'list':
            # pylint: disable=consider-iterating-dictionary
            for obj in sorted(extracted_objs.keys()):
                self.OUTPUT("{}: {}".format(obj, extracted_objs[obj]))
        else:
            self.OUTPUT(ujson.dumps(extracted) + "\n")

    ###########################################################################
    # pylint: disable=unused-argument
    def merge_cli(self, obj_type, parsed, argv):
        """merge data into a current object.  see --help"""
        obj_name = parsed['name']
        data = self._load_content(obj_name, parsed.get('--content'))
        try:
            data = self.union_dict(self.rcs.get(obj_type, obj_name), data)
        except Exception as err: # pylint: disable=broad-except
            self.DEBUG("Excpeption: " + str(err))
            self.ABORT("Cannot find object '" + obj_name + "'")

        self.rcs.update(obj_type, obj_name, data)

    ###########################################################################
    def _json_load_handle_error(self, content, ask2continue=False, cleanfile=None):
        try:
            data = ujson.loads(content)
            msg = _valid_name(data.get('name'))
            if msg:
                raise ValueError(msg)
            return data
        except ValueError as err:
            if content:
                linenbr = 1
                for line in content.split("\n"):
                    self.NOTIFY("{0:3}: {1}".format(linenbr, line))
                    linenbr += 1
            self.NOTIFY("Unable to parse: " + str(err))
            if ask2continue:
                answer = get_input("Return to editor? [y] ")
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
            data = {}

        content = self._load_content(obj_name, obj_content, read=read)
        if content:
            data = self.union_dict(data, content)
        return data

################################################################################
class Release(rfx.Base):
    """Records of the software build/release/package process"""

    obj = None
    name = ''
    engine = None
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
            rfx.Base.__inherit__(self, kwargs['base'])
        self.engine = Engine(base=self)

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
                self.obj = self.engine.get_object("release", self.name, notify=False)
            except rfx.NotFoundError as err:
                if abort:
                    self.ABORT(str(err[0])) # pylint: disable=unsubscriptable-object

    ############################################################################
    def update(self):
        """push an update of the object"""
        if self.obj:
            self.engine.update_object('release', self.name, self.obj, notify=False)
        else:
            self.ABORT("No object to update")

    ############################################################################
    def status_cli(self, app, version, args):
        """report or change the status of a release"""
        if not version:
            if args.setkey:
                self.ABORT("Cannot set a status without a version")

            # NOTE: not efficient, fix Reflex Engine itself and come fix this
            re_rel = re.compile("^" + app)
            count = 0
            for obj in self.engine.list_objects('release'):
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
            self.engine.delete_object('release', self.name)

