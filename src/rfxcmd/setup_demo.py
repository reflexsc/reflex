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

def setup():
    rcs = rfx.client.Session()
    rcs.cfg_load()
    data = dictlib.Obj()

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
                        "qa",
                        "prd",
                        "sbx"
                    ]
                }
            },
            "lanes": {
                "tst": {
                    "short": "t",
                    "full": "Test"
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
        "name":"kirk"
    })
    create(rcs.create, "apikey", {
        "name":"spock"
    })
    create(rcs.create, "apikey", {
        "name":"redshirt"
    })
    create(rcs.create, "apikey", {
        "name":"svc-bct",
    })
    create(rcs.create, "apikey", {
        "name":"svc-eng",
    })

    data['keys'] = dictlib.Obj({
        'kirk': do(rcs.get, "apikey", "kirk"),
        'spock': do(rcs.get, "apikey", "spock"),
        'redshirt': do(rcs.get, "apikey", "redshirt"),
        'svc-bct': do(rcs.get, "apikey", "svc-bct"),
        'svc-eng': do(rcs.get, "apikey", "svc-eng"),
    })

    ########################################
    create(rcs.create, "group", {
        "name":"bridge",
        "group":["kirk", "spock"],
        "type":"Apikey"
    })

    create(rcs.create, "group", {
        "name":"support",
        "group":["redshirt"],
        "type":"Apikey"
    })

    create(rcs.create, "policy", {
        "name": "bridge-all-access",
        "policy": "token_name in groups.bridge"
    })

    create(rcs.create, "policy", {
        "name": "support-not-sensitive",
        "policy": "token_name in groups.support and sensitive == False"
    })

    create(rcs.create, "policy", {
        "name": "support-sensitive",
        "policy": "token_name in groups.support and sensitive == True"
    })

    data['policies'] = dictlib.Obj({
        'bridge-all-access': do(rcs.get, "policy", "bridge-all-access"),
        'support-not-sensitive': do(rcs.get, "policy", "support-not-sensitive"),
        'support-sensitive': do(rcs.get, "policy", "support-sensitive"),
    })

    ########################################
    create(rcs.create, "policyscope", {
        "name": "bridge-all",
        "policy": "support-not-sensitive",
        "actions": 'read',
        "type": 'global',
        "matches": 'True'
    })
    create(rcs.create, "policyscope", {
        "name": "support-read-configs-sensitive",
        "policy": "support-sensitive",
        "actions": 'read',
        "type": 'global',
        "matches": 'obj_type == "Config" and re.match(r"^bct-", obj["name"])'
    })
    create(rcs.create, "policyscope", {
        "name": "support-read-configs",
        "policy": "support-not-sensitive",
        "actions": 'read',
        "type": 'global',
        "matches": 'obj_type == "Config" and not re.match(r"^bct-", obj["name"])'
    })

    ########################################
    create(rcs.create, "pipeline", {
        "name": "bridge-navigation",
        "title": "Navigation Console",
    })

    create(rcs.create, "config", {
        "name": "bridge-navigation",
        "type": "parameter",
        "setenv": {
            "PORT": 8080
        }
    })
    create(rcs.create, "config", {
        "name": "bridge-navigation-train",
        "type": "parameter",
        "extends": ["bridge-navigation"],
        "sensitive": {
           "parameters": {
               "mongodb-pass": "H77OsiFfuH",
               "mongodb-user": "nav_user",
               "mongodb-host": "mongo-x42020",
               "mongodb-name": "navigation_train"
           }
        }
    })

    create(rcs.create, "config", {
        "name": "bridge-navigation-battle",
        "type": "parameter",
        "extends": ["bridge-navigation"],
        "sensitive": {
           "parameters": {
               "mongodb-pass": "qhsm1LjO/52K",
               "mongodb-user": "nav_user",
               "mongodb-host": "mongo-x42020",
               "mongodb-name": "navigation_battle"
           }
        }
    })
    create(rcs.create, "config", {
        "name": "bridge-navigation-main",
        "type": "parameter",
        "extends": ["bridge-navigation"],
        "sensitive": {
           "parameters": {
               "mongodb-pass": "SKAtGgGl4x+IF",
               "mongodb-user": "nav_user",
               "mongodb-host": "mongo-x42020",
               "mongodb-name": "navigation_main"
           }
        }
    })

    create(rcs.create, "service", {
        "name": "bridge-navigation-train",
        "title": "Navigation Bridge - Training",
        "config": "bridge-navigation-train",
        "pipeline": "bridge-navigation",
    })

    create(rcs.create, "service", {
        "name": "bridge-navigation-battle",
        "config": "bridge-navigation-battle",
        "title": "Navigation Bridge - Battle",
        "pipeline": "bridge-navigation",
    })

    create(rcs.create, "service", {
        "name": "bridge-navigation-main",
        "config": "bridge-navigation-main",
        "title": "Navigation Bridge - Main",
        "pipeline": "bridge-navigation",
    })

    ########################################
    create(rcs.create, "pipeline", {
        "name": "bct",
        "title": "Bat'leth Combat Training"
    })

    create(rcs.create, "config", {
        "name": "bct",
        "type": "parameter",
        "extends": ["common"],
        "exports": ["bct-config1", "bct-config2"],
        "sensitive": {
            "parameters": {
                "MONGO-URI":"mongodb://%{MONGO-HOSTS}/%{MONGO-DBID}"
            },
            "config": {
                "db": {
                    "server": "%{MONGO-HOSTS}",
                    "db": "%{MONGO-DBID}",
                    "user": "%{MONGO-USER}",
                    "pass": "%{MONGO-PASS}",
                    "replset": {
                        "rs_name": "myReplicaSetName"
                    }
                }
            }
        },
        "setenv": {
            "MONGO-URI": "%{MONGO-URI}"
        }
    })
    create(rcs.create, "config", {
        "name": "bct-tst",
        "type": "parameter",
        "extends": ["bct"],
        "exports": ["bct-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "MONGO-USER":"test_user",
                "MONGO-PASS":"not a good password",
                "MONGO-HOSTS":"test-db",
                "MONGO-DBID":"test_db"
            }
        }
    })
    create(rcs.create, "config", {
        "name": "bct-qa",
        "type": "parameter",
        "extends": ["bct"],
        "exports": ["bct-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "MONGO-USER":"qa_user",
                "MONGO-PASS":"a better passwd",
                "MONGO-HOSTS":"qa-db",
                "MONGO-DBID":"qa_db"
            }
        }
    })
    create(rcs.create, "config", {
        "name": "bct-prd",
        "type": "parameter",
        "exports": ["bct-keystore"],
        "procvars": ["sensitive.config.db"],
        "sensitive": {
            "parameters": {
                "MONGO-USER":"test_user",
                "MONGO-PASS":"not a good password",
                "MONGO-HOSTS":"test-db",
                "MONGO-DBID":"test_db"
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
        "name": "bct-config1",
        "type": "file",
        "content": {
            "source": "local.xml.in",
            "dest": "local.xml",
            "varsub": True
        }
    })

    create(rcs.create, "config", {
        "name": "bct-config2",
        "type": "file",
        "content": {
            "dest":"local-production.json",
            "ref":"sensitive.config",
            "type":"application/json"
        }
    })

    create(rcs.create, "config", {
        "name": "bct-keystore",
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
        "name": "bct-tst",
        "config": "bct-tst",
        "title":"Bat'leth Combat Training - TST",
        "pipeline": "bct",
    })

    create(rcs.create, "service", {
        "name": "bct-qa",
        "config": "bct-qa",
        "title":"Bat'leth Combat Training - QA",
        "pipeline": "bct",
    })

    create(rcs.create, "service", {
        "name": "bct-prd",
        "config": "bct-prd",
        "title":"Bat'leth Combat Training - PRD",
        "pipeline": "bct",
    })

