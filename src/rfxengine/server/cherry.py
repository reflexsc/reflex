#!/app/local/bin/virtual-python
# vim modeline (put ":set modeline" into your ~/.vimrc)
# vim:set expandtab ts=4 sw=4 ai ft=python:
# pylint: disable=superfluous-parens

"""
General Webhooks and simple API routing
"""

import os
import sys
import time
import base64
import logging
import logging.config
import traceback
import argparse
import cherrypy._cplogging
import setproctitle
import dictlib
import timeinterval
import rfx
from rfx import json2data #, json4human
import rfxengine
from rfxengine import log
import rfxengine.memstate
import rfxengine.server.endpoints as endpoints
import rfxengine.db.objects as dbo
import rfxengine.db.mxsql as mxsql

################################################################################
# pylint: disable=protected-access
class CherryLog(cherrypy._cplogging.LogManager):
    """
    Because CherryPy logging and python logging are a hot mess.  Modern bitdata
    systems and 12-factor apps want key value logs for ease of use.  This gives
    us an easy switch by using Reflex's logging
    """

    ############################################################################
    def time(self):
        """do not want"""
        return ''

    ############################################################################
    # pylint: disable=redefined-outer-name
    def error(self, msg='', context='', severity=logging.INFO, traceback=False):
        """log error"""

        kwargs = {}
        if traceback:
            # pylint: disable=protected-access
            kwargs['traceback'] = cherrypy._cperror.format_exc()
            if not msg:
                msg = "error"

        if isinstance(msg, bytes):
            msg = msg.decode()

        log(msg, type=context, severity=severity, **kwargs)

    ############################################################################
    def access(self):
        """log access"""
        request = cherrypy.serving.request
        remote = request.remote
        response = cherrypy.serving.response
        outheaders = response.headers
        inheaders = request.headers
        if response.output_status is None:
            status = "-"
        else:
            status = response.output_status.split(" ".encode(), 1)[0]

        remaddr = inheaders.get('X-Forwarded-For', None) or \
                  remote.name or remote.ip

        if isinstance(status, bytes):
            status = status.decode()

        login = cherrypy.serving.request.login
        kwargs = dict()
        if login and login.token_name:
            kwargs['token'] = login.token_name
            # Notes: insert other auth attributes?

        log("http " + str(status),
            query=request.request_line,
            remote=remaddr,
            len=outheaders.get('Content-Length', '') or '-',
            **kwargs)

################################################################################
class Logger(logging.StreamHandler):
    """
    A handler class which allows the cursor to stay on
    one line for selected messages
    """
    on_same_line = False

    ############################################################################
    def configure(self, *args):
        """do not want"""
        pass

    ############################################################################
    def emit(self, record):
        """Overriding emit"""
        try:
            msg = record.msg.strip()
            log(msg)
        except (KeyboardInterrupt, SystemExit):
            raise
        except: # pylint: disable=bare-except
            self.handleError(record)

    ############################################################################
    # pylint: disable=redefined-builtin
    def format(self, record):
        return record.msg.decode()

