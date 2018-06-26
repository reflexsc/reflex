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
# pylint: disable=anomalous-backslash-in-string,too-many-lines

"""
Object Abstraction Layer

Abstract our data to encourage loose coupling.
This avoids the anti-pattern of an ORM.

A few unit tests are embedded, however, most of them are external.
"""

import re
import uuid
import time
import base64
import hashlib # hashlib is fastest for hashing
import nacl.utils # for keys -- faster than cryptography lib
import nacl.pwhash # for password hashing
from mysql.connector.errors import ProgrammingError, IntegrityError, DatabaseError
import rfx
from rfx import json4store, json2data #, json4human
from rfxengine.exceptions import ObjectNotFound, NoArchive, ObjectExists,\
                                 NoChanges, InvalidParameter,\
                                 PolicyFailed
from rfxengine import abac, log, do_DEBUG#, trace
from rfxengine.db.pool import db_interface
from rfxengine.db.mxsql import row_to_dict
import dictlib

# todo: want to hook object and its relationships

################################################################################
class RCMap(dictlib.Obj):
    """
    Making the code readable.
    Defaults for instantiating.

    dtype=     # data type - "value" (default), dict or list (literals)
    stype=     # store type - "read" (default), "opt" or "alter"
    stored=    # string of name for storage, or "data" object
    encrypted= # is this a base table attribute, or is it stored in data column?
    hasid=     # if stored=!"data" and hasid=True the value has an id#,
               # extract and store it as well (stored + _id).
               # do not use if not in 1:1 relationship
    """

    # pylint: disable=too-many-arguments
    def __init__(self, dtype="value", stype="alter",
                 stored="data", encrypt=False, hasid=False, sensitive=False):

#DEV CHECK#        if os.environ.get('DEVELOPMENT'):
#DEV CHECK        if dtype not in ("value", dict, list):
#DEV CHECK            raise ValueError("Invalid RCMap dtype, not one of: \"value\", dict, array") # pylint: disable=line-too-long
#DEV CHECK        if stype not in ("read", "opt", "alter"):
#DEV CHECK            raise ValueError("Invalid RCMap stype, not one of: read, optional, alter") # pylint: disable=line-too-long

        super(RCMap, self).__init__(encrypt=encrypt,
                                    stored=stored,
                                    dtype=dtype,
                                    stype=stype,
                                    hasid=hasid,
                                    sensitive=sensitive)

################################################################################
# pylint: disable=too-many-public-methods
class RCObject(rfx.Base):
    """
    Common attributes for DSE Abstraction Layer

    Similar to an ORM, but we are doing it in our own code, keeping it light and
    focused on our needs and not over-burdening things.

    obj.load() / obj.dump() -- convert from/to json representation (as dict)
                            -- think import/export
    obj.get(target, dbi=X, date=Y)
    obj.update() # auto-archives, when there is an archive
    obj.create() # auto-archives, when there is an archive
    obj.delete()
    obj.list_buffered()
    obj.list_iterated()
    """

    table = ''
    archive = False
    foreign = True # do we allow foreign keys (i.e. undefined)
    master = None
    obj = None
    vardata = True
    start = 0
    reqid = 0

    ## mapping.  RCMap changes position to named.  Positions:
    ##           use on alter, use on create, extract from dict
    omap = None

    # does this object use policies?
    policy_map = True
    policies = None

    def __init__(self, *args, **kwargs):
        if not self.omap:
            self.omap = dictlib.Obj()

        self.omap['id'] = RCMap(stype="read", stored='id')
        self.omap['name'] = RCMap(stored='name')
        self.omap['updated_by'] = RCMap(stype="read", stored='updated_by')
        self.omap['updated_at'] = RCMap(stype="read", stored='updated_at')
        self.reqid = kwargs.get('reqid', 0)

        if kwargs.get('master'):
            self.master = kwargs['master']
            del kwargs['master']
            kwargs['base'] = self.master

        if kwargs.get('clone'):
            self.master = kwargs['clone'].master
            del kwargs['clone']
            kwargs['base'] = self.master

        super(RCObject, self).__init__(*args, **kwargs)

    ############################################################################
    def _get_policies(self, dbi=None):
        if not self.policy_map:
            return dict(read=list(), write=list(), admin=list())

        target_id = 0
        if self.obj and self.obj.get('id'):
            target_id = self.obj['id']
        cache = self.master.cache
        start = time.time() # so it is all matching the same time
        self.start = start
        key = self.table + "." + str(target_id)
        pmap = cache.get_cache('policymap', key)
        if not pmap:
            return self._get_policies_direct(target_id, key, cache, start, dbi=dbi)

        # if any single policy is expired, re-grab the entire set, as it has
        # dependent information
        for action in pmap:
            for policy in pmap[action]:
                if policy.expires <= start:
                    return self._get_policies_direct(target_id, key, cache, start, dbi=dbi)

        return pmap

    ############################################################################
    # pylint: disable=too-many-arguments
    def _get_policies_direct(self, target_id, key, cache, start, dbi=None):
        if target_id:
            result = dbi.do_getlist("""
                SELECT action, result, sort_order, id, name, policy, data, unix_timestamp(updated_at), target_id
                  FROM Policy, PolicyFor
                 WHERE id=policy_id AND obj = ? AND (target_id = ? OR target_id = 0)
              """, self.table, target_id)
        else:
            result = dbi.do_getlist("""
                SELECT action, result, sort_order, id, name, policy, data, unix_timestamp(updated_at), target_id
                  FROM Policy, PolicyFor
                 WHERE id=policy_id AND obj = ? AND target_id = 0
              """, self.table)

        pmap = dict(read=list(), write=list(), admin=list())
        for row in result:
            policy = abac.Policy(*row)
            policy.expires = cache.set_cache('policy',
                                             policy.policy_id,
                                             policy,
                                             base_time=start)
            pmap[policy.policy_action].append(policy)

        for ptype in pmap:
            if pmap[ptype]:
                pmap[ptype].sort(key=lambda elem: elem.sort_key, reverse=True)

        cache.set_cache('policymap', key, pmap, base_time=start)
        return pmap

    ############################################################################
    def name2id_direct(self, target, dbi):
        """
        Does an object exist?  Lightweight test, return obj ID or 0

        Accepts a string or integer.  If an int, it refers to an existing object.
        If a string, it accepts a name reference for an object (name.id).  If .id
        is an integer, that is given preference in looking up the object.
        """
        name = None
        if isinstance(target, str):
            name, obj_id = self.split_name2id(target)
            if obj_id:
                target = obj_id
            else:
                target = name

        def get_name(name):
            """helper"""
            return dbi.do_getone("SELECT ID, NAME FROM " + self.table +
                                 " WHERE name = ?", name, output=list)

        if isinstance(target, int):
            result = dbi.do_getone("SELECT ID, NAME FROM " + self.table +
                                   " WHERE id = ?", target, output=list)
            if not result and name:
                result = get_name(name)
        else:
            result = get_name(target)
        if result:
            return result
        return (0, target)

    ############################################################################
    # pylint: disable=no-self-use
    def split_name2id(self, target):
        """convert a conventional name reference to include the id"""
        if target and target[:1] in "0123456789":
            return ('', int(target))
        name, obj_id = (target + ".").split(".")[:2]
        if obj_id and obj_id[:1] in "0123456789":
            return (name, int(obj_id))
        return (name, 0)

