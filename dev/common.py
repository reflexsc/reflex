# vim:set expandtab ts=4 sw=4 ai ft=python:
# vim modeline (put ":set modeline" into your ~/.vimrc)
# This is work extracted, drived or from Reflex, and is licensed using the GNU AFFERO License, Copyright Brandon Gillespie

import os
import sys
import socket
import config
import time
import json
import fcntl # get_my_ips
import struct # get_my_ips
#import dictlib
import subprocess

log_hdr = 0
log_cmd = 1
log_msg = 2
log_dbg = 3
log_err = 4
colors = {
    "fgblue":"\033[34m",
    "fgred":"\033[31m",
    "fggrn":"\033[32m",
    "fggray":"\033[37m",
    "reset":"\033[0m",
}

class Core(object):
    outfd = sys.stdout
    _cmd = sys.argv.pop(0)
    _syntax = None
    _debug = False

    def __init__(self, syntax=None, debug=False):
        self._debug = debug

        if syntax:
            self._syntax = syntax

    ############################################################
    def die(self, msg, *args, **kwargs):
        kwargs['level'] = log_err
        self.logf(msg, *args, **kwargs)
        self.outfd.write("\n\n")
        sys.exit(1)

    ############################################################
    def debug(self, msg, *args, **kwargs):
        if self._debug:
            self.log(msg.format(*args, **kwargs), level=log_dbg)

    def logf(self, msg, *args, level=log_hdr, **kwargs):
        self.log(msg.format(*args, **kwargs), level=level)

    def log(self, msg, level=log_hdr, linebreak=True):
        coloron = ''
        if level == log_hdr:
            fmt = "=====[ {time} {host}({ip}) ]===== {msg}"
            if config.COLOR:
                coloron = colors['fgblue']
        elif level == log_err:
            fmt = "!!!!![ {time} {host}({ip}) ]!!!!! {msg}"
            if config.COLOR:
                coloron = colors['fgred']
        elif level == log_msg:
            fmt = "{msg}"
        elif level == log_cmd:
            if msg[0] == ' ':
                fmt = ">>>{msg}"
            else:
                fmt = ">>> {msg}"
            if config.COLOR:
                coloron = colors['fggrn']
        elif level == log_dbg:
            fmt = "<<<<<< {time} {host}({ip}) >>>>>> DEBUG"
            if config.COLOR:
                fmt += colors['reset']
                coloron = colors['fggray']
            fmt += "\n\n    {msg}"
        else:
            fmt = "UNKNOWN LOG LEVEL " + str(log_msg) + ": {msg}"

        coloroff = ''
        if coloron:
            coloroff = colors['reset']
        if linebreak:
            self.outfd.write("\n")
        self.outfd.write(coloron +
                         fmt.format(msg=msg,
                                    host=MY_HOSTNAME,
                                    ip=MY_IPADDR,
                                    time=time.strftime("%FT%T")) +
                         coloroff)
        self.outfd.flush()

    ############################################################
    def sys(self, cmd, abort=False):
        self.outfd.flush()
        if isinstance(cmd, list):
            shell = False
        else:
            shell = True
        sub = subprocess.call(cmd, shell=shell)
        self.outfd.flush()
        if sub:
            if abort:
                sys.exit(sub)
            return False
        return True

    ############################################################
    def sys_out(self, cmd, abort=False):
        self.outfd.flush()
        if isinstance(cmd, list):
            shell = False
        else:
            shell = True
        sub = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=shell)
        output, err = sub.communicate()
        self.outfd.flush()
        output = output.decode() # grr bytes object
        if sub.returncode > 0:
            if abort:
                sys.exit(output)
            return (False, output)
        return (True, output)

    ############################################################
    def do(self, cmd, abort=True):
        buf = ""
        if isinstance(cmd, str):
            shell = True
            buf += cmd
        else:
            shell = False
            arg0 = True
            for arg in cmd:
                if arg0:
                    arg = os.path.basename(arg)
                    arg0 = False
                if " " in arg:
                    arg = json.dumps(arg) # easy way to get quoting right
                buf += " " + arg
        buf += "\n"
        self.log(buf, level=log_cmd)
        return self.sys(cmd, abort=abort)

    ############################################################################
    def syntax(self, msg):
        if self._syntax:
            self._syntax(self, msg)
        print("Error: " + str(msg))

    ############################################################################
    def call_abort(self, func, *args):
        try:
            if self._debug:
                self.debug(".call_abort({}, *{})", func, args)
            return func(*args)
        except Exception as err:
            self.log(str(err), level=log_err)
            sys.exit(1)

    ############################################################################
    def ecr_login(self, profile):
        age = 365 * 24 # random default age, 1 year old
        ecr_cfg = config.ECR.get(profile)
        if not ecr_cfg:
            raise ValueError("profile {} not found".format(profile))

        # are we doing last-login tracking?
        if not ecr_cfg.get('last'):
            return

        ecr_last = os.path.expanduser(ecr_cfg['last'])

        if os.path.exists(ecr_last):
            # age in hours since now
            age = (time.time() - os.path.getmtime(ecr_last)) / 60 / 60

        if age > config.ECR_LOGIN_MAX_AGE:
            self.log("Refreshing AWS ECR Login\n", level=log_hdr)
            cmd = ["aws", "ecr", "get-login",
                   "--region", ecr_cfg['aws-region'],
                   "--profile", ecr_cfg['aws-user']]
            self.log(" ".join(cmd) + "\n\n", level=log_cmd)
            status, output = self.sys_out(cmd)
            if status:
                # cleanup deprecated msg
                output = output.replace("-e none ", "")
                if self.sys(output):
                    with open(ecr_last, "w") as out:
                        out.write("\n")

MY_IPADDR = socket.gethostbyname(socket.gethostname())
MY_HOSTNAME = socket.gethostname()
