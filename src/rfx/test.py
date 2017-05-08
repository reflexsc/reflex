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
General Test Tools
"""

#from __future__ import absolute_import, division, print_function

# py2 backwards compatability
#from builtins import bytes
import os
import re
import hashlib
from types import ModuleType
import sys
import time
import subprocess
import traceback
import doctest
import rfx

###############################################################################
def check_file(fname, expected):
    """
    Match checksum on file as expected.
    """
    with open(fname, 'rb') as file_h:
        cksum = md5(file_h.read())
        if cksum != expected:
            return False
    return True

###############################################################################
def md5(data):
    """
    Make a hash
    """
    return hashlib.md5(data).hexdigest()

###############################################################################
def check_data(data, expected):
    """
    Match checksum on file as expected.
    """
    cksum = md5(data)
    if cksum != expected:
        return False
    return True

###############################################################################
class TAP(rfx.Base):
    """
    Simple Test Anything Protocol (TAP) output
    """
    _tap_good = 0
    _tap_bad = 0
    _tap_total = 0
    _tap_logfile = 'test.log'

    ###########################################################################
    # pylint: disable=super-init-not-called
    def __init__(self):
        os.environ['PYTHONPATH'] = ":".join(sys.path)
        self.colorize = True

    ###########################################################################
    # change behavior
    # pylint: disable=super-init-not-called
    def TRAP(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            return True
        except: # pylint: disable=bare-except
            self.log("--- Error Calling:\n    {}({}, {})\n--- "
                     .format(func, args, kwargs))
            self.log(traceback.format_exc())
            return False

    ###########################################################################
    # pylint: disable=invalid-name, no-else-return
    def ok(self, label, value):
        """
        Basic TAPS evaluation
        """
        self._tap_total += 1
        tstamp = "{:.7f} ".format(time.time())
        msg = "ok {} {}".format(self._tap_total, label)
        if value:
            self._tap_good += 1
            self.NOTIFY(tstamp + msg, color='green')
            self.log("==> " + msg + "\n\n")
            return True
        else:
            self._tap_bad += 1
            self.NOTIFY(tstamp + "not " + msg, color='red')
            self.log("==> not " + msg + "\n\n")
            return False

    ###########################################################################
    def ok_run(self, label, expected, exec_args, shell=False):
        """
        Run a command
        Failure if returncode != 0 or expected md5 does not match output
        """
        sub = subprocess.Popen(exec_args,
                               shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        sub.wait()
        output = sub.stdout.read()
        self.log_header(label)
        self.log_output("OUTPUT", output)
        if sub.returncode > 0:
            self._debug_output(output, force=True)
            self.OUTPUT(label + " return code failure ({})".format(sub.returncode))
            return self.ok(label, False)

        return self._ok_output_md5(label, expected, output)

    ###########################################################################
    def ok_run_compare(self, label, exc, *expected, shell=False):
        """
        Run a command
        Failure if returncode != 0 or expected md5 does not match output
        """
        sub = subprocess.Popen(exc,
                               shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        sub.wait()
        output = sub.stdout.read().decode()

        return self._ok_output_compare(label, output, *expected)

    ###########################################################################
    def log_output(self, label, output):
        """
        Log a block of text
        """
        def hdr(label): # pylint: disable=missing-docstring
            length = int((80 - (len(label))) / 2)
            self.log(("<" * length) + label + (">" * length) + "\n")
        self.log("\n")
        hdr(" BEGIN " + label + " ")
        self.log(output)
        hdr(" END " + label + " ")
        self.log("\n")

    ###########################################################################
    def _debug_output(self, output, force=False):
        """
        Print output if we are debugging it
        """
        if force or self.do_DEBUG(module='test'):
            if isinstance(output, bytes): # grr python 2/3
                output = output.decode()
            self.log_output("OUTPUT", output)

    ###########################################################################
    def ok_func(self, label, func, *args, **kwargs):
        """
        TAPS evaluation, calling function
        """
        return self.ok(label, self.TRAP(func, *args, **kwargs))

    ###########################################################################
    def ok_func_io(self, label, expected, base_class, func, *args, **kwargs):
        """
        TAPS evaluation, hashing result of io streams, assuming rfx.base object

        Assert: base_class.outputfd and notifyfd are already set to a StringIO
        """
        self.TRAP(func, *args, **kwargs)
        base_class.outputfd.seek(0)
        base_class.notifyfd.seek(0)
        output = base_class.outputfd.read() + base_class.notifyfd.read()
        return self._ok_output_md5(label, expected, output)

    ###########################################################################
    # pylint: disable=too-many-arguments
    def ok_func_compare(self, label, base_class, func, fargs, fkwargs, *expected, **kwargs):
        """
        TAPS evaluation, hashing result of io streams, assuming rfx.base object

        Assert: base_class.outputfd and notifyfd are already set to a StringIO
        """
        try:
            func(*fargs, **fkwargs)
        except: # pylint: disable=bare-except
            base_class.NOTIFY(traceback.format_exc())
        base_class.outputfd.seek(0)
        base_class.notifyfd.seek(0)
        output = base_class.outputfd.read() + base_class.notifyfd.read()
        base_class.outputfd.seek(0)
        base_class.notifyfd.seek(0)
        base_class.outputfd.truncate(0)
        base_class.notifyfd.truncate(0)
        return self._ok_output_compare(label, output, *expected, **kwargs)

    ###########################################################################
    def _ok_output_compare(self, label, output, *expected, **kwargs):
        """
        Compare object
        """
        def trim(string):
            """sub"""
            string = string.strip()
            string = re.sub(r'^\s+', '', string, flags=re.IGNORECASE|re.MULTILINE)
            string = re.sub(r'\s+$', '', string, flags=re.IGNORECASE|re.MULTILINE)
            return string

        negate = False
        if kwargs.get('negate'):
            negate = True
        self.log_header(label)
        self.log("================ Expect ================\n")
        if negate:
            self.log("<<NOT>> ")
        self.log("\n<<AND>>\n".join(expected) + "\n")
        self.log("=============== Received ===============\n")
        self.log(output)
        self.log("========================================")
        output = trim(output)
        if output[-1:] != "\n":
            self.log("\n")

        def rxsearch(rx):
            """sub"""
            rx = trim(rx)
            if re.search(rx, output):
                return not negate
            return negate

        truth = list(map(rxsearch, expected))
        # pylint: disable=redefined-variable-type
        truth = bool(len(set(truth)) == 1 and truth[0] is True)
        return self.ok(label, truth)

    ###########################################################################
    def _ok_output_md5(self, label, expected, output):
        """
        Check output to match md5, fail or not
        """
        cksum = md5(output)
        self._debug_output(output)
        self.log_header(label)
        self.log_output("OUTPUT", output)

        if cksum != expected:
            self.OUTPUT("Expected: {} != found: {}".format(expected, cksum))
            return self.ok(label, False)
        return self.ok(label, True)

    ###########################################################################
    def ok_func_cksum(self, label, expected, func, *args, **kwargs):
        """
        TAPS evaluation, hashing result of function call
        """
        result = self.TRAP(func, *args, **kwargs)
        self.DEBUG("=> {}".format(result))
        cksum = md5(str([result]))
        if cksum != expected:
            self.DEBUG("Expected: {} != found: {}".format(expected, cksum))
            return self.ok(label, False)
        return self.ok(label, True)

    ###########################################################################
    def ok_data_cksum(self, label, expected, data):
        """
        TAPS evaluation, hashing result of output
        """
        self.DEBUG("=> {}".format(data))
        cksum = md5(str([data]))
        if cksum != expected:
            self.DEBUG("Expected: {} != found: {}".format(expected, cksum))
            return self.ok(label, False)
        return self.ok(label, True)

    ###########################################################################
    def exit(self):
        """
        Exit appropriate for the test run, with status equal to failed tests"
        """
        self.NOTIFY("{0} failed".format(self._tap_bad))
        sys.exit(self._tap_bad)

    ###########################################################################
    def log(self, msg):
        """
        Send to tap logfile
        """
        if isinstance(msg, str):
            msg = msg.encode('utf-8')
        with open(self._tap_logfile, 'ab') as out_fd:
            out_fd.write(msg)
            out_fd.flush()

    ###########################################################################
    def log_header(self, label, filed=None):
        """
        Log, but in header formatting.
        """
        banner = "=" * 80
        banner = banner + "\n=== " + label + "\n" + banner + "\n"
        if filed:
            filed.write(banner)
        else:
            self.log(banner)

    ###########################################################################
    # pylint: disable=missing-docstring
    def inline_unit(self, *module, **kwargs):
        """
        Inline does not redirect stdout
        """
        (failed, total) = doctest.testmod(*module)
        if module:
            module = module[0]
            if isinstance(module, ModuleType):
                label = module.__name__ # pylint: disable=no-member
            else:
                label = module.__class__.__name__
        else:
            label = self.__class__.__name__
        self.ok(label + " unit tests", failed <= 0)
        if kwargs.get('exit_on_fail') and failed:
            self.NOTIFY("see test.log")
            self.exit()
        return failed, total

    ###########################################################################
    # pylint: disable=missing-docstring
    def unit(self, lib_base, modfile, exit_on_fail=False):
        failed = 1
        with open(self._tap_logfile, 'a') as out_fd:
            self.log_header(modfile + " unit test", filed=out_fd)
            sub = subprocess.Popen(['python', '-m', 'doctest',
                                    '-v', lib_base + "/" + modfile],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
            for line in sub.stdout:
                out_fd.write(line.decode())
            for line in sub.stderr:
                out_fd.write(line.decode())
            sub.wait()
            failed = sub.returncode

        self.ok(modfile + " unit tests", failed <= 0)

        if exit_on_fail and failed:
            self.NOTIFY("see test.log")
            self.exit()

        return failed

    ###########################################################################
    # pylint: disable=missing-docstring
    def lint(self, lib_base, modfile, exit_on_fail=False):
        failed = 1
        rating = ''
        with open(self._tap_logfile, 'a') as out_fd:
            sub = subprocess.Popen(['python', '-m', 'pylint',
                                    lib_base + "/" + modfile],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)

            self.log_header(modfile + " lint test", filed=out_fd)

            for line in sub.stdout:
                line = line.decode()
                out_fd.write(line)
                m = re.search("Your code has been rated at ([0-9.]+)/", line)
                if m:
                    rating = m.group(1)
                    if float(rating) == 10:
                        failed = 0
            for line in sub.stderr:
                out_fd.write(line.decode())
            sub.wait()

        if failed:
            with open(self._tap_logfile, 'r') as log:
                rx_ignore = re.compile(r'^(I:|==+)')
                rx_end = re.compile(r'^Report$')
                for line in log:
                    if rx_ignore.search(line):
                        continue
                    if rx_end.search(line):
                        break
                    self.NOTIFY(line.strip())

        self.ok("{} lint test ({}/10)" .format(modfile, rating), failed <= 0)

        if exit_on_fail and failed:
            self.exit()

        return failed