#    ############################################################################
#    def exists(self, target, dbi=None):
#        """Does an object exist?  Lightweight test, return obj ID or 0"""
#        return self.name2id_direct(target, dbi)[0]

    ############################################################################
    def dump(self):
        """Put an object into its dict/JSON representation"""
        self.obj['updated_at'] = str(self.obj['updated_at']) # in datetime
        return self.obj # we now store in this format

    ############################################################################
    def load(self, data):
        """Get an object from its dict/JSON representation"""
        self.obj = data
        return self.validate()

    ############################################################################
    # pylint: disable=too-many-branches
    @db_interface
    def get(self, target, attrs, dbi=None, archive=None):
        """
        Get an object from the DB

        If a archive is specified, pull the archived version (value is the date)
        archive is only appropriate for tables supporting Archive.
        """
        sql = "SELECT * FROM " + self.table
        if isinstance(target, str):
#            trace("name2id")
            idnbr = self.name2id_direct(target, dbi)[0]
#            trace("name2id done")
        elif not isinstance(target, int):
            raise InvalidParameter("Invalid target type specified?")
        else:
            idnbr = target

        args = [idnbr]

#        trace("get_policies start")
        if attrs is not True: # special override
            self.obj = {'id': idnbr} # mockup ID so policies will match
            self.policies = self._get_policies(dbi=dbi)
#        trace("get_policies done")

        if archive:
            if not self.archive:
                raise NoArchive(self.table + " does not support archives")
            sql += 'Archive WHERE id=? AND updated_at = from_unixtime(?)'
            args.append(archive[0])
        else:
            sql += ' WHERE id=?'

#        trace("dbi do_getone start")
        dbin = dbi.do_getone(sql, *args)
#        trace("dbi do_getone done")

        if not dbin:
            raise ObjectNotFound("Unable to load {}: {}"
                                 .format(self.table, target))

#        trace("decode start")
        self.obj = self._get_decode(attrs, dbin)
#        trace("decode done")

#        trace("authorized? start")
        if attrs is not True:
            self.authorized("read", attrs, sensitive=False, raise_error=True)
#        trace("authorized? done")

        # are there any policies targeted to this object?
        return self

    ############################################################################
    def _get_decode(self, attrs, dbin, cols=None):
        """
        Decode an object from its db results.  Can be called with a separate
        list of columns we care about (to sub-scope and optimize).  Used by
        both getting the full object, and getting as a list.
        """
        data = dict()
        obj = dict()

        if self.vardata and dbin.get('data'):
            data = json2data(dbin['data'])

        foreign = set()
        if self.foreign:
            foreign = set(data.keys())

        # loop omap, if stored==data, extract
        for name, col in self.omap.items():
            if cols and name not in cols:
                continue

            if col.stored == "data":
                value = data.get(name, None)
            elif name == "updated_at":
                if dbin.get("unix_timestamp(updated_at)"):
                    value = dbin.get("unix_timestamp(updated_at)")
                else:
                    value = dbin.get("updated_at").timestamp()
            else:
                value = dbin.get(col.stored, None)

            if value is None:
                if col.stype == "opt":
                    continue
                raise InvalidParameter("Object load: missing '" + col.stored + "'")

            if col.encrypt:
                if attrs is True:
                    value = json2data(self.decrypt(value))
                elif not self.authorized("read", attrs, sensitive=True, raise_error=False):
                    value = {'encrypted': 'values'}
                elif isinstance(value, str) and value[:3] == '__$':
                    value = json2data(self.decrypt(value))

            elif col.sensitive:
                if attrs is True:
                    if col.dtype != "value":
                        value = json2data(value)
                elif self.authorized("write", attrs,
                                     sensitive=True, raise_error=False):
                    value = json2data(value)
                else:
                    value = ["redacted"]
            elif col.dtype != "value":
                value = json2data(value)

            foreign.discard(name)
            obj[name] = value

        for name in foreign:
            try:
                obj[name] = json2data(data.get(name))
            except ValueError:
                obj[name] = data.get(name)

        return obj

    ############################################################################
    @db_interface
    def delete(self, target, attrs, dbi=None):
        """
        Delete a specified object -- does not delete from Archive
        """
        self.policies = self._get_policies(dbi=dbi)
        if not self.authorized("write", attrs, sensitive=False, raise_error=False):
            raise PolicyFailed("Unable to get permission to delete object")

        obj_id = self.name2id_direct(target, dbi)[0]
        if obj_id:
            deleted = dbi.do_count("DELETE FROM " + self.table + " WHERE id = ?", obj_id)
        else:
            raise ObjectNotFound("Target not found")
        self.obj = dict(id=obj_id)
        self.deleted(attrs, dbi=dbi)

        return deleted

    ############################################################################
    @db_interface
    def list_buffered(self, attrs, dbi=None, limit=0, match=None, archive=None):
        """
        too much duplicate code, just redirected to list_buffered
        """
        return self.list_cols(attrs, ['name', 'id'],
                              dbi=dbi, limit=limit, match=match, archive=archive)

    ############################################################################
    # pylint: disable=too-many-locals,too-many-statements
    @db_interface
    def list_cols(self, attrs, cols, dbi=None, limit=0, match=None, archive=None):
        """
        List objects, using an iterator, with a specific called set of columns.
        """
        self.policies = self._get_policies(dbi=dbi)
        self.authorized("read", attrs, sensitive=False, raise_error=True)

        cols = set(cols)
        keys = set()
        args = []
        added_data = False
        if 'id' not in cols: # needed for get_policies
            cols.add('id')

