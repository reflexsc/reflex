#!/usr/bin/env python3
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

# common includes
import sys
import os
import argparse
import doctest
import subprocess
from subprocess import *
import hashlib
import shutil
import json
import re
import base64
import nacl.utils
import requests
import onetimejwt
import jwt
import time
import dictlib
try:
    import StringIO as strio
except:
    import io as strio
import rfxengine.db.mxsql
from rfxengine.db.objects import Pipeline, Service, Config, Instance, Policy, Policyscope, Apikey, Build, Group, ObjectExists
import rfx
import rfxengine.abac as abac
from rfxengine import memstate, trace
from rfx.test import *
from rfx import json4human, json4store, json2data
from rfx import client

################################################################################
# Tests
def dict_compare(d1, d2):
    d1_keys = set(d1.keys())
    d2_keys = set(d2.keys())
    intersect_keys = d1_keys.intersection(d2_keys)
    added = d1_keys - d2_keys
    removed = d2_keys - d1_keys
    modified = {o : (d1[o], d2[o]) for o in intersect_keys if d1[o] != d2[o]}
    same = set(o for o in intersect_keys if d1[o] == d2[o])
    return added, removed, modified, same
    
################################################################################
class Tester(rfx.Base):
    dbm = None
    tap = None
    baseurl = ''
    auth = None
    attrs = None
    count = 0
    results = dict()

    def __init__(self, *args, **kwargs):
        if kwargs.get('baseurl'):
            self.baseurl = kwargs['baseurl']
            del kwargs['baseurl']
        super(Tester, self).__init__(*args, **kwargs)
        self.attrs = abac.MASTER_ATTRS
        self.tap = TAP()
        #self.dbm = rfxengine.db.mxsql.Master(config={
        #    'database': 'rfxtst',
        #    'user': 'root'
        #}, base=kwargs['base'])

        # configure the cache