################################################################################
class Server(rfx.Base):
    """
    central server
    """
    conf = None
    dbm = None
    stat = dictlib.Obj(heartbeat=dictlib.Obj(count=0, last=0))
    mgr = None
    cherry = None

    def __init__(self, *args, **kwargs):
        super(Server, self).__init__(*args, **kwargs)
        base = kwargs.get('base')
        self.cherry = cherrypy
        if base:
            rfx.Base.__inherit__(self, base)

    def monitor(self):
        """
        internal heartbeat from Cherrypy.process.plugins.Monitor
        """
        self.stat.heartbeat.last = time.time()

    # pylint: disable=too-many-locals
    def start(self, test=True):
        """
        Startup script for webhook routing.
        Called from agent start
        """

        cherrypy.log = CherryLog()
        cherrypy.config.update({
            'log.screen': False,
            'log.access_file': '',
            'log.error_file': ''
        })
        cherrypy.engine.unsubscribe('graceful', cherrypy.log.reopen_files)
        logging.config.dictConfig({
            'version': 1,
            'formatters': {
                'custom': {
                    '()': 'rfxengine.server.cherry.Logger'
                }
            },
            'handlers': {
                'console': {
                    'level':'INFO',
                    'class':'rfxengine.server.cherry.Logger', #logging.StreamHandler',
                    'formatter': 'custom',
                    'stream': 'ext://sys.stdout'
                }
            },
            'loggers': {
                '': {
                    'handlers': ['console'],
                    'level': 'INFO'
                },
                'cherrypy.access': {
                    'handlers': ['console'],
                    'level': 'INFO',
                    'propagate': False
                },
                'cherrypy.error': {
                    'handlers': ['console'],
                    'level': 'INFO',
                    'propagate': False
                },
            }
        })

        defaults = {
            'server': {
                'route_base': '/api/v1',
                'port': 54000,
                'host': '0.0.0.0'
            },
            'heartbeat': 10,
            'requestid': False,
            'cache': {
                'housekeeper': 60,
                'policies': 300,
                'sessions': 300,
                'groups': 300
            },
            'crypto': {
#                '000': {
# dd if=/dev...
#                    'key': "",
#                    'default': True,
#                }
            },
            'db': {
                'database': 'reflex_engine',
                'user': 'root'
            },
            'auth': {
                'expires': 300
            }
        }

        cfgin = os.environ.get('REFLEX_ENGINE_CONFIG')
        if cfgin:
            try:
                cfgin = json2data(base64.b64decode(cfgin))
            except: # pylint: disable=bare-except
                try:
                    cfgin = json2data(cfgin)
                except Exception as err: # pylint: disable=broad-except
                    traceback.print_exc()
                    self.ABORT("Cannot process REFLEX_ENGINE_CONFIG: " +
                               str(err) + " from " + cfgin)

            conf = dictlib.Obj(dictlib.union(defaults, cfgin))
        else:
            conf = dictlib.Obj(defaults)

        # cherry py global
        cherry_conf = {
            'server.socket_port': 9000,
            'server.socket_host': '0.0.0.0'
        }

        if dictlib.dig_get(conf, 'server.port'): # .get('port'):
            cherry_conf['server.socket_port'] = int(conf.server.port)
        if dictlib.dig_get(conf, 'server.host'): # .get('host'):
            cherry_conf['server.socket_host'] = conf.server.host

        # if production mode
        if test:
            log("Test mode enabled")
            conf['test_mode'] = True
        else:
            cherry_conf['environment'] = 'production'
            conf['test_mode'] = False

        # db connection
        self.dbm = mxsql.Master(config=conf.db, base=self, crypto=conf.get('crypto'))

        # configure the cache
        self.dbm.cache = rfxengine.memstate.Cache(**conf.cache.__export__())
        self.dbm.cache.start_housekeeper(conf.cache.housekeeper)

        # schema
        schema = dbo.Schema(master=self.dbm)
        schema.initialize(verbose=False, reset=False)
        sys.stdout.flush()

        cherrypy.config.update(cherry_conf)

        endpoint_conf = {
            '/': {
                'response.headers.server': "stack",
                'tools.secureheaders.on': True,
                'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
                'request.method_with_bodies': ('PUT', 'POST', 'PATCH'),
            }
        }
        cherrypy.config.update({'engine.autoreload.on': False})
        self.conf = conf

        # startup cleaning interval
        def clean_keys(dbm):
            """periodically called to purge expired auth keys from db"""
            dbo.AuthSession(master=dbm).clean_keys()

        timeinterval.start(conf.auth.expires * 1000, clean_keys, self.dbm)

        # mount routes
        cherrypy.tree.mount(endpoints.Health(conf, server=self),
                            conf.server.route_base + "/health",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Token(conf, server=self),
                            conf.server.route_base + "/token",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="config"),
                            conf.server.route_base + "/config",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="service"),
                            conf.server.route_base + "/service",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="pipeline"),
                            conf.server.route_base + "/pipeline",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="instance"),
                            conf.server.route_base + "/instance",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="build"),
                            conf.server.route_base + "/build",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="group"),
                            conf.server.route_base + "/group",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="apikey"),
                            conf.server.route_base + "/apikey",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="policy"),
                            conf.server.route_base + "/policy",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="policyscope"),
                            conf.server.route_base + "/policyscope",
                            endpoint_conf)
        cherrypy.tree.mount(endpoints.Object(conf, server=self,
                                             obj="state"),
                            conf.server.route_base + "/state",
                            endpoint_conf)

        # setup our heartbeat monitor
        int_mon = cherrypy.process.plugins.Monitor(cherrypy.engine,
                                                   self.monitor,
                                                   frequency=conf.heartbeat/2)
        int_mon.start()
        print("Base path={}".format(conf.server.route_base))
        cherrypy.engine.start()
        cherrypy.engine.block()

################################################################################
def main():
    """de mojo"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action='append')
    parser.add_argument("--test", action='store_true')

    args = parser.parse_args()

    base = rfx.Base(debug=args.debug, logfmt='txt').cfg_load()
    if args.test:
        base.timestamp = False
    else:
        base.timestamp = True
    setproctitle.setproctitle('reflex-engine') # pylint: disable=no-member
    rfxengine.SERVER = Server(base=base)
    rfxengine.SERVER.start(test=args.test)

################################################################################
if __name__ == "__main__":
    main()