#        if 'updated_at' in cols:
#            cols[cols.index('updated_at')] = 'unix_timestamp(updated_at)'

        # special case '*'
        if "*" in cols:
            cols = set(self.omap.keys())

        for item in cols:
            col = self.omap.get(item)
            if not col or col.stored == 'data':
                if not added_data:
                    keys.add("data")
                    added_data = True
            elif col == 'updated_at':
                keys.add('unix_timestamp(updated_at)')
            else:
                keys.add(col.stored)

        sql = "SELECT " + ",".join(keys) + " FROM " + self.table
        where = []
        if archive:
            if not self.archive:
                raise NoArchive(self.table + " does not support archives")
            sql += 'Archive'
            if archive[1] > archive[0]:
                args += [archive[1], archive[0]]
            else:
                args += [archive[0], archive[1]]
            where = ["(updated_at <= from_unixtime(?) AND updated_at >= from_unixtime(?))"]

        if match:
            where = ["name like ?"] + where
            args = [match + "%"] + args # translate from glob

        if where:
            sql += ' WHERE ' + " AND ".join(where)
        sql += " ORDER BY name"

        if limit:
            sql += " LIMIT ?"
            args += [str(limit)]

        result = list()

        if not self.obj:
            self.obj = dict()

        try:
            # to get policies, and keep our cursor open
            dbi2 = self.master.connect()
            cursor = dbi.do(sql, *args)
            for row_raw in cursor:
                row = row_to_dict(cursor, row_raw)
                self.obj['id'] = row['id']
                self.policies = self._get_policies(dbi=dbi2)
                row = self._get_decode(attrs, row, cols=cols)
                result.append(row)
            cursor.close()
        except:
            # if we broke, dump the rest of the vals so the connection is clean
            try:
                cursor.fetchall()
            except: # pylint: disable=bare-except
                pass
            raise
        finally:
            dbi2.done()

        return result

    ############################################################################
    # pylint: disable=too-many-locals, too-many-statements
    def _put(self, attrs, dbi=None, doabac=True):
        """
        Store an object into DB
        """
        obj = self.obj
        errors = []

        #################### AUTHORIZED
        # these will raise errors if not acceptable
        self.policies = self._get_policies(dbi=dbi)
        self.authorized("write", attrs, sensitive=False, raise_error=doabac)

        errors += self.validate()
        errors += self.map_soft_relationships(dbi)

        chgs = ['updated_by=?']
        args = [str(attrs.token_nbr)]

        # build the submit query
        # sort the keys so the SQL statement is the same and can be cached
        data = dict()
        foreign = set()
        if self.foreign:
            foreign = set(obj.keys())
            foreign.discard('id')
            foreign.discard('updated_at')
            foreign.discard('updated_by')
            foreign.discard('name')

        for name in sorted(self.omap.keys()):
            col = self.omap[name]

            # optional items?
            value = obj.get(name, None)
            if col.stype == "read" or value is None:
                continue

            # map out hard relationship id's?
            if col.hasid:
                rel_id = self.split_name2id(value)[1]
                chgs.append(col.hasid + '=?')
                args.append(rel_id)
                self.obj[col.hasid] = rel_id # for future use

            # encode or encrypt the value
            if col.encrypt:
                if isinstance(value, dict) and value.get('encrypted') == 'values':
                    continue # ignore
                self.authorized('write', attrs, sensitive=True, raise_error=doabac)
                value = self.encrypt(json4store(value))
            elif col.dtype != "value":
                value = json4store(value)

            # store it
            if foreign:
                foreign.discard(name)
            if col.stored == "data":
                data[name] = value
            else:
                chgs.append(col.stored + "=?")
                args.append(value)

        for name in foreign:
            value = obj.get(name, None)
            if value is None:
                continue
            data[name] = json4store(value)

        # data is last
        if self.vardata:
            chgs.append("data=?")
            args.append(json4store(data))

        where = ""
        action = 'INSERT INTO '
        cur_id = obj.get('id', None)
        if cur_id:
            action = 'UPDATE '
            where = ' WHERE id=?'
            args += [cur_id]

        try:
            sql = action + self.table + " SET " + ",".join(chgs) + " " + where
            cursor = dbi.do(sql, *args)
            if cursor.lastrowid:
                self.obj['id'] = cursor.lastrowid
            rows = cursor.rowcount
            cursor.close()
            if rows == 0:
                raise NoChanges("No changes were made")
        except IntegrityError as err:
            raise ObjectExists(str(err))
        except DatabaseError as err:
            log("type=error", msg=str(err), subtype="dberror", sql=sql)
            raise InvalidParameter(str(err))

        if self.obj['id']:
            errors += self.changed(attrs, dbi=dbi)

        return errors

    ############################################################################
    @db_interface
    def update(self, attrs, dbi=None):
        """Update data from self into db.  Use on pre-existing objects"""
        if not self.obj.get('id') and self.obj.get('name'):
            self.obj['id'] = self.name2id_direct(self.obj['name'], dbi)[0]
        return self._put(attrs, dbi=dbi)

    ############################################################################
    @db_interface
    def create(self, attrs, dbi=None, doabac=True):
        """Create data from self into db.  Use on new objects"""
        if self.obj.get('id') and self.name2id_direct(self.obj['id'], dbi)[0] \
           or self.name2id_direct(self.obj['name'], dbi)[0]:
            raise ObjectExists(self.table + " named `{name}` already exists"
                               .format(**self.obj))
        return self._put(attrs, dbi=dbi, doabac=doabac)

    ############################################################################
    def validate(self):
        """
        Validate object from external sources, before converting to db format.

        On critical errors raises InvalidParameter.

        Minor errors are returned as an array.
        """

        data = self.obj # speed up
        for name, col in self.omap.items():
            if col.stype == "alter":
