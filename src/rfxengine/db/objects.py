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
import traceback
import hashlib # hashlib is fastest for hashing
import nacl.utils # for keys -- faster than cryptography lib
from mysql.connector.errors import ProgrammingError, IntegrityError
import rfx
from rfx import json4store, json2data, json4human
from rfxengine import abac, log, trace, do_DEBUG
from rfxengine.db.pool import db_interface
from rfxengine.db.mxsql import OutputSingle, row_to_dict
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
class ObjectNotFound(Exception):
    """Returned when a requested object cannot be found"""
    pass

class NoArchive(Exception):
    """The specified object doesn't support Archives"""
    pass

class ObjectExists(Exception):
    """Returned when there are relationship problems"""
    pass

class RelationshipException(Exception):
    """Returned when there are relationship problems"""
    pass

class NoChanges(Exception):
    """Nothing was changed"""
    pass

class CipherException(Exception):
    """Problems w/crypto"""
    pass

class InvalidParameter(Exception):
    """Variant error for catching bad params"""
    pass

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
    obj.archives()
    """

    table = ''
    archive = False
    foreign = True # do we allow foreign keys (i.e. undefined)
    master = None
    obj = None
    vardata = True
    start = 0

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
    # pylint: disable=unused-argument
    @db_interface
    def archives(self, target, dbi=None):
        """Returns a list of archived dates, or NoArchive exception"""

        if not self.archive:
            raise NoArchive(self.table + " does not support archives")

        #################### AUTHORIZED
        return dbi.do_getlist("SELECT updated_at FROM " + self.table +
                              "Archive WHERE id = ?", target, output=OutputSingle)

    ############################################################################
    def _get_policies(self, dbi=None):
        policies = dict(read=dict(), write=dict(), admin=dict())
        if not self.policy_map:
            return policies

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
            for pol_id in pmap[action]:
                policy = cache.get_cache('policy', pol_id, start=start)
                if not policy:
                    return self._get_policies_direct(target_id, key, cache, start, dbi=dbi)
                policies[action][pol_id] = policy

        return policies

    ############################################################################
    # pylint: disable=too-many-arguments
    def _get_policies_direct(self, target_id, key, cache, start, dbi=None):
        if target_id:
            result = dbi.do_getlist("""
                SELECT action, id, name, policy, data, unix_timestamp(updated_at), target_id
                  FROM Policy, PolicyFor
                 WHERE id=policy_id AND obj = ? AND (target_id = ? OR target_id = 0)
              """, self.table, target_id)
        else:
            result = dbi.do_getlist("""
                SELECT action, id, name, policy, data, unix_timestamp(updated_at), target_id
                  FROM Policy, PolicyFor
                 WHERE id=policy_id AND obj = ? AND target_id = 0
              """, self.table)

        policies = dict(read=dict(), write=dict(), admin=dict())
        pmap = dict(read=list(), write=list(), admin=list())
        for row in result:
            policy = abac.Policy(*row)
            policy.expires = cache.set_cache('policy',
                                             policy.policy_id,
                                             policy,
                                             base_time=start)
            pmap[policy.policy_type].append(policy.policy_id)
            policies[policy.policy_type][policy.policy_id] = policy
        cache.set_cache('policymap', key, pmap, base_time=start)
        return policies

    ############################################################################
    @db_interface
    def name2id(self, target, dbi=None):
        """wraps name2id_direct with db_interface decorator"""
        return self.name2id_direct(target, dbi)

    ############################################################################
    def name2id_direct(self, target, dbi):
        """
        Does an object exist?  Lightweight test, return obj ID or 0

        Accepts a string or integer.  If an int, it refers to an existing object.
        If a string, it accepts a name reference for an object (name.id).  If .id
        is an integer, that is given preference in looking up the object.
        """
        if isinstance(target, str):
            name, obj_id = self.split_name2id(target)
            if obj_id:
                target = obj_id
            else:
                target = name
        if isinstance(target, int):
            result = dbi.do_getone("SELECT ID FROM " + self.table +
                                   " WHERE id = ?", target, output=list)
        else:
            result = dbi.do_getone("SELECT ID FROM " + self.table +
                                   " WHERE name = ?", target, output=list)
        if result:
            return result[0]
        return 0

    ############################################################################
    # pylint: disable=no-self-use
    def split_name2id(self, target):
        """convert a conventional name reference to include the id"""
        if target and target[:1] in "0123456789":
            return ('', int(target))
        name, obj_id = (target + ".").split(".")[:2]
        if obj_id and obj_id[:1] in "0123456789":
            return (name, int(obj_id))
        else:
            return (name, 0)

    ############################################################################
    @db_interface
    def exists(self, target, dbi=None):
        """Does an object exist?  Lightweight test, return obj ID or 0"""
        return self.name2id_direct(target, dbi)

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
    def get(self, target, attrs, dbi=None, archived=None):
        """
        Get an object from the DB

        If a archive is specified, pull the archived version (value is the date)
        archive is only appropriate for tables supporting Archive.
        """
        sql = "SELECT * FROM " + self.table
        if isinstance(target, str):
            idnbr = self.name2id_direct(target, dbi)
        elif not isinstance(target, int):
            raise InvalidParameter("Invalid target type specified?")
        else:
            idnbr = target

        args = [idnbr]

        if attrs is not True: # special override
            self.obj = {'id': idnbr} # mockup ID so policies will match
            self.policies = self._get_policies(dbi=dbi)

        if archived:
            sql += 'Archive WHERE id=? AND updated_at = ?'
            args.append(archived)
        else:
            sql += ' WHERE id=?'

        dbin = dbi.do_getone(sql, *args)

        if not dbin:
            raise ObjectNotFound("Unable to load {}: {}"
                                 .format(self.table, target))

        self.obj = self._get_decode(attrs, dbin)

        if attrs is not True:
            self.authorized("read", attrs, sensitive=False)

        # are there any policies targetted to this object?
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
#        if not attrs.get('obj'):
#            attrs['obj'] = dict()
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
            else:
                value = dbin.get(name, None)

            if value is None:
                if col.stype == "opt":
                    continue
                raise InvalidParameter("Object load: missing '" + name + "'")

            if col.encrypt:
                trace("{} Authorized get {}".format(name, json4human(attrs)))
                if attrs is True or not self.authorized("read",
                                                        attrs,
                                                        sensitive=True,
                                                        raise_error=False):
                    value = 'encrypted'
                elif isinstance(value, str) and value[:3] == '__$':
                    trace("{} decrypt".format(name))
                    value = json2data(self.decrypt(value))

            elif col.sensitive:
                if attrs is True:
                    if col.dtype != "value":
                        value = json2data(value)
                elif self.authorized("write", attrs,
                                     sensitive=True, raise_error=False):
                    value = json2data(value)
                else:
                    value = ["redacted"] # pylint: disable=redefined-variable-type
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
        self.authorized("write", attrs, sensitive=False)

        obj_id = self.name2id_direct(target, dbi)
        if obj_id:
            deleted = dbi.do_count("DELETE FROM " + self.table + " WHERE id = ?", obj_id)
        else:
            raise ObjectNotFound("Target not found")
        self.obj = dict(id=obj_id)
        self.deleted(attrs, dbi=dbi)

        return deleted

    ############################################################################
    @db_interface
    def list_buffered(self, attrs, dbi=None, limit=0, match=None):
        """
        Query and get a list of objects, using a buffer for results.
        Do not worry about the dbi.
        Arguments:

            limit=X  -- optional, limit to X results
            match=X  -- optional, match name as provided (glob)

        Use .list_iterated() for an iterator based list, but you must
        also provide the dbi.
        """
        (sql, args) = self._list_prep(attrs, limit=limit, match=match, dbi=dbi)

        results = []
        cursor = dbi.do(sql, *args)
        for row in cursor:
            results.append(row_to_dict(cursor, row))
        return results

    ############################################################################
#    def list_iterated(self, attrs, dbi=None, limit=0, match=None):
#        """
#        List objects, using an iterator.  Same as .list_buffered() but you also
#        must include the dbi, and results are given as an iterator (cursor)
#        """
#        (sql, args) = self._list_prep(attrs, limit=limit, match=match)
#        cursor = dbi.do(sql, args)
#        return cursor

    ############################################################################
    # pylint: disable=too-many-arguments
    def _list_prep(self, attrs, dbi=None, limit=0, match=None):
        """
        Prepare sql and args for list
        """

        try:
            self.policies = self._get_policies(dbi=dbi)
            self.authorized("read", attrs, sensitive=False)
        except:
            trace("list read fail")
            trace(json4human(attrs))
            trace(self.policies)
            raise

        args = []
        sql = "SELECT id,name,updated_by,unix_timestamp(updated_at) FROM " + self.table
        if match:
            sql += " WHERE name like ?"
            args += [match.replace("*", "%")] # translate from glob

        sql += " ORDER BY name"
        if limit:
            sql += " LIMIT ?"
            args += [str(limit)]

        return (sql, args)

    ############################################################################
    # pylint: disable=too-many-locals
    @db_interface
    def list_cols(self, attrs, cols, dbi=None, limit=0, match=None):
        """
        List objects, using an iterator, with a specific called set of columns.
        """

        self.policies = self._get_policies(dbi=dbi)
        self.authorized("read", attrs, sensitive=False)

        keys = []
        args = []
        added_data = False
        if 'id' not in cols: # needed for get_policies
            cols.append('id')
        for item in cols:
            if item == "updated_at":
                keys.append("unix_timestamp(updated_at)")
            else:
                col = self.omap.get(item)
                if not col or col.stored == 'data':
                    if not added_data:
                        keys.append("data")
                        added_data = True
                else:
                    keys.append(col.stored)

        sql = "SELECT " + ",".join(keys) + " FROM " + self.table
        if match:
            sql += " WHERE name like ?"
            args += [match.replace("*", "%")] # translate from glob

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
        except:
            # if we broke, dump the rest of the vals so the connection is clean
            cursor.fetchall()
            raise
        finally:
            dbi2.done()

        return result

    ############################################################################
    # pylint: disable=too-many-locals, too-many-statements
    def _put(self, attrs, dbi=None):
        """
        Store an object into DB
        """
        obj = self.obj
        errors = []

        #################### AUTHORIZED
        # these will raise errors if not acceptable
        self.policies = self._get_policies(dbi=dbi)
        self.authorized("write", attrs, sensitive=False)

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

            # encode or encrypt the value
            if col.encrypt:
                if value == 'encrypted':
                    continue # ignore
                self.authorized('write', attrs, sensitive=True)
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
            if cursor.rowcount == 0:
                raise NoChanges("No changes were made")
        except IntegrityError as err:
            raise ObjectExists(str(err))

        if self.obj['id']:
            errors += self.changed(attrs, dbi=dbi)

        return errors

    ############################################################################
    @db_interface
    def update(self, attrs, dbi=None):
        """Update data from self into db.  Use on pre-existing objects"""
        if not self.obj.get('id') and self.obj.get('name'):
            self.obj['id'] = self.name2id_direct(self.obj['name'], dbi)
        return self._put(attrs, dbi=dbi)

    ############################################################################
    @db_interface
    def create(self, attrs, dbi=None):
        """Create data from self into db.  Use on new objects"""
        if self.obj.get('id') and self.exists(self.obj['id']) \
           or self.exists(self.obj['name']):
            raise ObjectExists(self.table + " named `{name}` already exists"
                               .format(**self.obj))
        return self._put(attrs, dbi=dbi)

    ############################################################################
    def validate(self):
        """
        Validate object.

        On critical errors raises InvalidParameter.

        Minor errors are returned as an array.
        """

        data = self.obj # speed up
        for name, col in self.omap.items():
            if col.stype == "alter":
                key = col.stored
                if key == "data":
                    key = name
                val = data.get(key, None)
                if val is None:
                    raise InvalidParameter("Object load: missing '" + key + "'")

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
        else:
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
            if not key_name in crypto:
                raise CipherException("Cannot find key ({}) to decrypt data?".format(key_name))
            return crypto[key_name]['cipher'].key_decrypt(data[6:])
        else:
            return data[6:]

    ############################################################################
    def authorized(self, action, attrs, sensitive=False, raise_error=True):
        """Cross reference ABAC policy for attrs and action"""

        attrs['action'] = action
        attrs['sensitive'] = sensitive
        attrs['obj_type'] = self.table
        attrs['obj'] = self.obj
        for act in (action, 'admin'):
            for policy_id in self.policies[act]:
                if self.policies[act][policy_id].allowed(attrs, raise_error=raise_error):
                    return True
        return False

    ############################################################################
    def map_soft_relationships(self, dbi):
        """
        Review all object relationship arrays and revise them as:

            name[.id]

        If .id is an integer it should map to an existing object, and is given
        lookup preference (ignoring name).  It may also be .notfound, at which
        point it is a bad reference, but is left in the array. Example:

            fin-kfs.14314
            fin-kfs.notfound

        Returns array of errors (if there were any).  An empty array is success.
        """

        # parent placeholder, just return an array
        return list()

    ############################################################################
    def _map_soft_relationship_name2id(self, dbi, table, target):
        """
        Internal function called for a single table and target.
        """
        obj_id = table.name2id_direct(target, dbi)
        target = target.split(".", 1)[0]
        if obj_id:
            return (target + "." + str(obj_id), '')
        else:
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
            pipe_id = class_obj.name2id_direct(target, dbi)
            svc_obj = Service(clone=self.master)
            sql = "SELECT id, name FROM Service WHERE pipeline_id = ?"
            for svc_id, svc_name in dbi.do_getlist(sql, pipe_id):
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

    ############################################################################
    def _delete_policyfor(self, dbi):
        """Delete PolicyFor pertaning to this object"""
        dbi.do("""DELETE FROM PolicyFor
                  WHERE obj = ? AND target_id = ?
               """, self.table, self.obj['id'])

    ############################################################################
    def deleted(self, attrs, dbi=None):
        """
        Any actions or updates required on delete of this object
        """
        self._delete_policyfor(dbi=dbi)

    ############################################################################
    def changed(self, attrs, dbi=None):
        """
        Any actions or updates required on change of this object
        """
        matchlist = policymatch_get_cached(self.master.cache, dbi, 'targetted')
        self.map_targetted_policies(matchlist, dbi=dbi)
        return list()

    #############################################################################
    def map_targetted_policies(self, matchlist, dbi=None):
        """
        Review policies and match to this object
        """

        self._delete_policyfor(dbi)
        context = dict(obj=self.obj, obj_type=self.table)
        for pmatch in matchlist:
            policymatch_map_for(pmatch, dbi, context, self.table, self.obj['id'])