#        self.dbm.cache = rfxengine.memstate.Cache()
#        self.dbm.cache.start_housekeeper(2) # extreme
#        self.dbm.cache.configure('policy', 1) # extreme
#        self.dbm.cache.configure('policymap', 1) # extreme
#        self.dbm.cache.configure('policyscope', 1) # extreme
#
        self.notifyfd = strio.StringIO()
        self.outputfd = strio.StringIO()
        self.auth = dictlib.Obj(apikey={'name':'','secret':''})

    ############################################################################
    def get_master_key(self):
        if not os.path.exists("engine.log"):
            self.ABORT("Cannot find engine.log")

        def _get_master_key():
            key_rx = re.compile(r'Initializing schema master apikey,.*REFLEX_APIKEY=(.*)$')
            with open("engine.log") as logf:
                for line in logf:
                    match = key_rx.search(line)
                    if match:
                        return match.group(1)

        master_key = _get_master_key()
        if not master_key:
            self.ABORT("Cannot find master key in engine.log!")

        key = master_key.strip().split(".")

        self.auth['apikey'] = dictlib.Obj(name=key[0],
                                          secret=base64.b64decode(key[1]),
                                          key=master_key)
        print("Using master key {}.{}".format(key[0], key[1]))

    ############################################################################
    def outhdr(self, words):
        self.OUTPUT("---> {}\n".format(words))

    ############################################################################
    def output(self, words):
        if isinstance(words, str):
            self.OUTPUT(words)
            self.OUTPUT("\n")
        else:
            self.OUTPUT(json4human(words))

    ############################################################################
    def make(self, obj_class, data):
        errs = []
    
        otype = obj_class(master=self.dbm)
        if not self.tap.TRAP(otype.load, data):
            return False
        try:
            otype.create(self.attrs)
        except:
            self.output(traceback.format_exc())
        return True
    
    ############################################################################
    def get(self, obj_class, target):
        errs = []
        otype = obj_class(master=self.dbm)
        try:
            self.DEBUG("Getting from DB")
            return otype.get(target, self.attrs)
        except:
            self.output(traceback.format_exc())
    
    ############################################################################
    def _resubmit(self, obj_class, name):
        obj = self.get(obj_class, name)
        if not obj:
            self.output("Unable to load object")
            return False

        obj.update()

        self.output(json4human(obj.obj))

    ############################################################################
    def resubmit(self, label, args, *expect, negate=False):
        self.tap.ok_func_compare(label, self, self._resubmit, args, {},
                                 *expect, negate=negate)

    ############################################################################
    def _addcheck(self, obj_class, name, data):
        data['name'] = name # just to be certain
        if not self.make(obj_class, data.copy()):
            return False
        obj = self.get(obj_class, name)
        if not obj:
            self.output("No second object to compare to")
            return False
        data2 = obj.dump()
        added, removed, modified, same = dict_compare(data2, data)

        warning = False
        if sorted(list(added)) != ['id', 'updated_at', 'updated_by']:
            self.output("Unexpected keys: " + str(sorted(list(added))))
            warning = True
        if removed:
            self.output("Missing Keys: " + str(sorted(list(removed))))
            warning = True
        if modified:
            self.output("Altered data: " + str(sorted(list(modified))))
            warning = True

        self.output(json4human(data2))
        return not warning

    ############################################################################
    def addcheck(self, label, args, *expect):
        self.tap.ok_func_compare(label, self, self._addcheck, args, {}, *expect)

    ############################################################################
    def reset(self):
        self.notifyfd.truncate(0)
        self.notifyfd.seek(0)
        self.outputfd.truncate(0)
        self.outputfd.seek(0)

    ############################################################################
    def rest_useauth(self, reqfunc, *args, **kwargs):
        """wrapper for rest tests"""
        if not kwargs:
            kwargs = {}
        njwt = jwt.encode({
            'jti': self.auth.token.jti,
            'exp': int(time.time() + 60)
        }, self.auth.token.secret)
        if kwargs.get('headers'):
            kwargs['headers']['X-ApiToken'] = njwt
        else:
            kwargs['headers'] = {
                'X-ApiToken': njwt
            }
        kwargs['cookies'] = {
            'sid': self.auth.token.name
        }
        return self.fcall(reqfunc, *args, **kwargs)

    ############################################################################
    def rest_newauth(self, reqfunc, *args, **kwargs):
        """wrapper for rest tests"""
        if not kwargs:
            kwargs = {}

        auth_jwt = jwt.encode(dict(
            jti=self.auth.apikey.name,
            seed=base64.b64encode(nacl.utils.random(256)).decode(),
            exp=time.time() + 300
        ), self.auth.apikey.secret)

        if kwargs.get('headers'):
            kwargs['headers']['X-Apikey'] = auth_jwt
        else:
            kwargs['headers'] = {
                'X-Apikey': auth_jwt
            }
        result = self.fcall(reqfunc, *args, **kwargs)
        if result.headers.get('Content-Type') != 'application/json':
            self.NOTIFY(str(result.headers.get("Content-Type")))
            self.NOTIFY(str(result.content))
            return False
        data = json2data(result.content)
        if data.get("status", '') != "success":
            self.NOTIFY("Bad data")
            self.NOTIFY(json.dumps(data, indent=2))
            return False
        self.auth['token'] = dictlib.Obj(
            jti=data['jti'],
            name=result.cookies['sid'],
            secret=base64.b64decode(data['secret'])
        )

    ############################################################################
    def rcs(self, reqfunc, *args, **kwargs):
        self.reset()
        try:
            self.results[self.count] = reqfunc(*args, **kwargs)
            self.NOTIFY(str(self.results[self.count]))
            self.count += 1
        except:
            self.NOTIFY(traceback.format_exc())

    ############################################################################
    def fcall(self, reqfunc, *args, **kwargs):
        self.reset()
        try:
            r = reqfunc(*args, **kwargs)
            self.NOTIFY(r.content.decode())
            if r.status_code == 200:
                self.NOTIFY(str(r))
            else:
                self.NOTIFY(str(r))
                self.NOTIFY(json.dumps(dict(r.headers), indent=2))
            return r
        except:
            self.NOTIFY(traceback.format_exc())

    ############################################################################
    def okcmp(self, *args, **kwargs):
        return self.tap.ok_func_compare(*args, **kwargs)