#                key = col.stored
#                if key == "data":
#                    key = name
                val = data.get(name, None)
                if val is None:
                    raise InvalidParameter("Object validate: missing '" + name + "'")

            if not data.get(name): # only alter is required on load
                continue

            if col.dtype != "value" and not isinstance(data[name], col.dtype):
                raise InvalidParameter("Object load: `{}` is not type={}"
                                       .format(name, col.dtype))

        if not self.foreign:
            for name in data.keys():
                if name not in self.omap:
                    raise InvalidParameter("Foreign element in object: `{}` not one of: {}"
                                           .format(name, list(self.omap.keys())))

        return []

    ############################################################################
    # Future: add layers to this, allowing for group level additional crypto
    def encrypt(self, data):
        """Wrapper for Cipher"""
        crypto = self.master.crypto
        if crypto:
            key = self.master.default_key
            return '__$' + key + crypto[key]['cipher'].key_encrypt(data)
        self.NOTIFY("crypto ERROR no keys!")
        return '__$___' + data

    ############################################################################
    # Future: add layers to this, allowing for group level additional crypto
    def decrypt(self, data):
        """Wrapper for Cipher"""
        crypto = self.master.crypto
        if crypto:
            key_name = data[3:6]
            if key_name == '___': # not encrypted
                return data[6:]
            if not crypto.get(key_name):
                log("type=error", msg="cannot decrypt data: crypto key {} missing".format(key_name))
                return '"unencryptable--see logs"'
            try:
                return crypto[key_name]['cipher'].key_decrypt(data[6:])
            except nacl.exceptions.CryptoError as err:
                log("type=error", msg="cannot decrypt data: {}".format(err))
                return '"unencryptable--see logs"'
        else:
            return data[6:]

    ############################################################################
    def authorized(self, action, attrs, sensitive=False, raise_error=False):
        """
        Cross reference ABAC policy for attrs and action.  To evaluate policies
        for debugging, add --debug=abac.
        """

        attrs['action'] = action
        attrs['sensitive'] = sensitive
        attrs['obj_type'] = self.table
        attrs['obj'] = self.obj
        abac_debug = False
        def null(**kwargs): # pylint: disable=unused-argument
            """do nothing"""
        dbg = null

        if self.do_DEBUG(module="abac"):
            def do_dbg(*args, **kwargs): # pylint: disable=unused-argument
                """debugging"""
                kwargs["obj"] = "?"
                kwargs["table"] = self.table
                if self.obj:
                    kwargs["obj"] = self.obj.get('name', self.obj.get('id'))
                log("type=abac", **kwargs)
            dbg = do_dbg
            abac_debug = True
            dbg(step="start-auth", action=action, sensitive=sensitive)

        if action != 'admin':
            actions = [action, 'admin']
        else:
            actions = ['admin']
        for act in actions:
            for policy in self.policies[act]:
                if abac_debug:
                    dbg(step="check-policy", id=policy.policy_id, action=act,
                        expr=policy.policy_expr, name=policy.policy_name)
                if policy.allowed(attrs, debug=abac_debug, base=self):
                    dbg(step="AUTHORIZED", id=policy.policy_id, act=act)
                    return True
                if policy.policy_fail: # always drop out -- dangerous if misapplied
                    if raise_error:
                        raise PolicyFailed("Unable to get permission, try adding --debug=abac arg to engine or --debug=remote-abac to cli") # pylint: disable=line-too-long
                    return False
        if raise_error:
            raise PolicyFailed("Unable to get permission, try adding --debug=abac arg to engine or --debug=remote-abac to cli") # pylint: disable=line-too-long
        dbg(step="FAILED")
        return False

    ############################################################################
    def map_soft_relationships(self, dbi): # pylint: disable=unused-argument
        """
        Review all object relationship arrays and revise them as:

            name[.id]

        If .id is an integer it should map to an existing object, and is given
        lookup preference (ignoring name).  It may also be .0, at which
        point it is a bad reference, but is left in the name. Example:

            tardis-1.14314
            tardis-2.0

        Returns array of errors (if there were any).  An empty array is success.
        """

        # parent placeholder, just return an array
        return list()

    ############################################################################
    def _map_soft_relationship_name2id(self, dbi, table, target):
        """
        Internal function called for a single table and target.
        """
        obj = table.name2id_direct(target, dbi)
        target = target.split(".", 1)[0]
        if obj[0]:
            return (obj[1] + "." + str(obj[0]), '')
        return (target + ".notfound",
                table.table + ":" + target + " not found")

    ############################################################################
    def _map_soft_relationship(self, dbi, table, key):
        """
        Internal function called for a single key to read on self.obj.
        Target is stored back into key.

        Returns error as array.
        """
        if self.obj.get(key):
            target, error = self._map_soft_relationship_name2id(dbi, table, self.obj[key])
            self.obj[key] = target
            if error:
                return list([error])
        return list()

    ############################################################################
    def _map_soft_relationships(self, dbi, table, key):
        """
        Internal function called where key is an array.

        If `key` exists on internal object, walk through it as an array and
        map out relationships to include .id, contrasting against `table`.
        """
        errors = []
        if self.obj.get(key):
            new = []
            for name in self.obj[key]:
                target, error = self._map_soft_relationship_name2id(dbi, table, name)
                if error:
                    errors.append(error)
                new.append(target)
            self.obj[key] = new
        return errors

    @db_interface
    def descendants(self, class_obj, target, dbi=None):
        """
        Find descendants from class and target, where class is one of:

        Pipeline
        Service
        """
        descendants = list()
        if isinstance(Pipeline, class_obj):
            pipe = class_obj.name2id_direct(target, dbi)
            svc_obj = Service(clone=self.master)
            sql = "SELECT id, name FROM Service WHERE pipeline_id = ?"
            for svc_id, svc_name in dbi.do_getlist(sql, pipe[0]):
                descendants.append([svc_id, svc_name])
                descendants += self.descendants(svc_obj, svc_id)
            return list(set(descendants))
        elif isinstance(Service, class_obj):
            pass
        #     flatten configs
        #     import list of relevant configs
        #     import list of instances
        else:
            raise ValueError("class object must be Pipeline or Service")
        return list()

    ############################################################################
    def _delete_policyfor(self, dbi):
        """Delete PolicyFor pertaning to this object"""
        cursor = dbi.do("""DELETE FROM PolicyFor
                  WHERE obj = ? AND target_id = ?
               """, self.table, self.obj['id'])
        cursor.close()

    ############################################################################
    def deleted(self, attrs, dbi=None): # pylint: disable=unused-argument
        """
        Any actions or updates required on delete of this object
        """
        self._delete_policyfor(dbi=dbi)

    ############################################################################
    def changed(self, attrs, dbi=None): # pylint: disable=unused-argument
        """
        Any actions or updates required on change of this object
        """
        scopelist = policyscope_get_cached(self.master.cache, dbi, 'targeted')
        self._delete_policyfor(dbi)
        attribs = dict(obj=self.obj, obj_type=self.table)
        debug = self.do_DEBUG('abac')
        for pscope in scopelist:
            policyscope_map_for(pscope, dbi, attribs, self.table, self.obj['id'],
                                debug=debug)
        self.map_targeted_policies(scopelist, dbi=dbi)
        return list()

    #############################################################################
    def map_targeted_policies(self, scopelist, dbi=None):
        """
        Review policies and scope to what applies to this object
        """
        self._delete_policyfor(dbi)
        attribs = dict(obj=self.obj, obj_type=self.table)
        debug = self.do_DEBUG('abac')
        for pscope in scopelist:
            policyscope_map_for(pscope, dbi, attribs, self.table, self.obj['id'],
                                debug=debug)