################################################################################
class Pipeline(RCObject):
    """
    DROP> drop table if exists Pipeline;
     ADD> create table Pipeline (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     title varchar(255) not null,
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
     ADD>     title varchar(255) not null,
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

        self.omap['title'] = RCMap(stored='title')
        self.omap['contacts'] = RCMap(stored="data",
                                      dtype=dict,
                                      stype="opt")
        self.omap['launch'] = RCMap(stored="data",
                                    dtype=dict,
                                    stype="opt")
        self.omap['monitor'] = RCMap(stored="data", dtype=dict, stype="opt")
        super(Pipeline, self).__init__(*args, **kwargs)

################################################################################
class Service(RCObject):
    """
    DROP> drop table if exists Service;
     ADD> create table Service (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     title varchar(255) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     stage varchar(32) not null,
     ADD>     region varchar(32) not null,
     ADD>     pipeline_id int not null,
     ADD>     config_id int not null,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    DROP> drop table if exists ServiceArchive;
     ADD> create table ServiceArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     title varchar(255) not null,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     data text,
     ADD>     stage varchar(32) not null,
     ADD>     region varchar(32) not null,
     ADD>     pipeline_id int not null,
     ADD>     config_id int not null,
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
     ADD>     data text,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;
    """
    # a list of object attributes which are part of the actual db object

    table = 'Grp'

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['group'] = RCMap(stored="data", dtype=list)
        self.omap['type'] = RCMap(stored="data", dtype="value")
        super(Group, self).__init__(*args, **kwargs)

    ############################################################################
    def validate(self):
        errors = super(Group, self).validate()

        if self.obj['type'] not in ('Apikey', 'Pipeline'):
            raise ValueError("Invalid type=" + self.obj['type'] + " not one of: Apikey, Pipeline")

        return errors

    ############################################################################
    def map_soft_relationships(self, dbi):
        """map out my relationships"""
        errors = super(Group, self).map_soft_relationships(dbi)

        # todo: look for better option
        # pylint: disable=eval-used
        class_obj = eval(self.obj['type'])(clone=self)
        mapped = set()
        for target in self.obj['group']:
            (target, error) = self._map_soft_relationship_name2id(dbi,
                                                                  class_obj,
                                                                  target)
            if error:
                errors.append(error)
            mapped.add(target)

        self.obj['group'] = list(mapped)

        return errors

################################################################################
class Apikey(RCObject):
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
     ADD> insert into Apikey set id=100, uuid=uuid(), name='master', secrets='[]', data='{}';
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
        if not len(self.obj.get('name', '')):
            return ["Object load: missing 'name'"]
        return []

    ############################################################################
    @db_interface
    def create(self, attrs, dbi=None):
        """
        create new apikey
        """

        if self.obj.get('id'):
            raise InvalidParameter("id must be left undefined on apikey creation")

        self.obj['uuid'] = str(uuid.uuid4())
        self.obj['secrets'] = [base64.b64encode(nacl.utils.random(self.keysize)).decode()]

        return self._put(attrs, dbi=dbi)

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
        dbi.do("""
            INSERT INTO AuthSession
               SET token_id=?,
                   name=?,
                   secret=?,
                   expires_at=?,
                   session_data=?
            """, tok_id, session_id, secret_encoded, expires, data_txt)

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
     ADD>     primary key(id)
     ADD> ) engine=InnoDB;
     ADD> INSERT INTO Policy SET id=100, name='master', policy='token_name=="master"', data='{}';

    DROP> drop table if exists PolicyArchive;
     ADD> create table PolicyArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy text not null,
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;
     ADD>

    DROP> DROP TRIGGER IF EXISTS archive_Policy;
     ADD> CREATE TRIGGER archive_Policy BEFORE UPDATE ON Policy
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO PolicyArchive SELECT * FROM Policy WHERE NEW.id = id;
    """

    table = 'Policy'
    vardata = True

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['policy'] = RCMap(stype="alter", stored='policy')
        super(Policy, self).__init__(*args, **kwargs)

    #############################################################################
    # merge in w/abac.Policy validation
    # keep pre-rx version and post, for subsequent editing
    def validate(self):
        errors = super(Policy, self).validate()
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

        dbi.do("""DELETE FROM PolicyFor WHERE policy_id = ?""", self.obj['id'])
        dbi.do("""DELETE FROM Policymatch WHERE policy_id = ?""", self.obj['id'])
        self.master.cache.clear_type('policymap')

        return errors

################################################################################
def policymatch_get_cached(cache, dbi, mtype):
    """get a list of policymatch objects, checking cache first"""
    matchlist = cache.get_cache('policymatch', mtype)
    if matchlist:
        return matchlist
    return policymatch_get_direct(cache, dbi, mtype)

################################################################################
def policymatch_get_direct(cache, dbi, mtype):
    """get a list of policymatch objects directly from db, and update cache"""
    matchlist = list()
    cursor = dbi.do("""SELECT id,policy_id,matches,actions
                         FROM Policymatch
                        WHERE type = ?""", mtype)
    for row in cursor:
        matchlist.append(row_to_dict(cursor, row))
    cache.set_cache('policymatch', mtype, matchlist)
    return matchlist

################################################################################
def policymatch_map_for(pmatch, dbi, context, table, target_id):
    """"map poicymatch objects into PolicyFor table"""
    try:
        # pylint: disable=eval-used
        if eval(pmatch['matches'], {'__builtins__':{}, 'rx': re}, context):
            for action in pmatch['actions'].split(","):
                dbi.do("""REPLACE INTO PolicyFor
                          SET obj = ?, policy_id = ?, target_id = ?,
                              pmatch_id = ?, action = ?
                       """, table, pmatch['policy_id'], target_id,
                       pmatch['policy_id'], action)
    except: # pylint: disable=bare-except
        if do_DEBUG():
            log("error", traceback=traceback.format_exc())
        trace(traceback.format_exc())

################################################################################
class Policymatch(RCObject):
    # pylint: disable=line-too-long
    """
    DROP> drop table if exists Policymatch;
     ADD> create table Policymatch (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy_id int not null,
     ADD>     type enum('targetted', 'global') not null default 'targetted',
     ADD>     matches text not null,
     ADD>     actions varchar(64) not null,
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     primary key(id),
     ADD>     unique(name)
     ADD> ) engine=InnoDB;

    DROP> drop table if exists PolicymatchArchive;
     ADD> create table PolicymatchArchive (
     ADD>     id int auto_increment not null,
     ADD>     name varchar(64) not null,
     ADD>     policy_id int not null,
     ADD>     type enum('targetted', 'global') not null default 'targetted',
     ADD>     matches text not null,
     ADD>     actions varchar(64) not null,
     ADD>     data text,
     ADD>     updated_at timestamp not null,
     ADD>     updated_by varchar(32) not null,
     ADD>     index(id, updated_at),
     ADD>     index(id)
     ADD> ) engine=InnoDB;
     ADD>

    DROP> DROP TRIGGER IF EXISTS archive_Policymatch;
     ADD> CREATE TRIGGER archive_Policymatch BEFORE UPDATE ON Policymatch
     ADD>   FOR EACH ROW
     ADD>     INSERT INTO PolicymatchArchive SELECT * FROM Policymatch WHERE NEW.id = id;

    DROP> drop table if exists PolicyFor;
     ADD> create table PolicyFor (
     ADD>     policy_id int not null,
     ADD>     obj enum('Pipeline', 'Service', 'Config', 'Instance', 'Policy', 'Policymatch', 'Apikey', 'Build', 'Grp'),
     ADD>     action enum('write', 'read', 'admin') default 'read',
     ADD>     pmatch_id int not null default 0,
     ADD>     target_id int not null default 0,
     ADD>     primary key(obj, policy_id, target_id),
     ADD>     index(obj, target_id),
     ADD>     index(obj)
     ADD> ) engine=InnoDB;
    """

    table = 'Policymatch'
    policy_map = False
    vardata = True

    def __init__(self, *args, **kwargs):
        self.omap = dictlib.Obj()
        self.omap['policy_id'] = RCMap(stype="alter", stored='policy_id')
        self.omap['matches'] = RCMap(stype="alter", stored='matches')
        self.omap['actions'] = RCMap(stype="alter", stored='actions')
        self.omap['type'] = RCMap(stype="alter", stored='type')
        super(Policymatch, self).__init__(*args, **kwargs)


    #############################################################################
    # merge in w/abac.Policy validation
    # keep pre-rx version and post, for subsequent editing
    def validate(self):
        errors = super(Policymatch, self).validate()
        try:
            compile(self.obj['matches'], '<matches>', 'eval')
            # Future note: put in an eval here with mock data
        except SyntaxError as err:
            raise InvalidParameter("Cannot prepare match expression: " + err.args[0] +
                                   "\n" + "Character {}: {}".format(err.offset, err.text))
        except TypeError as err:
            raise InvalidParameter("Cannot prepare match expression: " + str(err))

        if self.obj['type'].lower() not in ('targetted', 'global'):
            raise InvalidParameter("Policy Match Type is not one of: global, targetted")

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
        errors = super(Policymatch, self).changed(attrs, dbi=dbi)

        # direct since we changed
        matchlist = policymatch_get_direct(self.master.cache,
                                           dbi,
                                           self.obj['type'])

        # invalidate all cached data for policy maps
        self.master.cache.clear_type('policymap')

        if self.obj['type'] == 'targetted':
            for table in Schema.tables:
                if not table.policy_map:
                    continue
                tobj = table(master=self.master)

                # build the list in memory, so we can re-use the cursor
                memarray = list()
                cursor = dbi.do("SELECT id,name FROM " + tobj.table)
                for row in cursor:
                    memarray.append(dict(id=row[0], name=row[1].decode('utf-8')))

                # now map out the policies
                for row in memarray:
                    tobj.obj = row
                    tobj.map_targetted_policies(matchlist, dbi=dbi)

        else: # global
            context = dict(obj={}, obj_type='')
            for pmatch in matchlist:
                for table in Schema.tables:
                    if not table.policy_map:
                        continue
                    obj = table(master=self.master)
                    context['obj'] = obj
                    context['obj_type'] = obj.table
                    dbi.do("""DELETE FROM PolicyFor
                              WHERE obj = ? AND pmatch_id = ? AND policy_id = ?
                           """, obj.table, pmatch['id'], pmatch['policy_id'])
                    policymatch_map_for(pmatch, dbi, context, obj.table, 0)

        return errors

################################################################################
class Schema(rfx.Base):
    """Define our DB schema as code"""
    # NOTE: update PolicyFor and PolicyActive table enums if adding to this list
    # also: order matters
    tables = [Policy, Policymatch, Pipeline, Service, Config, Instance, Apikey,
              Build, Group, AuthSession]
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
        """initialize the db (create everything)"""
        dbi.connect()

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
            for line in obj.__doc__.split("\n"):
                if reset:
                    match = re.search(r'^\s+(ADD|DROP)> *(.*)$', line)
                else:
                    match = re.search(r'^\s+(ADD)> *(.*)$', line)
                if match:
                    schema.append(match.group(2))
            for stmt in "\n".join(schema).split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                dbi.dbc.cmd_query(stmt)

        if reset or new_master:
            pmatch = Policymatch(master=self.master)
            pmatch.load({
                'name': 'master',
                'matches': 'True',
                'policy_id': 100,
                'actions': 'admin',
                'type': 'global'
            })
            pmatch.create(abac.MASTER_ATTRS)

            secret = base64.b64encode(nacl.utils.random(Apikey.keysize)).decode()
            dbi.do("""
                UPDATE Apikey
                   SET secrets = ?
                 WHERE id = 100
            """, json4store([secret]))

            log("Initializing schema master apikey, <CHANGE>",
                REFLEX_APIKEY="master." + secret)