################################################################################
def test_integration(schema, base, tester, args):
#    schema.initialize(verbose=False, reset=True)
    tname = 'test'
    tester.addcheck("Object Verify: Pipeline", 
            (Pipeline, tname, {
                'title': 'Code Test',
                'contacts': {},
                'launch': { "type": "action" },
            }),
            r'')
    tester.addcheck("Object Verify: Service", 
            (Service, tname + '-tst', {
                'pipeline': tname,
                'config': tname + '-tst',
                'region': 'saas1',
                'stage': 'PRD',
                'tenant': 'multitenant',
                'active-instances': [],
                'dynamic-instances': [tname],
                'static-instances': []
            }),
            r"""
            Altered data: \['config', 'dynamic-instances', 'pipeline'\]
            """)
    tester.addcheck("Object Verify: Config1", 
            (Config, tname + '-import', {
                'imports': [tname + '-import'],
                'type': 'parameter',
                'sensitive': { 'parameters' : { 'hello': 'there' }}
            }),
            r"""
            Altered data: \['imports'
            """)
    tester.addcheck("Object Verify: Config2", 
            (Config, tname + '-export', {
                # content
                'type': 'file',
            }),
            r'')
    tester.addcheck("Object Verify: Config3", 
            (Config, tname + '-tst', {
                'type': 'parameter',
                'extends': [tname],
                'exports': [tname + '-export'],
            }),
            r"""
            Altered data: \['exports', 'extends'\]
            """)
    tester.addcheck("Object Verify: Instance", 
            (Instance, tname, {
                'service': tname + '-tst',
                'status': 'ok',
                'address': {"host": "127.0.0.1"}
            }),
            r"""
            Altered data: \['service'\]
            """)
    tester.addcheck("Object Verify: Apikey", 
            (Apikey, tname + '-token', {
                'apikey': 'bob',
            }),
            r"""
            test-token
            """)
    tester.addcheck("Object Verify: Build", 
            (Build, tname + '-build', {
            }),
            r"""
            test-build
            """)
    tester.addcheck("Object Verify: Policy", 
            (Policy, tname + '-policy', {
                'policy': 'True'
            }),
            r"""
            "name":"test-policy"
            """)
    tester.addcheck("Object Verify: Policyscope", 
            (Policyscope, tname + '-policy', {
                'policy': 'test-policy',
                'matches': 'True',
                'objects': [],
                'type': 'targeted',
                'actions': 'admin'
            }),
            r"""
            "name":"test-policy"
            """)
    tester.addcheck("Object Verify: Group w/Apikey", 
            (Group, tname + "-apikey", {
                'type': "Apikey",
                'group': [ 'master', 'notest' ]
            }),
            r"""
            Altered data: \['group'\]
            """,
            r"notest\.notfound",
            r"master\.100"
            )
    tester.addcheck("Object Verify: Group w/Pipeline", 
            (Group, tname + "-pipeline", {
                'type': "Pipeline",
                'group': [ tname ]
            }),
            r"""
            Altered data: \['group'\]
            """,
            r"test\.1"
            )
    tester.addcheck("Object Verify: Group w/set", 
            (Group, tname + "-set", {
                'type': "set",
                'group': [ "a", "b", "c", "c" ]
            }),
            r"""
            Altered data: \['group'\]
            """,
            r'"a"',
            )
    tester.addcheck("Object Verify: Group w/passwords", 
            (Group, tname + "-password", {
                'type': "password",
                'group': [ "bob:this pass", "sue:that pass" ]
            }),
            r"""
            Altered data: \['group'\]
            """,
            r'"\$7\$'
            )

