#!/usr/bin/env python3

import requests
import os
import subprocess
import sys

def noerr(cmd):
    if subprocess.call(cmd, shell=True):
        sys.exit("Cannot continue")

def main():
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
    noerr("tar -xzf " + latest + ".tar.gz")

    os.chdir(latest)

    noerr("python3 setup.py install")
    os.chdir(owd)
    noerr("rm -rf " + tmp)

if __name__ == "__main__":
    main()

