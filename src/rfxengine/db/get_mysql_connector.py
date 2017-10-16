#!/usr/bin/env python3

"""get mysql connector"""

import os
import sys
import subprocess

def noerr(cmd):
    """noerr"""
    print(">>> " + cmd)
    if subprocess.call(cmd, shell=True):
        sys.exit("Cannot continue")

def main():
    """main"""
    version = "2.1.4"
    download_url = "https://dev.mysql.com/get/Downloads/Connector-Python/"

    print("Building mysql connector " + version)

    owd = os.getcwd()
    tmp = "tmp" + str(os.getpid())
    os.mkdir(tmp)
    os.chdir(tmp)

    latest = "mysql-connector-python-" + version
    download_url += latest + ".tar.gz"

    # could use requests lib instead
    noerr("curl --fail -L -O " + download_url)
    noerr("tar --owner=" + str(os.getuid()) +
          " --group=" + str(os.getgid()) +
          " -xzf " + latest + ".tar.gz")

    os.chdir(latest)

    args = sys.argv
    args.pop(0)
    noerr("python3 setup.py install " + " ".join(args))
    os.chdir(owd)
    noerr("rm -rf " + tmp)

if __name__ == "__main__":
    main()