###############################################################################
def test_functional(schema, base, tester, baseurl, args):
#    schema.initialize(verbose=False, reset=True)
    tester.okcmp("REST Health Check", tester, tester.fcall,
                 [requests.get, baseurl + "/health"],
                 {}, # "X-Request-Id": "test-request-id1"},
                 r'Response \[204\]')

    tester.okcmp("Unauthorized", tester, tester.fcall,
                 [requests.get, baseurl + "/config"],
                 {}, # "X-Request-Id": "test-request-id2"},
                 r'Response \[401\]')

    user_attrs = abac.attrs_skeleton(token_nbr=100, token_name='master')

    tester.get_master_key()

    if not tester.okcmp("Authorized", tester, tester.rest_newauth,
                        [requests.get, baseurl + "/token"], {},
                        r'Response \[200\]'):
        print("Cannot continue")
        sys.exit(1)

    samples = dictlib.Obj({
        'pipeline': {
            'mcreate': {
                'name': 'gallifrey',
                'title': 'Prydonian Academy'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'gallifrey'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Pipeline named.*already exists'],
            'mupdate': {
                'name': 'gallifrey',
                'title': 'College of Cardinals',
                'id': 1
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'title": "College of Cardinals'],
            'mmerge': {
                'name': 'gallifrey',
                'launch': {'this': 'now'}
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'launch": {"this']
        },
        'config': {
            'mcreate': {
                'type': 'parameter',
                'name': 'tardis'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'tardis'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'type': 'parameter',
                'name': 'tardis',
                'id': 1,
                'exports': ["sonic"],
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'exports": \["sonic.notfound'],
            'mmerge': {
                'name': 'tardis',
                'exports': ["screwdriver"],
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'exports": \["screwdriver.notfound']
        },
        'service': {
            'mcreate': {
                'config': 'tardis',
                'pipeline': 'gallifrey',
                'stage': 'stage',
                'region': 'arcadia',
                'name': 'gallifrey-prd'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'region': 'arcadia',
                'name': 'gallifrey-prd'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'config': 'tardis',
                'pipeline': 'gallifrey',
                'stage': 'prd',
                'region': 'arcadia',
                'name': 'gallifrey-prd',
                'id': 1
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'stage": "prd'],
            'mmerge': {
                'name': 'gallifrey-prd',
                'pipeline': "screwdriver",
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'pipeline": "screwdriver']
        },
        'instance': {
            'mcreate': {
                'name': 'gallifrey-1',
                'service': 'gallifrey-prd',
                'status': 'ok',
                'address': {}
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'gallifrey-1'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'name': 'gallifrey-1',
                'id': 1,
                'service': 'gallifrey-prd',
                'status': 'failed',
                'address': {}
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'status": "failed'],
            'mmerge': {
                'name': 'gallifrey-1',
                'address': {'host': 'there'}
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'address": {"host": "there']
        },
        'build': {
            'mcreate': {
                'name': 'gallifrey-1600',
                'application': 'gallifrey',
                'version': '1600',
                'state': 'ready'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'name': 'gallifrey-1600',
                'id': 1,
                'application': 'gallifrey',
                'version': '1600',
                'state': 'failed'
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'state": "failed'],
            'mmerge': {
                'name': 'gallifrey-1',
                'status': {'starting': 'up'}
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'status": {"starting": "up']
        },
        'state': {
            'mcreate': {
                'name': 'versions',
                'application': 'gallifrey',
                'version': '1600',
                'state': 'ready'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'name': 'versions',
                'id': 1,
                'application': 'gallifrey',
                'version': '1600',
                'state': 'failed'
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'state": "failed'],
            'mmerge': {
                'name': 'versions',
                'status': {'starting': 'up'}
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'status": {"starting": "up']
        },
        'apikey': {
            'mcreate': {
                'name': 'the-doctor',
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'id': 200,
                'name': 'boo'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "id must be left undef'],
            'mupdate': {
                'name': 'the-doctor',
                'id': 101, # hate using fixed #'s
                'description': 'bow ties are cool'
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'description": "bow ties are cool'],
            'mmerge': {
                'name': 'the-doctor',
                'description': 'who'
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'description": "who']
        },
        'group': {
            'mcreate': {
                'name': 'timelords',
                'type': 'Apikey',
                'group': ['the-doctor']
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'timelords2',
                'id': 1,
                'group': ['the-master']
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Object validate'],
            'mupdate': {
                'name': 'timelords',
                'type': 'Apikey',
                'id': 1,
                'group': ['the-master']
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'group": \["the-master'],
            'mmerge': {
                'name': 'timelords',
                'group': ['the-doctor', 'the-master']
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'group": \["the', r'the-master.notfound', r'the-doctor.10']
        },
        'policy': {
            'mcreate': {
                'name': 'timelords',
                'policy': 'True'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'tardises',
                'policy': '! a tardis'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Cannot prepare policy'],
            'mupdate': {
                'id': 101, # garr static values lame
                'policy': 'False'
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'policy": "False'],
            'mmerge': {
                'name': 'timelords',
                'description': 'do not trust the master'
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'description": "do not trust']
        },
        'policyscope': {
            'mcreate': {
                'name': 'timelords',
                'policy': 'timelords',
                'actions': 'admin',
                'type': 'targeted',
                'objects': [],
                'matches': 'True'
            },
            'mcreate-expect': [r'Response \[201\]'],
            '-mcreate': {
                'name': 'tardises',
                'policy': 'timelords',
                'actions': 'bob',
                'objects': [],
                'type': 'targeted',
                'matches': '! good'
            },
            '-mcreate-expect': [r'Response \[400\]', r'message": "Cannot prepare match'],
            'mupdate': {
                'name': 'timelords',
                'objects': [],
                'policy': 'timelords',
                'type': 'targeted',
                'matches': 'False',
                'actions': 'read, write, ADMIN'
            },
            'mupdate-expect': [r'status": "updated'],
            'mupdate-validate': [r'matches": "False', r'actions": "admin"'],
            'mmerge': {
                'name': 'timelords',
                'description': 'do not trust the master'
            },
            'mmerge-expect': [r'status": "updated'],
            'mmerge-validate': [r'description": "do not trust']
        }
    })

    # order matters, otherwise I would use sample.keys()
    for obj in ('pipeline', 'config', 'service', 'instance', 'build', 'apikey', 'group', 'policy', 'policyscope', 'state'):
        odata = samples[obj].mcreate
        tester.okcmp("REST create " + obj, tester, tester.rest_useauth,
                     [requests.post, baseurl + "/" + obj],
                     {"data": json4store(samples[obj].mcreate),
                      'headers':{'Content-Type':'application/json',
                                 'X-Request-Id': 'test-request-id1',
                      }},
                     *samples[obj].mcreate_expect)
        tester.okcmp("REST create " + obj + " (w/failure)", tester, tester.rest_useauth,
                     [requests.post, baseurl + "/" + obj],
                     {"data": json4store(samples[obj]._mcreate),
                      'headers':{'Content-Type':'application/json'}},
                     *samples[obj]._mcreate_expect)
        name = odata.get('name', odata.get('id', "bork"))
        tester.okcmp("REST get " + obj, tester, tester.rest_useauth,
                     [requests.get, baseurl + "/" + obj + "/" + name], {},
                     r'name": "' + name)
        tester.okcmp("REST get " + obj + " (w/failure)", tester, tester.rest_useauth,
                     [requests.get, baseurl + "/" + obj + "/notfound"], {},
                     r'status": "failed', r'Response \[404\]', r'Unable to load')
        tester.okcmp("REST update " + obj, tester, tester.rest_useauth,
                     [requests.put, baseurl + "/" + obj + "/" + name],
                     {"data": json4store(samples[obj].mupdate),
                      'headers':{'Content-Type':'application/json'}},
                     *samples[obj].mupdate_expect)
        tester.okcmp("REST get " + obj, tester, tester.rest_useauth,
                     [requests.get, baseurl + "/" + obj + "/" + name], {},
                     *samples[obj].mupdate_validate)
        tester.okcmp("REST update/merge " + obj, tester, tester.rest_useauth,
                     [requests.put, baseurl + "/" + obj + "/" + name + "?merge=True"],
                     {"data": json4store(samples[obj].mmerge),
                      'headers':{'Content-Type':'application/json'}},
                     *samples[obj].mmerge_expect)
        tester.okcmp("REST get " + obj, tester, tester.rest_useauth,
                     [requests.get, baseurl + "/" + obj + "/" + name], {},
                     *samples[obj].mmerge_validate)
        tester.okcmp("REST delete " + obj, tester, tester.rest_useauth,
                     [requests.delete, baseurl + "/" + obj + "/" + name], {},
                     r'status": "deleted')

        # recreate for others to reference
        tester.okcmp("REST create " + obj, tester, tester.rest_useauth,
                     [requests.post, baseurl + "/" + obj],
                     {"data": json4store(samples[obj].mcreate),
                      'headers':{'Content-Type':'application/json'}},
                     *samples[obj].mcreate_expect)

def test_full_stack(schema, base, tester, baseurl, args):
    user_attrs = abac.attrs_skeleton(token_nbr=100, token_name='master')

    tester.get_master_key()

    os.environ['REFLEX_URL'] = tester.baseurl
    os.environ['REFLEX_APIKEY'] = tester.auth.apikey.key
    rcs_master = client.Session().cfg_load()

    # verify instance ping
    tester.okcmp("Reflex Instance Ping", tester, tester.rcs,
                 [rcs_master.instance_ping, "test-instance", {
                     "address": {
                         "ip0": "10.0.0.0"
                     },
                 }], {},
                 r"'status': 'updated'"
                 )
    tester.okcmp("Reflex Instance Ping Verify", tester, tester.rcs,
                 [rcs_master.get, "instance", "test-instance"], {},
                 r"'ping-from-ip': '127.0.0.1'")

    # create a new apikey
    tester.okcmp("Reflex Apikey Create", tester, tester.rcs,
                 [rcs_master.create, "apikey", {
                     "name": "amy-pond"
                 }], {},
                 r"'status': 'created'"
                 )
    tester.okcmp("Reflex Apikey Get", tester, tester.rcs,
                 [rcs_master.get, "apikey", "amy-pond"], {},
                 r"'name': 'amy-pond'")

    # tester object keeps all its results based on order.  If added to above,
    # increment the order
    pond_apikey = tester.results[3]['name'] + "." + tester.results[3]['secrets'][0]
    os.environ['REFLEX_APIKEY'] = pond_apikey
    rcs_pond = client.Session().cfg_load()
    tester.okcmp("Reflex Client Create (limited)", tester, tester.rcs,
                 [rcs_pond.create, "config", {
                     "name":"tardis-main",
                     "type":"parameter",
                 }], {},
                 r"rfx.client.ClientError: Forbidden"
                 )

    tester.okcmp("Reflex Client Create (master)", tester, tester.rcs,
                 [rcs_master.create, "config", {
                     "name":"tardis-main",
                     "type":"parameter",
                     "sensitive":{"config":"real"}
                 }], {},
                 r"'status': 'created'"
                 )

    tester.okcmp("Reflex Client Create (master)", tester, tester.rcs,
                 [rcs_master.create, "config", {
                     "name":"tardis-main-sub",
                     "type":"parameter",
                     "sensitive":{"config":"data"},
                     "extends":["tardis-main"]
                 }], {},
                 r"'status': 'created'"
                 )

    tester.okcmp("Reflex Client Get (limited w/fail)", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"rfx.client.ClientError: Forbidden"
                 )

    tester.okcmp("Reflex Client Get (master)", tester, tester.rcs,
                 [rcs_master.get, "config", "tardis-main"], {},
                 r"'name': 'tardis-main'")

    tester.okcmp("Reflex Client Get List (master)", tester, tester.rcs,
                 [rcs_master.list, "config"], {},
                 r"tardis-main-sub")

    # create a policy allowing amy pond to read any config (sensitive and not)
    tester.okcmp("Reflex Policy Create (pond not sensitive)", tester, tester.rcs,
                 [rcs_master.create, "policy", {
                     "name": "pond-read-configs",
                     "policy": 'token_name=="amy-pond" and sensitive==False'
                 }], {},
                 r"'status': 'created'")
    tester.okcmp("Reflex Policy Create (pond sensitive)", tester, tester.rcs,
                 [rcs_master.create, "policy", {
                     "name": "pond-read-sensitive",
                     "policy": 'token_name=="amy-pond" and sensitive==True'
                 }], {},
                 r"'status': 'created'")

    # map the policy global
    tester.okcmp("Reflex Policyscope Create global", tester, tester.rcs,
                 [rcs_master.create, "policyscope", {
                     "name": "pond-read-configs",
                     "policy": "pond-read-configs",
                     "actions": 'read',
                     'objects': [],
                     "type": 'global',
                     "matches": 'obj_type == "Config"'
                 }], {},
                 r"'status': 'created'")

    tester.okcmp("Reflex Client Get (limited w/success)", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"'name': 'tardis-main'",
                 r"'sensitive': {'encrypted'")

    tester.okcmp("Reflex Client Get List (pond sensitive cols w/o)", tester, tester.rcs,
                 [rcs_pond.list, "config"], {'cols': ["name","sensitive"]},
                 r"tardis-main-sub",
                 r"'sensitive': {'encrypted")

    # map the sensitive policy just to the individual items (it is targeted)
    tester.okcmp("Reflex Policyscope Create sensitive", tester, tester.rcs,
                 [rcs_master.create, "policyscope", {
                     "name": "pond-read-sensitive",
                     "policy": "pond-read-sensitive",
                     "actions": 'read',
                     'objects': [],
                     "type": 'targeted',
                     "matches": 'obj_type == "Config"'# and rx.search("^tardis-", obj["name"])'
                 }], {},
                 r"'status': 'created'")

    tester.okcmp("Reflex Client Get (limited w/success)", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"'name': 'tardis-main'",
                 r"'sensitive': {'config': 'real'}")

    tester.okcmp("Reflex Client Get List (pond)", tester, tester.rcs,
                 [rcs_pond.list, "config"], {},
                 r"tardis-main-sub")

    tester.okcmp("Reflex Client Get List (pond sensitive)", tester, tester.rcs,
                 [rcs_pond.list, "config"], {},
                 r"tardis-main-sub")

    tester.okcmp("Reflex Client Get List (pond sensitive cols)", tester, tester.rcs,
                 [rcs_pond.list, "config"], {'cols': ["name","sensitive"]},
                 r"tardis-main-sub",
                 r"'sensitive': {'config': 'real'}")

    tester.okcmp("Reflex Policy Drop", tester, tester.rcs,
                 [rcs_master.delete, "policy", "pond-read-sensitive"], {},
                 r"'status': 'deleted'")

    tester.okcmp("Reflex Client Get (limited w/fail)", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"""sensitive': {'encrypted"""
                 )

    # insert policy test for fail instead of pass
    tester.okcmp("Reflex Policy Create result=fail", tester, tester.rcs,
                 [rcs_master.create, "policy", {
                     "name": "pond-fail",
                     "policy": 'token_name=="master"',
                     "order": 100,
                     "result": 'fail'
                 }], {},
                 r"'status': 'created'")

    # map the sensitive policy just to the individual items (it is targeted)
    tester.okcmp("Reflex Policyscope Create result=fail", tester, tester.rcs,
                 [rcs_master.create, "policyscope", {
                     "name": "pond-fail",
                     "policy": "pond-fail",
                     "actions": 'read',
                     'objects': [],
                     "type": 'targeted',
                     "matches": 'obj_type == "Config"'
                 }], {},
                 r"'status': 'created'")

    tester.okcmp("Reflex get forbidden w/fail", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"rfx.client.ClientError: Forbidden"
                 )

    tester.okcmp("Reflex get forbidden wo/fail", tester, tester.rcs,
                 [rcs_master.get, "config", "tardis-main"], {},
                 r"name': 'tardis-main"
                 )

    tester.okcmp("Reflex drop fail policy", tester, tester.rcs,
                 [rcs_master.delete, "policy", "pond-fail"], {},
                 r"'status': 'deleted'")

    # insert policy test for groups
    tester.okcmp("Reflex Group Create Password", tester, tester.rcs,
                 [rcs_master.create, "group", {
                     "name": "da-codes",
                     "type": "password",
                     "group": ["pass1:word1", "pass2:pass2"],
                 }], {},
                 r"'status': 'created'")

    # insert policy test for fail instead of pass
    tester.okcmp("Reflex Policy Update (pond sensitive w/pass)", tester, tester.rcs,
                 [rcs_master.patch, "policy", "pond-read-configs", {
                     "policy": 'token_name=="amy-pond" and pwin(http_headers, "da-codes")',
                     "order": 100
                 }], {},
                 r"'status': 'updated'")

    tester.okcmp("Reflex get wo/pass", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"rfx.client.ClientError: Forbidden"
                 )

    rcs_pond.headers["X-Password"] = base64.b64encode("pass2".encode()).decode()
    tester.okcmp("Reflex get w/pass", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"'sensitive': {'config': 'real'}"
                 )

    # insert policy test for fail instead of pass
    tester.okcmp("Reflex Policy Update (pond sensitive w/2pass)", tester, tester.rcs,
                 [rcs_master.patch, "policy", "pond-read-configs", {
                     "policy": 'token_name=="amy-pond" and pwsin(http_headers, "da-codes")',
                     "order": 100
                 }], {},
                 r"'status': 'updated'")

    tester.okcmp("Reflex get w/1pass as fail", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"rfx.client.ClientError: Forbidden"
                 )

    del(rcs_pond.headers["X-Password"])
    rcs_pond.headers["X-Passwords"] = base64.b64encode(json.dumps(["pass2","word1"]).encode()).decode()
    tester.okcmp("Reflex get w/2pass good", tester, tester.rcs,
                 [rcs_pond.get, "config", "tardis-main"], {},
                 r"'sensitive': {'config': 'real'}"
                 )

    # test a list as master, amy pond, and after policy is deleted

################################################################################
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action='append')
    parser.add_argument("--dbinit", action='store_true')
    parser.add_argument("--noclean", "--no-clean", action='store_true')
    parser.add_argument("option", choices=['unit', 'lint', 'integration', 'functional', 'full-stack'])

    args = parser.parse_args()
    base = rfx.Base(debug=args.debug).cfg_load()

    libdir = "../../src"

    baseurl = "http://127.0.0.1:54321/api"
    tester = Tester(base=base, baseurl=baseurl)
    tap = tester.tap

    ###########################################################################
    base.timestamp = False # so our output is not altered by timestamps
    base.term_width = 80
    #schema = rfxengine.db.objects.Schema(master=tester.dbm)
    schema = None

    def get_libfiles():
        flist = []
        for (base, folders, files) in os.walk(libdir + "/rfxengine"):
            for f in files:
                if f[-3:] == ".py":
                    if libdir == base:
                        flist.append(f)
                    else:
                        flist.append(base.replace(libdir + "/", "") + "/" + f)
        return flist

#    if args.dbinit:
#        schema.initialize(verbose=True, reset=True)

    if args.option == 'unit':
        for f in get_libfiles():
            if f != "rfxengine/abac.py": # bug w/global:MASTER_ATTRS breaking doctest
                tap.unit(libdir, f, exit_on_fail=False)
        return
    elif args.option == 'lint':
        tap.lint(libdir, 'rfxengine', exit_on_fail=False)
        return
    elif args.option == 'integration':
        test_integration(schema, base, tester, args)
    elif args.option == 'functional':
        test_functional(schema, base, tester, baseurl, args)
    elif args.option == 'full-stack':
        test_full_stack(schema, base, tester, baseurl, args)

    tester.tap.exit()

    # add test to validate abac roles
################################################################################
if __name__ == "__main__":
    main()

