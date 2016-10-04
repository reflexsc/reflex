#!/app/local/bin/virtual-python
# vim modeline (put ":set modeline" into your ~/.vimrc)
# vim:set expandtab ts=4 sw=4 ai ft=python:
# pylint: disable=superfluous-parens

"""
General Webhooks and simple API routing
"""

import sys
import time
import logging
import logging.config
import argparse
import cherrypy._cplogging
import setproctitle
import dictlib
import timeinterval
import reactor
import reactor.core
from reactor.core import log
from reactor import json2data
import reactor.core.memstate
import reactor.core.server.endpoints as endpoints
import reactor.core.db.objects as dbo
import reactor.core.db.mxsql as mxsql

################################################################################
# pylint: disable=protected-access
class CherryLog(cherrypy._cplogging.LogManager):
    """
    Because CherryPy logging and python logging are a hot mess.  Modern bitdata
    systems and 12-factor apps want key value logs for ease of use.  This gives
    us an easy switch by using Reactor's logging
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
class Server(reactor.Base):
    """
    central server
    """
    conf = None
    dbm = None
    stat = dictlib.Obj(heartbeat=dictlib.Obj(count=0, last=0))
    mgr = None

    def __init__(self, *args, **kwargs):
        super(Server, self).__init__(*args, **kwargs)
        base = kwargs.get('base')
        if base:
            reactor.Base.__inherit__(self, base)

    def monitor(self):
        """
        internal heartbeat from Cherrypy.process.plugins.Monitor
        """
        self.stat.heartbeat.last = time.time()

    # pylint: disable=too-many-locals
    def start(self, test=True, cfgin=False):
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
                    '()': 'reactor.core.server.cherry.Logger'
                }
            },
            'handlers': {
                'console': {
                    'level':'INFO',
                    'class':'reactor.core.server.cherry.Logger', #logging.StreamHandler',
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

        # bring i our config
        if not cfgin:
            print("--cfgin required")
            sys.exit(0)
        stdin = sys.stdin.read().strip()
        defaults = {
            'server': {
                'route_base': '/api/v1',
                'port': 54321,
                'host': '127.0.0.1'
            },
            'heartbeat': 10,
            'cache': {
                'housekeeper': 60,
                'policies': 300,
                'sessions': 300
            },
            'secrets': [],
            'db': {
                'database': 'reactor_core',
                'user': 'root'
            },
            'auth': {
                'expires': 300
            }
        }
        conf = dictlib.Obj(dictlib.union(defaults,
                                         dictlib.dig(json2data(stdin),
                                                     "sensitive.config")))

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
        self.dbm = mxsql.Master(config=conf.db, base=self)

        # configure the cache
        self.dbm.cache = reactor.core.memstate.Cache()
        self.dbm.cache.start_housekeeper(conf.cache.housekeeper)
        self.dbm.cache.configure('policy', conf.cache.policies)
        self.dbm.cache.configure('policymap', conf.cache.policies)
        self.dbm.cache.configure('policymatch', conf.cache.policies)
        self.dbm.cache.configure('session', conf.cache.sessions)

        # schema
        schema = dbo.Schema(master=self.dbm)
        schema.initialize(verbose=False, reset=False)

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
                                             obj="policymatch"),
                            conf.server.route_base + "/policymatch",
                            endpoint_conf)

        # setup our heartbeat monitor
        int_mon = cherrypy.process.plugins.Monitor(cherrypy.engine,
                                                   self.monitor,
                                                   frequency=conf.heartbeat/2)
        int_mon.start()

        cherrypy.engine.start()
        cherrypy.engine.block()

################################################################################
def main():
    """de mojo"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action='append')
    parser.add_argument("--test", action='store_true')
    parser.add_argument("--cfgin", action='store_true')
    # BJG: see note on logfmt
#    parser.add_argument("--logfmt", choices=['txt', 'json'], default='json')
    parser.add_argument("action", choices=["start"])

    args = parser.parse_args()

    base = reactor.Base(debug=args.debug, logfmt='txt').cfg_load()
    if args.test:
        base.timestamp = False
    else:
        base.timestamp = True
    setproctitle.setproctitle('reactor-cored') # pylint: disable=no-member
    reactor.core.SERVER = Server(base=base)

    if args.action == "start":
        reactor.core.SERVER.start(test=args.test, cfgin=args.cfgin)
    else:
        base.ABORT("Unrecognized action: " + args.action)

################################################################################
if __name__ == "__main__":
    main()
