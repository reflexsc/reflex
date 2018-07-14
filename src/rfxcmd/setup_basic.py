#!/app/local/bin/virtual-python
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

import rfx.client
import dictlib
from .setup import create, do
try:
    from builtins import input # pylint: disable=redefined-builtin
    get_input = input # pylint: disable=invalid-name
except: # pylint: disable=bare-except
    get_input = raw_input # pylint: disable=invalid-name, undefined-variable

def setup():
    rcs = rfx.client.Session()
    rcs.cfg_load()
    data = dictlib.Obj()

    # QUERY for first things
    admin = get_input("admin key name (i.e.: adama): ")
    service = get_input("service name (i.e.: galactica): ")
    builder = get_input("builder name (i.e. jenkins): ")

    ########################################
    # master config for reflex tools
    create(rcs.create, "config", {
        "name":"reflex",
        "type":"parameter",
        "config": {
            "regions": {
                "local": {
                    "lanes": [
                        "tst",
                        "dev",
                        "qa",
                        "prd",
                        "sbx"
                    ],
                    "nbr": 1
                }
            },
            "lanes": {
                "tst": {
                    "short": "t",
                    "full": "Test"
                },
                "dev": {
                    "short": "d",
                    "full": "Development"
                },
                "qa": {
                    "short": "q",
                    "full": "QA"
                },
                "prd": {
                    "short": "p",
                    "full": "Production"
                },
                "sbx": {
                    "short": "x",
                    "full": "Sandbox"
                }
            }
        }
    })
    ########################################
    create(rcs.create, "apikey", {
        "name":admin
    })
    create(rcs.create, "apikey", {
        "name":service+"-prd"
    })
    create(rcs.create, "apikey", {
        "name":service+"-nonprd"
    })
    create(rcs.create, "apikey", {
        "name":builder+"-builder"
    })

    ########################################
    create(rcs.create, "group", {
        "name":"admin-apikey",
        "group":[admin],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"admin-super",
        "group":[admin],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"svc-keymaterial",
        "group":[service + "-prd"],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"devs",
        "group":[admin],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"pwds-admin",
        "group":[],
        "type":"password"
    })

    create(rcs.create, "group", {
        "name":service+"-prd",
        "group":[service+"-prd"],
        "type":"Apikey"
    })
    create(rcs.create, "group", {
        "name":service+"-nonprd",
        "group":[service+"-nonprd"],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"devops",
        "group":[admin],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"builders",
        "group":[builder+"-builder"],
        "type":"Apikey"
    })

    ############################################################################
    create(rcs.create, "policy", {
        "name": "admin-apikey",
        "order": 100,
        "result": "pass",
        "policy": "token_name in groups['admin-apikey'] and pwsin(http_headers, 'pwds-admin')"
    })
    create(rcs.create, "policyscope", {
        "name": "admin-apikey",
        "policy": "admin-apikey",
        "actions": 'read',
        "type": 'targeted',
        "matches": "obj_type in ['Apikey']"
    })

    #
    create(rcs.create, "policy", {
        "name": "admin-super",
        "policy": "token_name in groups['admin-super']",
        "or-stronger-policy": "token_name in groups['admin-super'] and pwsin(http_headers, 'pwds-admin')"
    })
    create(rcs.create, "policyscope", {
        "name": "admin-super",
        "policy": "admin-super",
        "actions": 'admin',
        "type": 'global',
        "matches": 'True',
    })

    # 
    create(rcs.create, "policy", {
        "name": "svc-keymaterial",
        "order": 100,
        "result": "fail",
        "description": "this is for compliance; any config ending with -keymaterial",
        "policy": "(token_name in groups.get('svc-keymaterial') and rx(r'^10\.', ip)) or (token_name in groups['admin-super'] and pwsin(http_headers, 'pwds-admin'))"
    })
    create(rcs.create, "policyscope", {
        "name": "svc-keymaterial",
        "policy": "svc-keymaterial",
        "actions": 'read',
        "type": 'targeted',
        "matches": "obj_type == 'Config' and obj['name'][-12:] == '-keymaterial'"
    })

    #
    create(rcs.create, "policy", {
        "name": "read-non-sensitive",
        "policy": "not sensitive and (token_name in groups.devs or token_name in groups.devops or token_name in groups['admin-super'])",
    })
    create(rcs.create, "policyscope", {
        "name": "read-non-sensitive",
        "policy": "read-non-sensitive",
        "actions": "read",
        "type": 'global',
        "matches": 'True'
    })

    print("NOTE: use command: `reflex app create --region=name --lanes=a,b,c NAME` for application specific configs")

    get_input("press [enter] to continue adding mockup service/config objects")

    ########################################
    create(rcs.create, "pipeline", {
        "name": service,
        "title": service,
    })

    create(rcs.create, "config", {
        "name": service,
        "type": "parameter",
        "setenv": {
            "PORT": 8080
        }
    })

    ########################################
    create(rcs.create, "pipeline", {
        "name": service,
        "title":  service,
    })

    create(rcs.create, "config", {
        "name": service,
        "type": "parameter",
        "extends": ["common"],
        "exports": [service + "-config1", service + "-config2"],
        "sensitive": {
            "parameters": {
                "DB-URI":"mongodb://%{DB-HOSTS}/%{DB-DBID}"
            },
            "config": {
                "db": {
                    "server": "%{DB-HOSTS}",
                    "db": "%{DB-DBID}",
                    "user": "%{DB-USER}",
                    "pass": "%{DB-PASS}",
                    "replset": {
                        "rs_name": "myReplicaSetName"
                    }
                }
            }
        },
        "setenv": {
            "DB-URI": "%{DB-URI}"
        }
    })
    create(rcs.create, "config", {
        "name": service + "-tst",
        "type": "parameter",
        "extends": [service],
        "exports": [service + "-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "DB-USER":"test_user",
                "DB-PASS":"not a good password",
                "DB-HOSTS":"test-db",
                "DB-DBID":"test_db"
            }
        }
    })
    create(rcs.create, "config", {
        "name": service + "-qa",
        "type": "parameter",
        "extends": [service],
        "exports": [service + "-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "DB-USER":"qa_user",
                "DB-PASS":"a better passwd",
                "DB-HOSTS":"qa-db",
                "DB-DBID":"qa_db"
            }
        }
    })
    create(rcs.create, "config", {
        "name": service + "-prd",
        "type": "parameter",
        "exports": [service + "-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "DB-USER":"test_user",
                "DB-PASS":"not a good password",
                "DB-HOSTS":"test-db",
                "DB-DBID":"test_db"
            }
        }
    })
    create(rcs.create, "config", {
        "name": "common",
        "type": "parameter",
        "sensitive": {
            "parameters": {
                "SHARED-SECRET":"moar"
            }
        }
    })

    create(rcs.create, "config", {
        "name": service + "-config1",
        "type": "file",
        "content": {
            "source": "local.xml.in",
            "dest": "local.xml",
            "varsub": True
        }
    })

    create(rcs.create, "config", {
        "name": service + "-config2",
        "type": "file",
        "content": {
            "dest":"local-production.json",
            "ref":"sensitive.config",
            "type":"application/json"
        }
    })

    create(rcs.create, "config", {
        "name": service + "-keystore",
        "type": "file",
        "content": {
            "dest":"local.keystore",
            "ref":"sensitive.data",
            "encoding": "base64"
        },
        "sensitive": {
            "data": "bm90IHJlYWxseSBhIGtleXN0b3JlIG9iamVjdAo="
        }
    })

    create(rcs.create, "service", {
        "name": service + "-tst",
        "config": service + "-tst",
        "title": service + " TST",
        "pipeline": service,
    })

    create(rcs.create, "service", {
        "name": service + "-qa",
        "config": service + "-qa",
        "title": service + " QA",
        "pipeline": service,
    })

    create(rcs.create, "service", {
        "name": service + "-prd",
        "config": service + "-prd",
        "title": service + " PRD",
        "pipeline": service,
    })

