#$#HEADER-START
# vim:set expandtab ts=4 sw=4 ai ft=python:
#
#     Reflex Service Configuration Event Engine
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
General reflex module and controls
"""

import io
import os
import os.path
import pprint # used by debug
import sys
import time
import re
import copy
import fcntl # get_my_ips, unbuffered
import struct # get_my_ips
import socket # get_my_ips
import threading
import traceback
import subprocess
import base64
from stat import S_IRWXU # LOG2FILE
import requests
import nacl.utils
import jwt
import ujson as json
# cipher bits
from rfx import crypto

# find the reflex base install folder based on this lib
BASEDIR = os.path.realpath(os.path.dirname(__file__)).rsplit("/", 1)[0]
while BASEDIR and not os.path.exists(BASEDIR + "/.pkg/version"):
    BASEDIR = BASEDIR.rsplit("/", 1)[0]

################################################################
def json4human(data):
    """Json output for humans"""
    return json.dumps(data, escape_forward_slashes=False, indent=2, sort_keys=True)

def json4store(data, **kwargs):
    """Json output for storage"""
    return json.dumps(data, escape_forward_slashes=False, **kwargs)

def json2data(string):
    """json to its python representation"""
    return json.loads(string)

################################################################
def threadlock(func):
    """use a thread lock on current method, if self.lock is defined"""
    def threadlock_wrapper(*args, **kwargs):
        """Decorator Wrapper"""
        lock = args[0].lock
        lock and lock.acquire(True) # pylint: disable=expression-not-assigned
        try:
            result = func(*args, **kwargs)
            lock and lock.release() # pylint: disable=expression-not-assigned
        except:
            lock and lock.release() # pylint: disable=expression-not-assigned
            raise

        return result

    return threadlock_wrapper

################################################################
def get_my_ips():
    """highly os specific - works only in modern linux kernels"""
    ips = list()
    for ifdev in sorted(os.listdir("/sys/class/net")):
        if ifdev == "lo":
            continue
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ips.append(socket.inet_ntoa(fcntl.ioctl(
                sock.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack('256s', ifdev[:15].encode())
            )[20:24]))
        except OSError:
            pass
    return ips

################################################################
# pylint: disable=too-few-public-methods
class Colorize(object):
    """Enhance an object with ANSI colorizing"""

    colorize = False
    ansi_codes = {
        'start': "\033[",
        'fgblue': '34m',
        'fgred': '31m',
        'fggreen': '32m',
        'fggray': '30m',
        'reset': '0m',
    }

    ############################################################
    def ansi_code(self, code):
        """
        If colorizing is enabled, return the requested ansi code
        """
        if not self.colorize:
            return ''
        return self.ansi_codes['start'] + self.ansi_codes[code]

################################################################
# pylint: disable=too-many-instance-attributes, too-many-public-methods
class Base(Colorize):
    """Base object where common and global attributes are defined"""

    cfg = {
        "REFLEX_URL": "",
        "REFLEX_APIKEY": ""
    }
    cmd = 'reflex'
    logfmt = 'txt'
    logfd = sys.stderr
    outputfd = sys.stdout
    notifyfd = sys.stderr
    debug = {'basic':False}
    timestamp = False
    term_width = 80
    lock = None # used with threadsafe=True init arg
    my_ip = ''
    my_ips = None
    my_host = ''

    # global static. DO NOT CHANGE. This should be enhanced with a local secret file
    secret_seed = "Y+8CcVDNH/HWt1nDuNPrdl0npPQYwrPZ3ZqSMtutbso="

    ############################################################
    def __inherit__(self, base):
        """To avoid multiple config loads, call __inherit__ to inherit a parent's previous load"""
        self.logfmt = base.logfmt
        self.DEBUG("rfx.Base.__inherit__(base={0})".format(base))
        self.debug = base.debug
        self.cfg = base.cfg
        self.timestamp = base.timestamp
        self.logfd = base.logfd
        self.outputfd = base.outputfd
        self.notifyfd = base.notifyfd
        # do not copy self.lock

        return self

    # pylint: disable=unused-argument
    def __init__(self, *args, **kwargs):
        self.logfmt = kwargs.get('logfmt', 'txt')
        os.environ['LOGFMT'] = self.logfmt
        #self.DEBUG("rfx.Base.__init__()")
        if 'debug' in kwargs and kwargs['debug']:
            for module in kwargs['debug']:
                self.debug[module] = True

        # implement a mutex if requested
        if kwargs.get('threadsafe'):
            self.lock = threading.Lock()

    ############################################################
    def get_my_nameip(self):
        """Try to get my own IP, capture errors"""
        self.my_host = socket.gethostname()
        try:
            self.my_ips = get_my_ips()
            self.my_ip = self.my_ips[0]
        except: # pylint: disable=bare-except
            try:
                self.my_ip = socket.gethostbyname(self.my_host)
                self.my_ips = [self.my_ip]
            except: # pylint: disable=bare-except
                self.NOTIFY("Not able to identify my own address")
                self.my_ip = ''
                self.my_ips = []

    ############################################################
    # terminal size
    # pylint: disable=no-self-use
    def get_terminal_width(self):
        """return the terminal height, width"""
        sub = subprocess.Popen(["stty", "size"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        sub.wait()
        if sub.returncode > 0:
            return
        try:
            width = sub.stdout.read().split()[1]
            self.term_width = int(width) or 80
        except IndexError: # pylint: disable=broad-except
            self.NOTIFY("Unable to calculate terminal width")

    ############################################################
    def cfg_load(self):
        """Load state config data"""
        try:
            self.DEBUG("Config Load:", data=self.cfg, module='config')
            cfg_path = os.environ['HOME'] + '/.reflex/cfg'
            if os.path.exists(cfg_path):
                with open(cfg_path, 'rb') as in_file:
                    data = in_file.read().decode()

                #vers = data[:4]
                data = data[4:]

                # do something with version here
                key = crypto.Key(self.cfg_secret())
                cipher = crypto.Cipher(key)
                self.cfg = json.loads(cipher.key_decrypt(data).encode())

            else:
                self.DEBUG("No .reflex/cfg", module='config')

            # then pull environment settings, as they have higher priority
            self._env2cfg('REFLEX_URL')
            self._env2cfg('REFLEX_APIKEY')
        except ValueError as err:
            self.NOTIFY("Unable to load config!")
        except IOError as err:
            self.NOTIFY("Unable to load config!")
            self.DEBUG("Reason: " + str(err))

        if not self.cfg.get('REFLEX_URL') or not self.cfg.get('REFLEX_APIKEY'):
            self.NOTIFY("neither REFLEX_URL nor REFLEX_APIKEY configured, try: reflex setup wizard")
        return self

    ############################################################
    def cfg_save(self):
        """Save config to file"""
        base = os.environ['HOME'] + '/.reflex'
        if not os.path.exists(base):
            os.mkdir(base)
            os.chmod(base, 0o0700)
        elif not os.path.isdir(base):
            self.ABORT("Config folder {} is not a directory!"
                       .format(base))

        cfg_file = base + '/cfg'
        data = json4store(self.cfg)
        try:
            key = crypto.Key(self.cfg_secret())
            with open(cfg_file, 'wb') as out_file:
                os.chmod(cfg_file, 0o0600)
                # future safe: so we can handle different versions and keys
                out_file.write("0000".encode())
                cipher = crypto.Cipher(key)
                out_file.write(cipher.key_encrypt(data).encode())

        except IOError as err:
            self.NOTIFY("Unable to save config!")
            self.DEBUG("Reason: " + str(err))

    ############################################################
    # security through obscurity
    def cfg_secret(self):
        """Pull the secret key.  Not great security, but better than non-encrypted"""
        site_key_file = os.environ.get('REFLEX_SITE_KEY')
        if site_key_file and os.path.exists(site_key_file):
            with open(site_key_file, 'r') as in_file:
                self.DEBUG("Using site crypto key", module='crypto')
                return base64.b64decode(in_file.read())

        return self.secret_seed

    ############################################################
    def cfg_get(self, name, default=None, required=False):
        """
        Pull a CFG value, but allow the environment to override
        >>> b = Base()
        >>> b.cfg['TST'] = 'sue'
        >>> b.cfg_get('TST')
        'sue'
        >>> os.environ['TST'] = 'bob'
        >>> b.cfg_get('TST')
        'bob'
        >>> b.cfg_get('ZZZZZZ', default='no')
        'no'
        >>> b.cfg_get('ZZZZZZ', required=True)
        Traceback (most recent call last):
        ...
        ValueError: ZZZZZZ not defined (in environ or reflex settings)
        """
        if name in os.environ.keys():
            return os.environ[name]
        elif name in self.cfg.keys():
            return self.cfg[name]
        elif default:
            return default
        elif required:
            raise ValueError(name + " not defined (in environ or reflex settings)")
        else:
            return ''

    ############################################################
    def _env2cfg(self, key):
        """Replace config key from environment or docker secrets, if it exists"""
        secret_path = "/run/secrets/" + key
        if os.path.exists(secret_path):
            with open(secret_path) as infile:
                self.cfg[key] = infile.read().strip()
        elif key in os.environ.keys():
            self.cfg[key] = os.environ[key]

    ############################################################
    # pylint: disable=invalid-name,too-many-branches
    def NOTIFY(self, *msg, **kwargs):
        """
        Internal print wrapper, so it can be easily overloaded--Notify is for human readable

        >>> b = Base()
        >>> b.notifyfd = sys.stdout
        >>> b.NOTIFY("Hello")
        Hello
        """
        if len(msg) > 1:
            msg = " ".join(msg)
        elif msg:
            msg = msg[0]
        else:
            msg = ''

        if self.logfmt == 'json':
            merged = self.union_dict({
                'timestamp':int(time.time()*1000),
                'service':self.__class__.__name__,
            }, kwargs)
            if 'msg' in merged and not merged['msg']:
                del merged['msg']
            if msg:
                if not merged.get('msg'):
                    merged['msg'] = msg.join(" ")
                else:
                    merged['_msg'] = msg.join(" ")
            json.dump(merged, self.notifyfd, escape_forward_slashes=False)
            return

        msg_start = ''
        msg_end = '\n'
        if 'color' in kwargs:
            msg_start = self.ansi_code('fg' + kwargs['color'])
            msg_end = self.ansi_code('reset') + '\n'
            del kwargs['color']

        if self.timestamp:
            msg_start += self.TIMESTAMP()

        if kwargs:
            for kwarg in kwargs:
                val = str(kwargs[kwarg])
                if " " in val:
        # see engine/logging.txt message
        #            val = json4store(kwargs[kwarg])
                    val = '"' + re.sub(r'\n', '\n\t', val.replace('"', '\\"')) + '"'
                msg += " " + kwarg + "=" + val

        self.notifyfd.write(msg_start + msg + msg_end)

    ############################################################
    def TIMESTAMP(self): # pylint: disable=invalid-name
        """return a timestamp"""
        return time.strftime("%Y-%m-%d %H:%M:%S ")


    ############################################################
    def OUTPUT(self, msg): # pylint: disable=invalid-name
        """
        Internal print wrapper, so it can be easily overloaded--OUTPUT is for machine readable

        >>> b = Base()
        >>> b.outputfd = sys.stdout
        >>> b.OUTPUT("Hello")
        Hello
        """
        if isinstance(msg, bytes): # grr python 2/3
            msg = msg.decode()
        self.outputfd.write(msg + "\n")

    ############################################################
#    def _json_logmsg(self, **args):
#        merged = self.union_dict({
#            'timestamp':int(time.time()*1000),
#            'service':self.__class__.__name__,
#        }, args)
#        if not len(merged['message']):
#            del merged['message']
#        return json4store(merged)

    ############################################################
    ######## Full request debugging, put this in the appropriate places:
    #        import logging
    #        import httplib
    #        httplib.HTTPConnection.debuglevel = 1
    #        # you need to initialize logging, otherwise you will
    #        # not see anything from requests
    #        logging.basicConfig()
    #        logging.getLogger().setLevel(logging.DEBUG)
    #        requests_log = logging.getLogger("requests")
    #        requests_log.setLevel(logging.DEBUG)
    #        requests_log.propagate = True
    ########

    def do_DEBUG(self, module=""): # pylint: disable=invalid-name
        """Am I in a debug mode? """
        if not module:
            module = str(self.__class__.__name__).lower()
        debug = self.debug or {'*':False}
        if debug.get('*', None) or module in debug.keys():
            return True
        return False

    # pylint: disable=invalid-name,too-many-arguments
    def DEBUG(self, msg, module="", err=None, color='gray', **kwargs):
        """Send debug output, if we are in debug mode"""
        if not module:
            module = str(self.__class__.__name__)
        if self.do_DEBUG(module=module):
            if err:
                msg = msg + ": " + str(err)
            self.NOTIFY("DEBUG " + module + ": " + msg,
                        level="DEBUG", err=err, color=color, **kwargs)

    ############################################################
    def ABORT(self, msg, err=None): # pylint: disable=invalid-name
        """Abort and fail nicely"""
        if err:
            self.DEBUG("Error", err=err, color='red')
        self.NOTIFY("ABORT: " + msg, color='red')
        if err and err.strerror:
            self.NOTIFY(str(err), color='red')

        sys.exit(1)

    ############################################################
    def LOG(self, **args): # pylint: disable=invalid-name
        """
        >>> b = Base()
        >>> b.logfd = sys.stdout
        >>> b.logfmt = 'txt'
        >>> args = {'x':"this is a test", 'y':'this', 'z':{"hello":1}}
        >>> b.LOG(**args) # doctest: +ELLIPSIS
        ts=... x="this is a test" y=this z={"hello":1}
        >>> b.logfmt = 'json'
        >>> b.LOG(**args) # doctest: +ELLIPSIS
        {".ts":...,"x":"this is a test","y":"this","z":{"hello":1}}
        """
        if self.logfmt == 'txt':
            buf = "ts=" + str(int(time.time()*1000))
            for key in sorted(args):
                value = args[key]
                if isinstance(value, str):
                    if ' ' in value:
                        if '"' in value:
                            value = value.replace('"', r'\"')
                        buf += " " + str(key) + '="' + value + '"'
                    else:
                        buf += " " + str(key) + "=" + value
                else:
                    # note: this will not parse well in splunk
                    buf += " " + str(key) + '=' + json4store(value)
        else:
            args['.ts'] = int(time.time()*1000)
            buf = json4store(args, sort_keys=True)
        self.logfd.write(buf + "\n")

    ############################################################
    def LOG2FILE(self, fname, mode, data): # pylint: disable=invalid-name
        """Mode should be one of: w, w+"""
        tmpdir = self.cfg_get('TMPDIR', default='/tmp')
        path_fname = tmpdir + "/" + fname
        with open(path_fname, mode) as outf:
            outf.write(json4human(data))
        if os.path.exists(path_fname):
            os.chmod(path_fname, S_IRWXU)

    ############################################################
    def union_dict(self, dict1, dict2):
        """
        Helper function - deep merge of two dictionaries

        >>> b = Base()
        >>> d = b.union_dict({"a":{"b":{"c":1}}},
        ...                  {"a":{"b":{"d":2}},"e":3})
        >>> import json
        >>> json.dumps(d, sort_keys=True)
        '{"a": {"b": {"c": 1, "d": 2}}, "e": 3}'
        """
        for key in dict1: # , value in dict1.iteritems():
            #key = str(key) # grr unicode
            value = dict1[key]
            if key not in dict2:
                dict2[key] = value
            elif isinstance(value, dict):
                self.union_dict(value, dict2[key])
        return dict2

    ############################################################
    def union_set(self, set1, set2): # pylint: disable=no-self-use
        """
        Helper function - union of two sets

        Defined as a method only so it can be called programatically

        """
        return set(set2).union(set(set1))

    ############################################################################
    def sed_env_dict(self, dictionary):
        """
        reference dictionary for sed_env
        """
        for key in dictionary:
            dictionary[key] = self.sed_env(dictionary[key], dictionary, key)
        return dictionary

    ############################################################################
    # pylint: disable=dangerous-default-value
    def sed_env(self, value, dictionary, source_value, env={}):
        """
        search and replace ${VARIABLE} in stream, using os.environ for vars,
        or keyword argument 'env' if it is defined
        """
        if not env:
            env = os.environ

        def env_match(match):
            """sub function used in matching"""
            match_key = match.group(1)
            if match_key in env:
                return env[match_key]
            elif match_key != source_value and match_key in dictionary:
                return dictionary[match_key]
            return None
        return re.sub(r"\$\{([a-zA-Z0-9_-]+)\}", env_match, value)

    ###########################################################################
    def TRAP(self, func, *args, **kwargs):
        """
        Call a function and ignore any thrown errors.  See errors with --debug
        """
        try:
            self.DEBUG("(| {}({}, {}) |)".format(func, args, kwargs))
            return func(*args, **kwargs)
        except: # pylint: disable=bare-except
            self.DEBUG(traceback.format_exc())
            return False

###############################################################################
# Common Errors we might throw
# pylint: disable=missing-docstring
class NotFoundError(Exception):
    pass

# pylint: disable=missing-docstring
class CannotContinueError(Exception):
    pass

def unbuffer(filed):
    fcl = fcntl.fcntl(filed.fileno(), fcntl.F_GETFL)
    fcl |= os.O_SYNC
    fcntl.fcntl(filed.fileno(), fcntl.F_SETFL, fcl)

################################################################################
def set_interval(milliseconds, func, *args, **kwargs):
    """
    Call function every interval.  Starts the timer at call time.
    Although this could also be a decorator, that would not initiate the time at
    the same time, so would require additional work.

    Arguments following function will be sent to function.  Note that these args
    are part of the defining state, and unless it is an object will reset each
    interval.

    The inine test will print "TickTock x.." every second, where x increments.

    >>> import time
    >>> class Tock(object):
    ...     count = 0
    ...     stop = None
    >>> def tick(obj):
    ...     obj.count += 1
    ...     if obj.stop and obj.count == 4:
    ...         obj.stop.set() # shut itself off
    ...         return
    ...     print("TickTock {}..".format(obj.count))
    >>> tock = Tock()
    >>> tock.stop = set_interval(1000, tick, tock)
    >>> time.sleep(6)
    TickTock 1..
    TickTock 2..
    TickTock 3..
    """

    stopper = threading.Event()
    def interval(seconds, func, *args, **kwargs):
        def wrapper():
            if stopper.isSet():
                return
            interval(seconds, func, *args, **kwargs)
            try:
                func(*args, **kwargs)
            except: # pylint: disable=bare-except
                traceback.print_exc()

        thread = threading.Timer(seconds, wrapper)
        thread.daemon = True
        thread.start()
    interval(milliseconds/1000, func, *args, **kwargs)
    return stopper
