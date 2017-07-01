#!/usr/bin/env python3

import sys
import rfx.launch
import rfx.client

argv = list(sys.argv).copy()
argv.pop(0)
count = argv.pop(0)
base = rfx.Base().cfg_load()

rcs = rfx.client.Session(base=base)
for x in range(0, int(count)):
    rcs.get(*argv)
    sys.stderr.write(".")

#for x in range(0, int(count)):
#    rfx.launch.LaunchCli(base=base).env_cli(argv, {}, None)
