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
MySQL plugin for db abstract.  Supports MariaDB as well.

Uses Prepared Cursors by default.
"""

import time
import traceback
import mysql.connector
from rfxengine import log#, trace
from rfxengine.db import pool

def row_to_dict(cursor, row):
    """Zip a resulting row with its column names into a dictionary"""
    return dict(zip(decode_row(cursor.column_names), decode_row(row)))

def decode_row(row):
    """
    Mysqlconnector returns bytes for strings sometimes.  Lame!
    """
    return tuple([elem.decode('utf-8') if isinstance(elem, bytes) else elem for elem in row])

################################################################################
# pylint: disable=too-few-public-methods
class OutputSingle(object):
    """
    Object for an enumerated option value instead of a string (more efficient).

    Used with Interface.do_getlist()
    """
    pass

################################################################################
class Master(pool.Master):
    """
    Init is called with config as sent in params to mysql.connector
    """

    ############################################################################
    def new_interface(self, iid):
        """Open a new connection"""
        super(Master, self).new_interface(iid)
        return Interface(master=self, iid=iid).connect()

################################################################################
class Interface(pool.Interface):
    """MySQL Db Abstraction Interface Object"""
    _last_used = 0
    max_idle_time = 3600 # reconnect after 1 hour
    dbc = None

    ############################################################################
    def __init__(self, **kwargs):
        kwargs['dbc'] = None
        super(Interface, self).__init__(**kwargs)

    ############################################################################
    def __del__(self):
        self.close()

    ############################################################################
    def acquire(self):
        """use this connection"""
        self._last_used = time.time()
        return super(Interface, self).acquire()

    ############################################################################
    def close(self):
        """Closes this database connection."""
        if self.dbc:
            self.dbc.close()
            super(Interface, self).close()
            self.dbc = None

    ############################################################################
    def expired(self):
        """is the instance expired?"""
        return self.dbc and ((time.time() - self._last_used) > self.max_idle_time)

    ############################################################################
    # called by heartbeat
    def alive(self):
        """Called by heartbeat to periodically reap older connections"""
        if self.expired():
            self.close()
            return False
        return True

    ############################################################################
    def connect(self):
        """Wrap MySQL connect to handle idle connections"""
        # Mysql closes idle connections, but we have no easy way of knowing
        # until we try to use it, so set our idle time to be less than the
        # setting on the server. Sync this time with the settings.
        if self.expired():
            self.close()

        while not self.dbc:
            try:
                # use_pure=False is asserting to use the native-c build
                self.dbc = mysql.connector.connect(use_pure=False, **self.master.config)
            except Exception as err: # pylint: disable=broad-except
                if self.do_DEBUG('db'):
                    log("Connect Problem, waiting...", traceback=traceback.format_exc(),
                        type="error")
                else:
                    log("Connect Problem, waiting...", error=str(err), type="error")
                time.sleep(1)

        self.dbc.autocommit = True
        self._last_used = time.time()
        return self

    ############################################################################
    # pylint: disable=invalid-name
    def prepare(self, stmt):
        """prepare a cursor and statement"""

        attempts = 3
        while True:
            attempts -= 1
            self.connect()
            try:
                cursor = self.dbc.cursor()
                stmt = stmt.replace("?", "%s")
                return cursor, stmt
            except mysql.connector.errors.OperationalError:
                self.close()
                # retry a few times
                if attempts > 0:
                    time.sleep(1)
                else: # or give up
                    raise

    ############################################################################
    # pylint: disable=invalid-name
    def do(self, stmt, *args):
        """Run a statement, return the cursor."""
        try:
            cursor, stmt = self.prepare(stmt)
        except mysql.connector.errors.InternalError as err:
            # bad coding, but this avoids blowing out future connections
            if str(err) == "Unread result found":
                self.close()
            raise
        except mysql.connector.errors.OperationalError as err:
            log("db error", error=str(err), type="error")
            self.close()
            raise

        cursor.execute(stmt, args)
        return cursor

    ############################################################################
    def do_count(self, stmt, *args):
        """Do action and return the # rows changed."""
        cursor = self.do(stmt, *args)
        rows = cursor.rowcount
        cursor.close()
        return rows

    ############################################################################
    def do_lastid(self, stmt, *args):
        """Do action and return the last insert id (if there is one)."""
        cursor = self.do(stmt, *args)
        last = cursor.lastrowid
        cursor.close()
        return last

    ############################################################################
    def do_getlist(self, stmt, *args, output=list): #, dslice=None):
        """
        Execute and fetch a list of lists.
        Should only be used on small data sets.

        output=list, dict, or OutputSingle (default is list).  This attribute
        describes how to handle the elements of the list.  If OutputSingle is
        specified, then the first element of each row is flattened into a single
        dimensional list.
        """
        cursor, stmt = self.prepare(stmt)
        stmt = stmt.replace("?", "%s")
        cursor.execute(stmt, args)

        output = list()
        for row in cursor:
            if output == OutputSingle:
                result = row[0]
            elif output == dict:
                result = row_to_dict(cursor, row)
            else:
                result = decode_row(row)

            output.append(result)

        cursor.close()

        return output

    ############################################################################
    def do_getone(self, stmt, *args, output=dict):
        """execute and fetch one row"""
        # pull one row
        try:
            if "LIMIT" not in stmt: # doesn't match both cases; follow convention
                stmt += " LIMIT 1" # or do a cursor.fetchall()
            cursor, stmt = self.prepare(stmt)
            cursor.execute(stmt, args)
            result = cursor.fetchone()
            if result:
                if output == dict:
                    result = row_to_dict(cursor, result)
                else:
                    result = decode_row(result)
        except mysql.connector.errors.InternalError as err:
            # bad coding, but this avoids blowing out future connections
            if str(err) == "Unread result found":
                self.close()
            raise
        except StopIteration:
            result = dict()

        cursor.close()
        return result
