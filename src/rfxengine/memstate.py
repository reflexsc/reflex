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
Memory Cache Management.  Derived from onetimejwt
"""

import time
import threading
import timeinterval
#from rfxengine import trace

################################################################################
def mutex(func):
    """use a thread lock on current method, if self.lock is defined"""
    def wrapper(*args, **kwargs):
        """Decorator Wrapper"""
        lock = args[0].lock
        lock.acquire(True)
        try:
            return func(*args, **kwargs)
        except:
            raise
        finally:
            lock.release()

    return wrapper

################################################################################
class Cache(object):
    """
    Threadsafe generic cache.

    Meant to be configured after initialization.  Supports deeper objects
    (such as db master) having a cache, but letting objects configure what
    is cached as they are supported.
    """

    cache = None
    ctypes = None
    lock = threading.Lock()

    ############################################################################
    def __init__(self, **kwargs):
        self.cache = dict()
        self.ctypes = dict()
        config = {
            'policy': 300,
            'policymap': 300,
            'policyscope': 300,
            'session': 300,
            'groups': 300
        }
        config.update(kwargs)
        for ctype in config:
            if ctype == 'housekeeper':
                continue
            self.configure(ctype, config[ctype])
        self._clean()

    ############################################################################
    def start_housekeeper(self, interval):
        """startup the housekeeper interval"""
        timeinterval.start(interval * 1000, self._clean)

    ############################################################################
    def configure(self, ctype, age):
        """configure a parameter for cache"""
        self.ctypes[ctype] = age
        self.cache[ctype] = dict()

    ############################################################################
    @mutex
    def _clean(self):
        """
        Run by housekeeper thread, cleans out stale cache items.

        If this becomes a bottleneck by taking too long, then change it
        so that only the call to delete is threadsafe, instead of the overall
        process - BJG
        """
        if not self.ctypes:
            return
        now = time.time()
        for ctype in ['policy']: # self.ctypes:
            keys = list(self.cache[ctype].keys()) # to avoid RuntimeError
            for key in keys:
                item = self.cache[ctype].get(key)
                if item and item['expires'] < now:
                    del self.cache[ctype][key]

    ############################################################################
    # pylint: disable=unused-argument
    @mutex
    def remove_cache(self, ctype, key, start=None):
        """remove an item from the cache"""
        if self.cache[ctype].get(key):
#DEBUG#            trace("CACHE REMOVE {} {}".format(ctype, key))
            del self.cache[ctype][key]

    def clear_type(self, ctype):
        """remove an item from the cache"""
#DEBUG#       trace("CACHE CLEAR {}".format(ctype))
        self.cache[ctype] = dict()

    def get_cache(self, ctype, key, start=None):
        """get an item from the global mutex-safe cache"""
        if not start:
            start = time.time()
        item = self.cache[ctype].get(key)
        if item and item['expires'] > start:
#DEBUG#           trace("CACHE HIT {} {}".format(ctype, key))
            return item['value']
#DEBUG#       trace("CACHE MISS {} {}".format(ctype, key))
        return None

    @mutex
    def set_cache(self, ctype, key, value, base_time=None):
        """Set an item in the global mutex-safe cache"""
        if not base_time:
            base_time = time.time()
#DEBUG#       trace("CACHE SET {} {}".format(ctype, key))
        expires = base_time + self.ctypes[ctype]
        self.cache[ctype][key] = dict(expires=expires, value=value)
        return expires
