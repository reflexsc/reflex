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
# pylint: disable=invalid-name
# pylint: skip

"""
Database Abstraction (DBA)

Simple wrapper for db connection pooling supporting multithreaded I/O,
without the anti-pattern of ORM.

A single instance of the Master object should be created and referenced to
get individual connections.  Connections are pooled for performance through
this means.

Example:

    dbm = Master({connect info}) # create a new db master
    dbi = dbm.connect()            # get a new interface
    cursor = dbi.dbc.cursor()      # get a cursor

Typically these objects are extended for each database type, providing more
functionality at the dbi layer.
"""

import threading
import rfx
from rfx.crypto import Key, Cipher
#from rfxengine import trace

################################################################################
# errors
class DbConnect(Exception):
    """We had a problem houston"""
    pass

class DbItemNotFound(Exception):
    """Could not find item"""
    pass

class DbSaveFailed(Exception):
    """Could not save item"""
    pass

################################################################################
# decorator
def db_interface(func):
    """
    Decorator to pull a db interface and send it onto the method as a named arg.
    Returns the dbinterface after method closes.
    """
    def dbi_wrapper(self, *args, **kwargs):
        """Decorator Wrapper"""
        if 'dbi' not in kwargs or not kwargs['dbi']:
            kwargs['dbi'] = self.master.connect()
        try:
            result = func(self, *args, **kwargs)
        finally:
            kwargs['dbi'].done()

        return result

    return dbi_wrapper

################################################################################
# classes
class Interface(rfx.Base):
    """Generic database interface object"""
    thread = None
    dbc = None # db connection
    iid = 0
    master = None

    ############################################################################
    # pylint: disable=super-init-not-called,unused-argument
    def __init__(self, *args, iid=0, master=None, dbc=None, **kwargs):
        super(Interface, self).__inherit__(master)

        self.dbc = dbc
        self.iid = iid
        self.master = master

    ############################################################################
    def is_free(self):
        """If the thread is in use"""
        if self.thread:
            return not self.thread.is_alive()
        return True

    ############################################################################
    def is_open(self):
        """If the thread is open"""
        if self.dbc:
            # could do more
            return True
        return False

    ############################################################################
    def acquire(self):
        """Grab this interface object if it is free, remembers the thread"""
        if self.is_free():
            self.thread = threading.current_thread()
            return True
        return False

    ############################################################################
    def done(self):
        """A thread is letting go"""
        self.thread = None
        self.master.done(self)

    ############################################################################
    def close(self):
        """Delete an interface from the pool"""
        self.done()
        self.master.close(self)

    ############################################################################
    def new_interface(self):
        """Placeholder to allow for inheritance"""
        pass

################################################################################
# Todo: figure out a way to recognize a dead connection and remove it from the
# pool.
class Master(rfx.Base):
    """Master database pool handler"""
    mutex = threading.Lock()
    free = set() # collections.deque()
    pool = None
    #master = None
    ids = 0
    config = None
    crypto = None
    default_key = None
    cache = None # optional memstate.Cache

    ############################################################################
    # pylint: disable=super-init-not-called
    #
    # Master(base=base, config={db config}, crypto={
    #     'xxx': {
    #         'key': "base64 encoded key",
    #         'default': True
    #     }
    # })
    #

    def __init__(self, **kwargs):
        self.pool = dict()
        self.config = dict()

        if 'base' in kwargs:
            super(Master, self).__inherit__(kwargs['base'])

        if 'config' in kwargs:
            self.config = kwargs['config']
            del kwargs['config']

        defaults = {}
        if 'crypto' in kwargs:
            self.crypto = dict()
            for name in kwargs['crypto']:
                self.NOTIFY("crypto initializing key=" + name)
                self.crypto[name] = dict()
                if len(name) != 3:
                    raise ValueError("Crypto names must be 3 characters long")
                key = kwargs['crypto'][name]['key']
                default = kwargs['crypto'][name].get('default', False)
                if default:
                    defaults[name] = True
                    default = True # fix values
                else:
                    default = False # fix values
                keyObj = Key(key)
                self.crypto[name]['cipher'] = Cipher(keyObj)
                self.crypto[name]['default'] = default

            if len(defaults.keys()) > 1:
                raise ValueError("Only one default key may be defined")
            if not defaults:
                raise ValueError("No default crypto key defined?")
            self.default_key = list(defaults.keys())[0]
        self.NOTIFY("crypto default key={}".format(self.default_key))


    ############################################################################
    def connect(self):
        """Thread safe wrapper"""
        self.mutex.acquire(True)

        # pull the next free connection
#        trace("pool.connect(): looking for free interfaces")
        while self.free:
            dbi = self.free.pop()
            if dbi.acquire(): # this should always be true
#                trace("{} pool.connect(): found one!".format(dbi.iid))
                return self.give(dbi)

        # scan existing connections for availability
        # NOTE: this should be a housekeeping task--it exists for when
        # a thread forgot to release its DBI
        for dbi_ref in self.pool:
            dbi = self.pool[dbi_ref]
            if dbi.acquire():
#                trace("{} pool.connect(): unused dbi?".format(dbi.iid))
                return self.give(dbi)

        # make a new interface object
        # may want a way to throttle this in the future
        self.ids += 1
        dbi = self.new_interface(iid=self.ids)
#        trace("{} pool.connect(): create dbi".format(dbi.iid))
        self.pool[dbi.iid] = dbi # pylint: disable=no-member
        if dbi.acquire(): # pylint: disable=no-member
            return self.give(dbi)

        self.mutex.release()
        raise DbConnect("Cannot get free db connection")

    ############################################################################
    # pylint: disable=unused-argument,no-self-use
    def new_interface(self, iid):
        """Override on descendant objects"""
        return {}

    ############################################################################
    def close(self, dbi):
        """Delete an interface from the pool"""
#        trace("{} discard DBI".format(dbi.iid))
        self.free.discard(dbi)
        del self.pool[dbi.iid]

    ############################################################################
    def done(self, dbi):
        """Release an interface back into the pool"""
#        trace("{} available DBI".format(dbi.iid))
        self.free.add(dbi)

    ############################################################################
    def give(self, dbi):
        """Let go of the pool lock and return the interface"""
#        self.DEBUG("Giving DBI {}".format(dbi.iid))
        self.mutex.release()
        return dbi
