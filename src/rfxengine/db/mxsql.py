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
#import traceback
import mysql.connector
from mysql.connector import errors
from rfxengine import log
from rfxengine.db import pool

def row_to_dict(cursor, row):
    """Zip a resulting row with its column names into a dictionary"""
    return dict(zip(cursor.column_names, decode_row(row)))

def decode_row(row):
    """
    Mysqlconnector returns bytearrays for strings, in order to maintain
    compatabiilty w/python 2.  Lame!
    """
    return tuple([elem.decode('utf-8') if isinstance(elem, bytearray) else elem for elem in row])

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
        return Interface(master=self, iid=iid)

################################################################################
class Interface(pool.Interface):
    """MySQL Db Abstraction Interface Object"""
    _last_used = 0
    max_idle_time = 3600 # reconnect after 1 hour
    dbc = None

    ############################################################################
    def __init__(self, **kwargs):
        master = kwargs['master']
        kwargs['dbc'] = None

        while not kwargs['dbc']:
            try:
                kwargs['dbc'] = mysql.connector.connect(**master.config)
            except (errors.ProgrammingError, errors.InterfaceError) as err:
                log("Connect Problem, waiting...", error=str(err))
                time.sleep(1)

        super(Interface, self).__init__(**kwargs)

    ############################################################################
    def __del__(self):
        self.close()

    ############################################################################
    def close(self):
        """Closes this database connection."""
        if self.dbc:
            self.dbc.close()
            self.dbc = None

    ############################################################################
    def connect(self):
        """Wrap MySQL connect to handle idle connections"""
        # Mysql closes idle connections, but we have no easy way of knowing
        # until we try to use it, so set our idle time to be less than the
        # setting on the server. Sync this time with the settings.
        self.DEBUG("interface.connect()")
        if self.dbc and (time.time() - self._last_used > self.max_idle_time):
            self.close()

        if not self.dbc:
            self.dbc = mysql.connector.connect(**self.master.config)
            self.dbc.autocommit = True
            self._last_used = time.time()

    ############################################################################
    # pylint: disable=invalid-name
    def do(self, stmt, *args):
        """Run a statement, return the cursor."""
        self.connect()
        try:
            cursor = self.dbc.cursor(prepared=True)
        except mysql.connector.errors.InternalError as err:
            # bad coding, but this avoids blowing out future connections
            if str(err) == "Unread result found":
                self.close()
            raise
        except mysql.connector.errors.OperationalError as err:
            log("db error", error=str(err))
            self.close()
            raise

        cursor.execute(stmt, args)
        return cursor

    ############################################################################
    def do_count(self, stmt, *args):
        """Do action and return the # rows changed."""
        return self.do(stmt, *args).rowcount

    ############################################################################
    def do_lastid(self, stmt, *args):
        """Do action and return the last insert id (if there is one)."""
        return self.do(stmt, *args).lastrowid

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
        self.connect()
        cursor = self.dbc.cursor(prepared=True)
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

        return output

    ############################################################################
    def do_getone(self, stmt, *args, output=dict):
        """execute and fetch one row"""
        self.connect()
        cursor = self.dbc.cursor(prepared=True)
        cursor.execute(stmt, args)

        # pull one row
        try:
            if output == dict:
                result = row_to_dict(cursor, cursor.next())
            else:
                result = decode_row(cursor.next())
        except StopIteration:
            result = dict()

        return result

    ############################################################################
    # pylint: disable=unused-argument
    def cursor(self, *args, **kwargs):
        """Return a cursor"""
        self.connect()
        kwargs['prepared'] = True
        return self.dbc.cursor(prepared=True)