################################################################################
class Pipeline(RCObject):
    """
    DROP> drop table if exists Pipeline;
     ADD> create table Pipeline (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    DROP> drop table if exists PipelineArchive;
     ADD> create table PipelineArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;
     ADD>

    DROP> DROP TRIGGER IF EXISTS archive_Pipeline;
     ADD> CREATE TRIGGER archive_Pipeline BEFORE UPDATE ON Pipeline
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO PipelineArchive SELECT * FROM Pipeline WHERE NEW.id = id;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Pipeline'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()

        self.omap['contacts'] = RCMap(stored="data",
                                      dtype=dict,
                                      stype="opt")
        self.omap['launch'] = RCMap(stored="data",
                                    dtype=dict,
                                    stype="opt")
        self.omap['monitor'] = RCMap(stored="data", dtype=list, stype="opt")
        super(Pipeline, self).__init__(*args, **kwargs)

################################################################################
class Service(RCObject):
    """
    DROP> drop table if exists Service;
     ADD> create table Service (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     lane varchar(32) not null default '',
     ADD>     region varchar(32) not null default '',
     ADD>     pipeline_id int not null default 0,
     ADD>     config_id int not null default 0,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    DROP> drop table if exists ServiceArchive;
     ADD> create table ServiceArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     lane varchar(32) not null default '',
     ADD>     region varchar(32) not null default '',
     ADD>     pipeline_id int not null default 0,
     ADD>     config_id int not null default 0,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;

    DROP> DROP TRIGGER IF EXISTS archive_Service;
     ADD> CREATE TRIGGER archive_Service BEFORE UPDATE ON Service
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO ServiceArchive SELECT * FROM Service WHERE NEW.id = id;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Service'
    archive = True

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['pipeline'] = RCMap(stored="data", hasid='pipeline_id')
        self.omap['pipeline_id'] = RCMap(stype="read", stored='pipeline_id')
        self.omap['config'] = RCMap(stored="data", hasid='config_id')
        self.omap['config_id'] = RCMap(stype="read", stored='config_id')
        self.omap['region'] = RCMap(stored="data", stype="opt")
        self.omap['lane'] = RCMap(stored="data", stype="opt")
        self.omap['tenant'] = RCMap(stored="data", stype="opt")
        self.omap['dynamic-instances'] = RCMap(stored="data", stype="opt", dtype=list)
        self.omap['active-instances'] = RCMap(stored="data", stype="opt", dtype=list)
        self.omap['static-instances'] = RCMap(stored="data", stype="opt", dtype=list)
        super(Service, self).__init__(*args, **kwargs)

    ############################################################################
    def map_soft_relationships(self, dbi):
        """map out my relationships"""
        errors = super(Service, self).map_soft_relationships(dbi)

        instance = Instance(clone=self)

        errors += self._map_soft_relationship(dbi, Pipeline(clone=self), "pipeline")
        errors += self._map_soft_relationship(dbi, Config(clone=self), "config")
        errors += self._map_soft_relationships(dbi, instance, "dynamic-instances")
        errors += self._map_soft_relationships(dbi, instance, "static-instances")
        errors += self._map_soft_relationships(dbi, instance, "active-instances")

        return errors

################################################################################
class Config(RCObject):
    """
    DROP> drop table if exists Config;
     ADD> create table Config (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    DROP> drop table if exists ConfigArchive;
     ADD> create table ConfigArchive (
     ADD>     id int not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;

    DROP> DROP TRIGGER IF EXISTS archive_Config;
     ADD> CREATE TRIGGER archive_Config BEFORE UPDATE ON Config
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO ConfigArchive SELECT * FROM Config WHERE NEW.id = id;
     ADD>
    """
    # a list of object attributes which are part of the actual db object

    table = 'Config'
    archive = True

    # note: translate export->exports at some point (typo_for)
    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['extends'] = RCMap(stored="data", dtype=list, stype="opt")
        self.omap['imports'] = RCMap(stored="data", dtype=list, stype="opt")
        self.omap['exports'] = RCMap(stored="data", dtype=list, stype="opt")
        self.omap['content'] = RCMap(stored="data", dtype=dict, stype="opt")
        self.omap['sensitive'] = RCMap(stored="data", dtype=dict, stype="opt", encrypt=True)
        self.omap['setenv'] = RCMap(stored="data", dtype=dict, stype="opt")
        self.omap['file'] = RCMap(stored="data", stype="opt")
        self.omap['type'] = RCMap(stored="data")
        super(Config, self).__init__(*args, **kwargs)

    ############################################################################
    def map_soft_relationships(self, dbi):
        """map out my relationships"""
        errors = super(Config, self).map_soft_relationships(dbi)

        if self.obj['type'] not in ('parameter', 'file'):
            raise InvalidParameter("Invalid type=" + self.obj['type'] +
                                   " not one of: parameter or file")

        errors += self._map_soft_relationships(dbi, self, "extends")
        errors += self._map_soft_relationships(dbi, self, "imports")
        errors += self._map_soft_relationships(dbi, self, "exports")

        return errors

################################################################################
class Instance(RCObject):
    """
    DROP> drop table if exists Instance;
     ADD> create table Instance (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     service_id int not null,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Instance'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['service'] = RCMap(stored="data", hasid='service_id')
        self.omap['service_id'] = RCMap(stype="read", stored='service_id')
        self.omap['status'] = RCMap(stored="data")
        self.omap['address'] = RCMap(stored="data", dtype=dict)
        super(Instance, self).__init__(*args, **kwargs)

    def skeleton(self): # pylint: disable=no-self-use
        """return a set of required attributes with default values"""
        return dict(
            address=dict(),
            service="unknown",
            status="new"
        )

    def validate(self):
        errors = super(Instance, self).validate()

        if not isinstance(self.obj['address'], dict):
            raise InvalidParameter("address is not an object")

        # could add status code validation
        return errors

    ############################################################################
    def map_soft_relationships(self, dbi):
        """map out my relationships"""
        errors = super(Instance, self).map_soft_relationships(dbi)
        errors += self._map_soft_relationship(dbi, Service(clone=self), "service")

        return errors

################################################################################
class State(RCObject):
    """
    DROP> drop table if exists State;
     ADD> create table State (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'State'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        super(State, self).__init__(*args, **kwargs)


################################################################################
class Build(RCObject):
    """
    DROP> drop table if exists Build;
     ADD> create table Build (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Build'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['application'] = RCMap(stored="data", stype="opt")
        self.omap['version'] = RCMap(stored="data", stype="opt")
        self.omap['state'] = RCMap(stored="data", stype="opt")
        self.omap['status'] = RCMap(stored="data", dtype=dict, stype="opt")
        self.omap['type'] = RCMap(stored="data", stype="opt")
        self.omap['link'] = RCMap(stored="data", stype="opt")
        super(Build, self).__init__(*args, **kwargs)

################################################################################
class Group(RCObject):
    """
    DROP> drop table if exists Grp;
     ADD> create table Grp (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     _grp text,
     ADD>     typ varchar(32),
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Grp'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['group'] = RCMap(stored="data", dtype=list, stype="alter")
        self.omap['_grp'] = RCMap(stored="_grp", dtype=list, stype="opt")
        self.omap['type'] = RCMap(stored="typ", dtype="value", stype="alter")
        super(Group, self).__init__(*args, **kwargs)

    ############################################################################
    def validate(self):
        errors = super(Group, self).validate()

        opts = Schema.table_names + ["set", "password"]
        if self.obj['type'].lower() not in opts:
            raise InvalidParameter("Invalid type=" + self.obj['type'] +
                                   " not one of: " + ", ".join(opts))

        return errors

    ############################################################################
    def map_soft_relationships(self, dbi): # pylint: disable=too-many-locals
        """map out my relationships"""
        errors = super(Group, self).map_soft_relationships(dbi)

        # just a list of strings
        if self.obj['type'] == 'set':
            self.obj['group'] = list(set([x.lower() for x in self.obj['group']]))
            self.obj['_grp'] = self.obj['group']
        elif self.obj['type'] == 'password':
            _grp = list()
            grp = list()
            for elem in self.obj['group']:
                parts = elem.split(":")
                if len(parts) != 2:
                    raise InvalidParameter("password group items should be a list of name:passwords") # pylint: disable=line-too-long
                name, pword = parts
                if pword[:3] == '$7$':
                    _grp.append(pword)
                    grp.append(elem)
                else:
                    sha256 = nacl.pwhash.scryptsalsa208sha256_str(pword.encode()).decode()
                    _grp.append(sha256)
                    grp.append(name.lower() + ":" + sha256)
            self.obj['group'] = grp
            self.obj['_grp'] = _grp
        elif self.obj['type'].lower() in Schema.table_names:
            # todo: look for better option
            # pylint: disable=eval-used
            class_obj = eval(self.obj['type'])(clone=self)
            mapped = set()
            noid = set()
            for target in self.obj['group']:
                (target, error) = self._map_soft_relationship_name2id(dbi,
                                                                      class_obj,
                                                                      target)
                if error:
                    errors.append(error)
                mapped.add(target)
                noid.add(target.split(".")[0])

            self.obj['group'] = list(mapped)
            self.obj['_grp'] = list(noid)
        else:
            raise ValueError("Invalid type: " + self.obj['type'])

        return errors

    ############################################################################
    @db_interface
    def get_for_attrs(self, dbi=None):
        """
        We pull from _grp for performance purposes, and it is stripped of the
        id for matching purposes
        """
        cache = self.master.cache
        groups = cache.get_cache('groups', '.')
        if groups:
            return groups
        groups = dictlib.Obj()
        result = dbi.do_getlist("""
            SELECT name, _grp FROM Grp
          """)
        for row in result:
            groups[row[0]] = json2data(row[1])
        cache.set_cache('groups', '.', groups)
        return groups

    #############################################################################
    def changed(self, attrs, dbi=None):
        errors = super(Group, self).changed(attrs, dbi=dbi)
        self.master.cache.clear_type('groups')
        return errors

    #############################################################################
    def deleted(self, attrs, dbi=None):
        errors = super(Group, self).deleted(attrs, dbi=dbi)
        self.master.cache.clear_type('groups')
        return errors

################################################################################
class Apikey(RCObject):
    # pylint: disable=line-too-long
    """
    DROP> drop table if exists Apikey;
     ADD> create table Apikey (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     uuid char(37) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     secrets text,
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name),
     ADD>     unique(uuid)
     ADD> ) engine=InnoDB;
     ADD> INSERT INTO Apikey set id=100, uuid=uuid(), name='master', secrets='[]', data='{}', updated_by="";
    """
    # a list of object attributes which are part of the actual db object

    table = 'Apikey'
    keysize = 66 # skips padding

    def __init__(self, *args, **kwargs):
        self.obj = dict()
        self.omap = dictlib.Obj()
        self.omap['uuid'] = RCMap(stored='uuid', dtype="value", stype="alter")
        self.omap['secrets'] = RCMap(stored="secrets", dtype=list, stype="opt", sensitive=True)
        self.omap['description'] = RCMap(stored="data", dtype="value", stype="opt")
        super(Apikey, self).__init__(*args, **kwargs)

    ############################################################################
    def validate(self):
        if not self.obj.get('name', ''):
            raise InvalidParameter("Object load: missing 'name'")
        return []

    ############################################################################
    @db_interface
    def create(self, attrs, dbi=None, doabac=True):
        """
        create new apikey
        """

        if self.obj.get('id'):
            raise InvalidParameter("id must be left undefined on apikey creation")

        self.obj['uuid'] = str(uuid.uuid4())
        self.obj['secrets'] = [base64.b64encode(nacl.utils.random(self.keysize)).decode()]

        return self._put(attrs, dbi=dbi, doabac=doabac)

    # override update to deal with params changing and deleting secrets
    # do not accept secrets as changes, must use new_secrete

    # create new_secret

class AuthSession(RCObject):
    """
    DROP> drop table if exists AuthSession;
     ADD> create table AuthSession (
     ADD>     token_id int not null,
     ADD>     name varchar(256) not null,
     ADD>     secret varchar(256) not null,
     ADD>     created_at timestamp not null,
     ADD>     expires_at int not null,
     ADD>     session_data text,
     ADD>     index(token_id, name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'AuthSession'
    policy_map = False

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['token_id'] = RCMap(stored='token_id', dtype="value")
        super(AuthSession, self).__init__(*args, **kwargs)

    ############################################################################
    @db_interface
    def new_session(self, token_obj, expires, data, dbi=None):
        """
        generate a unique auth session secret
        """

        tok_id = token_obj.obj['id']
        tok_name = token_obj.obj['name']
        session_id = hashlib.sha256((str(time.time()) + tok_name).encode()).hexdigest()
        secret_raw = nacl.utils.random(64)
        secret_encoded = base64.b64encode(secret_raw)
        data_txt = json4store(data)
        cursor = dbi.do("""
            INSERT INTO AuthSession
               SET token_id=?,
                   name=?,
                   secret=?,
                   expires_at=?,
                   session_data=?
            """, tok_id, session_id, secret_encoded, expires, data_txt)
        cursor.close()

        if not self.obj:
            self.obj = dict()

        self.obj['token_id'] = tok_id
        self.obj['session_id'] = session_id
        self.obj['name'] = session_id
        self.obj['secret_raw'] = secret_raw
        self.obj['secret_encoded'] = secret_encoded.decode()
        self.obj['data'] = data

        key = str(tok_id) + ":" + str(session_id)
        self.master.cache.set_cache('session', key, dict(expires=expires, obj=self))

        return True

    ############################################################################
    # pylint: disable=no-self-use
    @db_interface
    def get_session(self, token_id, session_id, dbi=None):
        """
        get unique auth session secret
        """

        cache = self.master.cache
        key = str(token_id) + ":" + str(session_id)
        session = cache.get_cache('session', key)
        if session:
            if session['expires'] > time.time():
                return session['obj']
            cache.remove_cache('session', key)

        row = dbi.do_getone("""
            SELECT secret, session_data, expires_at FROM AuthSession
             WHERE token_id = ? AND name = ? and expires_at >= ?
            """, int(token_id), str(session_id), int(time.time()), output=list)

        if not row:
            return False

        if not self.obj:
            self.obj = dict()

        self.obj['token_id'] = token_id
        self.obj['name'] = session_id
        self.obj['secret_raw'] = base64.b64decode(row[0])
        self.obj['secret_encoded'] = row[0]
        self.obj['data'] = json2data(row[1])

        cache.set_cache('session', key, dict(expires=row[2], obj=self))

        return self

    ############################################################################
    # pylint: disable=no-self-use
    @db_interface
    def clean_keys(self, dbi=None):
        """
        remove expired session keys
        """

        return dbi.do("""
            DELETE FROM AuthSession
             WHERE expires_at < ?
            """, time.time())

################################################################################
class Policy(RCObject):
    # pylint: disable=line-too-long
    """
    DROP> drop table if exists Policy;
     ADD> create table Policy (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy text not null,
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     result enum('pass', 'fail') not null default 'pass',
     ADD>     sort_order int not null default 1000,
     ADD>     primary key(id)
     ADD> ) engine=InnoDB;
     ADD> INSERT INTO Policy SET id=100, name='master', policy='token_name=="master"', data='{}', updated_by="";

    MIGRATE-001> alter table Policy add column sort_order int not null default 1000;
    MIGRATE-001> alter table Policy add column result enum('pass', 'fail') not null default 'pass';

    DROP> drop table if exists PolicyArchive;
     ADD> create table PolicyArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy text not null,
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     index(id, updated_at),
     ADD>     result enum('pass', 'fail') not null default 'pass',
     ADD>     sort_order int not null default 1000,
     ADD>     index(id)
     ADD> ) engine=InnoDB;

    MIGRATE-001> alter table PolicyArchive add column sort_order int not null default 1000;
    MIGRATE-001> alter table PolicyArchive add column result enum('pass', 'fail') not null default 'pass';

    DROP> DROP TRIGGER IF EXISTS archive_Policy;
     ADD> CREATE TRIGGER archive_Policy BEFORE UPDATE ON Policy
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO PolicyArchive SELECT * FROM Policy WHERE NEW.id = id;
    """

    table = 'Policy'
    archive = True
    vardata = True

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['policy'] = RCMap(stype="alter", stored='policy')
        self.omap['result'] = RCMap(stype="opt", stored='result')
        self.omap['order'] = RCMap(stype="opt", stored='sort_order')
        super(Policy, self).__init__(*args, **kwargs)

    #############################################################################
    # merge in w/abac.Policy validation
    # keep pre-rx version and post, for subsequent editing
    def validate(self):
        errors = super(Policy, self).validate()
        self.obj['order'] = self.obj.get('order', 1000) # make a default
        self.obj['result'] = self.obj.get('result', 'pass').lower() # make a default
        if self.obj['result'] not in ('pass', 'fail'):
            raise InvalidParameter("Result may be only pass or fail")
        try:
            compile(self.obj['policy'], '<policy>', 'eval')
            # Future note: put in an eval here with mock data
        except SyntaxError as err:
            raise InvalidParameter("Cannot prepare policy: " + err.args[0] + "\n" +
                                   "Character {}: {}".format(err.offset, err.text))
        except TypeError as err:
            raise InvalidParameter("Cannot prepare policy: " + str(err))
        return errors

    #############################################################################
    def changed(self, attrs, dbi=None):
        # changes to parent matching self
        errors = super(Policy, self).changed(attrs, dbi=dbi)

        self.master.cache.clear_type('policymap')

        return errors

    #############################################################################
    def deleted(self, attrs, dbi=None):
        # changes to parent matching self
        errors = super(Policy, self).deleted(attrs, dbi=dbi)

        dbi.do_count("""DELETE FROM PolicyFor WHERE policy_id = ?""", self.obj['id'])
        dbi.do_count("""DELETE FROM Policyscope WHERE policy_id = ?""", self.obj['id'])
        self.master.cache.clear_type('policymap')

        return errors

################################################################################
def policyscope_get_cached(cache, dbi, mtype):
    """get a list of policyscope objects, checking cache first"""
    scopelist = cache.get_cache('policyscope', mtype)
    if scopelist:
        return scopelist
    return policyscope_get_direct(cache, dbi, mtype)

################################################################################
def policyscope_get_direct(cache, dbi, mtype):
    """get a list of policyscope objects directly from db, and update cache"""
    scopelist = list()
    cursor = dbi.do("""SELECT id,policy_id,matches,actions
                         FROM Policyscope
                        WHERE type = ?""", mtype)
    for row in cursor:
        pscope = row_to_dict(cursor, row)
        pscope['ast'] = compile(pscope['matches'], '<scope ' + str(pscope['id']) + '>', "eval")
        scopelist.append(pscope)
    cursor.close()

    cache.set_cache('policyscope', mtype, scopelist)
    return scopelist

################################################################################
# pylint: disable=too-many-arguments
def policyscope_map_for(pscope, dbi, attribs, table, target_id, debug=False):
    """"map policyscope objects into PolicyFor table"""
    try:
        # pylint: disable=eval-used
        if not pscope.get('ast'):
            pscope['ast'] = compile(pscope['matches'], '<scope ' + str(pscope['id']) + '>', "eval")

        objs = pscope.get('objects', [])
        if objs:
            if objs[0] != '*' and attribs['obj_type'] not in objs:
                return

#        log("matches={} attrs={}".format(pscope['matches'], attribs))
#        log("context={}".format(abac.abac_context()))
#        log("attribs={}".format(attribs))

        if eval(pscope['ast'], abac.abac_context(), attribs):
            for action in pscope['actions'].split(","):
                if debug:
                    log("type=policymap",
                        action=action,
                        table=table,
                        scope=pscope['id'],
                        policy=pscope['policy_id'],
                        target=target_id)
                dbi.do_count("""REPLACE INTO PolicyFor
                                SET obj = ?, policy_id = ?, target_id = ?,
                                    pscope_id = ?, action = ?
                             """, table, pscope['policy_id'], target_id,
                             pscope['id'], action)

    except Exception as err: # pylint: disable=broad-except
        if do_DEBUG("abac"):
            context = dictlib.union(abac.abac_context(), attribs)
            log("type=error", msg="policymap failure: " + str(err),
                expr=pscope.get('matches'),
                context=context,
                scope=pscope.get('id', 0),
                policy=pscope.get('policy_id', 0),
                target=target_id)
        else:
            log("type=error", msg="policymap failure: " + str(err),
                expr=pscope.get('matches'),
                scope=pscope.get('id', 0),
                policy=pscope.get('policy_id', 0),
                target=target_id)

################################################################################
class Policyscope(RCObject):
    # pylint: disable=line-too-long
    """
    DROP> drop table if exists Policyscope;
     ADD> create table Policyscope (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy_id int not null,
     ADD>     type enum('targeted', 'global') not null default 'targeted',
     ADD>     matches text not null,
     ADD>     objects varchar(256) not null default '[]',
     ADD>     actions varchar(64) not null default 'read',
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    MIGRATE-001> alter table Policyscope change column type type enum('targeted', 'global') not null default 'targeted';
    MIGRATE-002> alter table Policyscope add column objects varchar(256) not null default '[]';
    MIGRATE-002> update Policyscope set objects = '[]' where objects = '';

    DROP> drop table if exists PolicyscopeArchive;
     ADD> create table PolicyscopeArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy_id int not null,
     ADD>     type enum('targeted', 'global') not null default 'targeted',
     ADD>     matches text not null,
     ADD>     objects varchar(256) not null default '[]',
     ADD>     actions varchar(64) not null default 'read',
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;
     ADD>

    MIGRATE-001> alter table PolicyscopeArchive change column type type enum('targeted', 'global') not null default 'targeted';
    MIGRATE-002> alter table PolicyscopeArchive add column objects varchar(256) not null default '[]';
    MIGRATE-002> update PolicyscopeArchive set objects = '[]' where objects = '';

    DROP> DROP TRIGGER IF EXISTS archive_Policyscope;
     ADD> CREATE TRIGGER archive_Policyscope BEFORE UPDATE ON Policyscope
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO PolicyscopeArchive SELECT * FROM Policyscope WHERE NEW.id = id;

    DROP> drop table if exists PolicyFor;
     ADD> create table PolicyFor (
     ADD>     policy_id int not null,
     ADD>     obj enum('Pipeline', 'Service', 'Config', 'Instance', 'Policy', 'Policyscope', 'Apikey', 'Build', 'Grp', 'State'),
     ADD>     action enum('write', 'read', 'admin') default 'read',
     ADD>     pscope_id int not null default 0,
     ADD>     target_id int not null default 0,
     ADD>     primary key(obj, policy_id, target_id),
     ADD>     index(obj, target_id),
     ADD>     index(obj)
     ADD> ) engine=InnoDB;
    """

    table = 'Policyscope'
    archive = True
    policy_map = True
    vardata = True

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['policy'] = RCMap(stored="data", hasid='policy_id')
        self.omap['policy_id'] = RCMap(stype="read", stored='policy_id')
        self.omap['objects'] = RCMap(stype="opt", stored='objects', dtype=list)
        self.omap['matches'] = RCMap(stype="alter", stored='matches')
        self.omap['actions'] = RCMap(stype="alter", stored='actions')
        self.omap['type'] = RCMap(stype="alter", stored='type')
        super(Policyscope, self).__init__(*args, **kwargs)

    #############################################################################
    # merge in w/abac.Policy validation
    # keep pre-rx version and post, for subsequent editing
    # pylint: disable=too-many-branches
    def validate(self):
        errors = super(Policyscope, self).validate()
        try:
            compile(self.obj['matches'], '<matches>', 'eval')
            # Future note: put in an eval here with mock data
        except SyntaxError as err:
            raise InvalidParameter("Cannot prepare match expression: " + err.args[0] +
                                   "\n" + "Character {}: {}".format(err.offset, err.text))
        except TypeError as err:
            raise InvalidParameter("Cannot prepare match expression: " + str(err))

        if self.obj['type'].lower() not in ('targeted', 'global'):
            raise InvalidParameter("Policy Match Type is not one of: global, targeted")

        if self.obj.get('objects'):
            tmap = dict()
            for table in Schema.tables:
                if not table.policy_map:
                    continue
                tmap[table.table.lower()] = table.table
            objs = set()
            errs = []
            for obj in self.obj['objects']:
                if obj.lower() == "group":
                    obj = "Grp"
                if obj == '*':
                    objs = ['*']
                    break
                if not tmap.get(obj.lower()):
                    errs += ["Object '" + obj + "' is not valid"]
                else:
                    objs.add(tmap.get(obj.lower()))

            if errs:
                raise InvalidParameter(", ".join(errs) + ".  Must be one of: " +
                                       ", ".join(list(tmap.values())))

            self.obj['objects'] = list(objs)

        actions = list()
        for action in re.split(r'\s*,\s*', self.obj['actions']):
            action = action.lower()
            if not action in ('admin', 'read', 'write'):
                raise InvalidParameter("Invalid action type: " + action)
            actions.append(action)
        if not actions:
            raise InvalidParameter("No valid actions defined")
        if 'admin' in actions:
            self.obj['actions'] = "admin"
        else:
            self.obj['actions'] = ",".join(actions)

        return errors

    #############################################################################
    def changed(self, attrs, dbi=None):
        errors = super(Policyscope, self).changed(attrs, dbi=dbi)

        self.map_self(dbi=dbi)

        return errors

    #############################################################################
    @db_interface
    def remap_all(self, dbi=None):
        """To be called periodically and make sure things are fresh"""
        cache = dictlib.Obj(
            did={},
            objlist={}
        )
        groups = Group(master=self.master).get_for_attrs()

        for scope_array in dbi.do_getlist("SELECT id FROM Policyscope"):
            scope_id = scope_array[0]
            pscope = Policyscope(clone=self)
            pscope.get(scope_id, attrs=True)
            pscope.map_self(dbi=dbi, cache=cache, invalidate=False, groups=groups)

        # at the end
        self.master.cache.clear_type('policymap')

    #############################################################################
    # pylint: disable=too-many-branches,too-many-arguments
    def map_self(self, dbi=None, cache=None, invalidate=True, groups=None, debug=None):
        """Map my policy scope against objects"""

        if debug is None:
            debug = self.do_DEBUG('abac')

        # first cleanup previous mappings from this policyscope
        dbi.do_count("""DELETE FROM PolicyFor WHERE pscope_id = ?""", self.obj['id'])
        if not groups:
            groups = Group(master=self.master).get_for_attrs()

        if self.obj['type'] == 'global':
            # this should be skeleton
            attribs = dict(obj={}, obj_type='', groups=groups)
            for table in Schema.tables:
                if not table.policy_map:
                    continue
                obj = table(master=self.master)
                attribs['obj'] = obj.obj or {
                    'name': 'n/a'
                }
                attribs['obj_type'] = obj.table
                policyscope_map_for(self.obj, dbi, attribs, obj.table, 0, debug=debug)

        else: # targeted
            for table in Schema.tables:
                # some tables skip policy mapping
                if not table.policy_map:
                    continue

                tobj = table(master=self.master)

                # build the list in memory, so we can re-use the cursor later
                if cache and cache.objlist.get(tobj.table):
                    memarray = cache.objlist.get(tobj.table)
                else:
                    memarray = list()
                    cursor = dbi.do("SELECT id,name FROM " + tobj.table)
                    for row in cursor:
                        val = row[1]
                        if isinstance(val, bytes):
                            val = val.decode('utf-8')
                        memarray.append(dict(id=row[0], name=val))
                    if cache:
                        cache.objlist[tobj.table] = memarray
                    cursor.close()

                # fanout: for each row in the table
                for row in memarray:
                    # this should be skeleton
                    attribs = dict(obj=row, obj_type=table.table, groups=groups)
                    policyscope_map_for(self.obj, dbi, attribs, table.table, row['id'],
                                        debug=debug)

        # invalidate all cached data for policy maps
        if invalidate:
            self.master.cache.clear_type('policymap')


    ############################################################################
    def map_soft_relationships(self, dbi):
        """map out my relationships"""
        errors = super(Policyscope, self).map_soft_relationships(dbi)

        maperr = self._map_soft_relationship(dbi, Policy(clone=self), "policy")
        if maperr:
            raise InvalidParameter(maperr[0])

        return errors

################################################################################
class Schema(rfx.Base):
    """Define our DB schema as code"""
    # NOTE: update PolicyFor enum if adding to this list
    # also: order matters
    tables = [Policy, Policyscope, Pipeline, Service, Config, Instance, Apikey,
              Build, Group, AuthSession, State]
    table_names = [x.__name__.lower() for x in tables]
    master = ''

    # pylint: disable=super-init-not-called
    def __init__(self, *args, **kwargs):
        if kwargs.get('master'):
            self.master = kwargs['master']
            del kwargs['master']
            kwargs['base'] = self.master

    ############################################################################
    @db_interface
    def cleanup(self, dbi=None, verbose=False):
        """cleanup the db (remove everything)"""
        dbi.connect()
        for obj in self.tables:
            if verbose:
                self.NOTIFY("--> Cleanup " + obj.table)
            schema = []
            for line in obj.__doc__.split("\n"):
                match = re.search(r'^\s+DROP> *(.*)$', line)
                if match:
                    schema.append(match.group(1))
            for stmt in "\n".join(schema).split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                dbi.dbc.cmd_query(stmt)

    ############################################################################
    # pylint: disable=unused-argument,too-many-branches
    @db_interface
    def initialize(self, dbi=None, verbose=False, reset=True):
        """Setup the database."""
        new_master = False

        for obj in self.tables:
            desc = None
            try:
                desc = dbi.do_getlist("describe " + obj.table)
            except ProgrammingError:
                pass
            if not reset and desc:
                continue

            if obj.table == 'Apikey':
                new_master = True

            schema = []

            # xTODO: add MIGRATE (need a tracking table)
            for line in obj.__doc__.split("\n"):
                if reset:
                    match = re.search(r'^\s+(ADD|DROP)> *(.*)$', line)
                else:
                    match = re.search(r'^\s+(ADD)> *(.*)$', line)
                if match:
                    schema.append(match.group(2))
            if verbose:
                self.NOTIFY("Initializing {} ..".format(obj.table))
            for stmt in "\n".join(schema).split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                dbi.dbc.cmd_query(stmt)

        if reset or new_master:
            if verbose:
                self.NOTIFY("Initializing new master ..\n")
            pscope = Policyscope(master=self.master)
            pscope.load({
                'name': 'master',
                'matches': 'True',
                'policy': 'master',
                'actions': 'admin',
                'objects': ['*'],
                'type': 'global'
            })
            pscope.create(abac.MASTER_ATTRS, doabac=False, dbi=dbi) # special override
            pscope.get("master", True, dbi=dbi)

            secret = base64.b64encode(nacl.utils.random(Apikey.keysize)).decode()
            dbi.do_count("""
                UPDATE Apikey
                   SET secrets = ?
                 WHERE id = 100
            """, json4store([secret]))

            log("Initializing schema master apikey, <CHANGE>",
                REFLEX_APIKEY="master." + secret)
