#$#HEADER-START
# vim:set expandtab ts=4 sw=4 ai ft=python:
#
#     Reactor Configuration Event Engine
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
Internal Monitoring subsystem.

Monitors are defined on Reactor Core pipeline->services and correlated to instances.

Multithreaded queue driven system which runs tests every 60 seconds.

Telemetry is sent to stdout in JSON format, and Reactor Core instances are updated when
status changes.  Heartbeat is sent to statuscake to demonstrate it is operating.
"""

# System modules
try:
    import Queue as queue # pylint: disable=import-error
except: # pylint: disable=bare-except
    import queue # pylint: disable=import-error,wrong-import-order
import threading # pylint: disable=wrong-import-order
import re # pylint: disable=wrong-import-order
import time # pylint: disable=wrong-import-order
import os # pylint: disable=wrong-import-order
import traceback # pylint: disable=wrong-import-order
import sys # pylint: disable=wrong-import-order
import resource # pylint: disable=wrong-import-order
import requests
import requests.exceptions
import ujson as json
import dictlib
import reactor
from reactor import set_interval
from reactor.backend import Core

################################################################################
# this is an odd limit
# pylint: disable=too-many-instance-attributes
class Monitor(reactor.Base):
    """
    Class used to drive server for monitoring.
    """
    config = {}
    monitors = []
    instances = {}
    services = {}
    pipelines = {}
    stats = dictlib.Obj(
        http_run=0,
        http_handled=0,
        total=0,
        procwin=0
    )
    last_stats = None
    workers_queue = queue.Queue()
    results_queue = queue.Queue()
    refresh_stopper = None
    heartbeat_stopper = None
    reporting_stopper = None
    thread_stopper = threading.Event()
    core = None

    ###########################################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if not base:
            raise ValueError("Missing reactor base definition")
        reactor.Base.__inherit__(self, base)
        self.core = Core(base=base)

    ############################################################################
    def configure(self, config):
        """
        Configure Monitor, pull list of what to monitor, initialize threads
        """
        self.config = config
        self.update_monitors()

        # initialize thread pools
        for profile in ('worker', 'result'):
            for _ in range(config['threads'][profile]['number']):
                worker = threading.Thread(target=config['threads'][profile]['function'])
                worker.daemon = True
                worker.start()

        # send a heartbeat right away
        self.heartbeat()

        # setup interval jobs
        self.refresh_stopper = set_interval(config['interval']['refresh']*1000,
                                            self.update_monitors)
        self.heartbeat_stopper = set_interval(config['interval']['heartbeat']*1000,
                                              self.heartbeat)
        self.reporting_stopper = set_interval(config['interval']['reporting']*1000,
                                              self.reporting)

        return self

    ############################################################################
    def feed_monitors(self):
        """
        Pull from the cached monitors data and feed the workers queue.  Run
        every interval (refresh:test).
        """
        self.thread_debug("Filling worker queue...", module='feed_monitors')
        for mon in self.monitors:
            self.thread_debug("    Adding " + mon['title'])
            self.workers_queue.put(mon)

    ############################################################################
    def start(self):
        """
        The main loop, run forever.
        """
        while True:
            self.thread_debug("Interval starting")
            for thr in threading.enumerate():
                self.thread_debug("    " + str(thr))
            self.feed_monitors()
            start = time.time()
            # wait fore queue to empty
            self.workers_queue.join()
            end = time.time()
            diff = self.config['interval']['test'] - (end - start)
            if diff <= 0:
                # alarm
                self.stats.procwin = -diff
                self.thread_debug("Cannot keep up with tests! {} seconds late"
                                  .format(abs(diff)))
            else:
                self.thread_debug("waiting {} seconds...".format(diff))
                time.sleep(diff)

    ############################################################################
    def update_monitors(self):
        """
        Periodically check in with Reactor Core and refresh the list of what to monitor
        """
        self.thread_debug("Starting monitor refresh", module="update_monitors")

        # need to make a more efficient way of doing this via Reactor Core
        new_monitors = []
        new_instances = {}
        new_services = {}
        new_pipelines = {}
        debug = self.do_DEBUG()
        def core_get(self, cache, otype, oname):
            """local cache wrapper around core.get_object"""
            if oname in cache:
                return cache[oname]
            obj = self.core.get_object(otype, oname, notify=debug)
            cache[oname] = obj
            return obj

        for svc_name in self.config['monitor-services']:
            try:
                svc = core_get(self, new_services, 'service', svc_name)
                pipeline = core_get(self, new_pipelines, 'pipeline', svc['pipeline'])
                for mon in pipeline['monitor']:
                    self.DEBUG("monitor {}".format(mon))
                    mon['service'] = svc_name
                    mon['pipeline'] = svc['pipeline']
                    for inst_name in svc['active-instances']:
                        inst = core_get(self, new_instances, 'instance', inst_name)

                        # todo: insert: macro flatten

                        mymon = mon.copy()
                        mymon['instance'] = inst_name
                        mymon['target'] = inst['address']
                        mymon['title'] = svc['name'] + ": " + mon['name']
                        new_monitors.append(mymon)
            except KeyboardInterrupt:
                raise
            except: # pylint: disable=bare-except
                self.NOTIFY("Error in processing monitor:", err=traceback.format_exc())

        self.NOTIFY("Refreshed monitors", total_monitors=len(new_monitors))

        # mutex / threadsafe?
        self.monitors = new_monitors
        self.instances = new_instances
        self.services = new_services
        self.pipelines = new_pipelines
        self.thread_debug("Refresh complete", module="update_monitors")

    ############################################################################
    def thread_debug(self, *args, **kwargs):
        """
        Wrap debug to include thread information
        """
        if 'module' not in kwargs:
            kwargs['module'] = "main"
        if not self.do_DEBUG(module=kwargs['module']):
            return
        thread_id = threading.current_thread().name
        kwargs['module'] = "[" + thread_id + "] " + kwargs['module']
        self.DEBUG(*args, **kwargs)

    ############################################################################
    def worker_thread(self):
        """
        The primary worker thread--this thread pulls from the monitor queue and
        runs the monitor, submitting the results to the handler queue.

        Calls a sub method based on type of monitor.
        """
        self.thread_debug("Starting monitor thread")
        while not self.thread_stopper.is_set():
            mon = self.workers_queue.get()
            self.thread_debug("Processing {type} Monitor: {title}".format(**mon))
            result = getattr(self, "_worker_" + mon['type'])(mon)
            self.workers_queue.task_done()
            self.results_queue.put({'type':mon['type'], 'result':result})

    ############################################################################
    def _worker_http(self, monitor):
        """
        Process an http monitor.
        """
        self.thread_debug("process_http", data=monitor, module='handler')
        query = monitor['query']
        method = query['method'].lower()
        self.stats.http_run += 1
        try:
            target = monitor['target']
            url = 'http://{host}:{port}{path}'.format(path=query['path'], **target)
            response = {
                'url': url,
                'status': 'failed',
                'result': {},
                'monitor': monitor,
                'message': 'did not meet expected result or no expected result defined',
                'elapsedms': monitor['timeout']*1000,
                'code':0
            }

            # not sed_env_dict -- we do not want to xref headers
            headers = query.get('headers', {})
            for elem in headers:
                headers[elem] = self.sed_env(headers[elem], {}, '')

            res = response['result'] = getattr(requests, method)(url,
                                                                 headers=headers,
                                                                 timeout=monitor['timeout'])
            response['code'] = res.status_code
            response['elapsedms'] = res.elapsed.total_seconds() * 1000
            if 'response-code' in monitor['expect']:
                if int(monitor['expect']['response-code']) == res.status_code:
                    response['message'] = ''
                    response['status'] = 'ok'
                else: # abort with failure, do not pass go
                    return response

            if 'content' in monitor['expect']:
                if monitor['expect']['content'] in res.text:
                    response['message'] = ''
                    response['status'] = 'ok'
                else: # abort with failure, do not pass go
                    return response

            if 'regex' in monitor['expect']:
                if re.search(monitor['expect']['regex'], res.text):
                    response['message'] = ''
                    response['status'] = 'ok'
                else: # abort with failure, do not pass go
                    return response

        except requests.exceptions.Timeout:
            response['message'] = 'timeout'
        except requests.exceptions.ConnectionError:
            response['message'] = 'connect-failed'
            response['elapsedms'] = -1
        return response

    ############################################################################
    def handler_thread(self):
        """
        A handler thread--this pulls results from the queue and processes them
        accordingly.

        Calls a sub method based on type of monitor.
        """
        self.thread_debug("Starting handler thread")
        while not self.thread_stopper.is_set():
            data = self.results_queue.get()
            self.thread_debug("Handling Result", module="handler")
            getattr(self, "_handler_" + data['type'])(data['result'])

    ############################################################################
    def _handler_http(self, result):
        """
        Handle the result of an http monitor
        """
        monitor = result['monitor']
        self.thread_debug("process_http", data=monitor, module='handler')
        self.stats.http_handled += 1

        # splunk will pick this up
        logargs = {
            'type':"metric",
            'endpoint': result['url'],
            'pipeline': monitor['pipeline'],
            'service': monitor['service'],
            'instance': monitor['instance'],
            'status': result['status'],
            'elapsed-ms': round(result['elapsedms'], 5),
            'code': result['code']
        }
        self.NOTIFY(result['message'], **logargs)

        # if our status has changed, also update Reactor Core
        if result['status'] != self.instances[monitor['instance']]['status']:
            # do some retry/counter steps on failure?
            self.instances[monitor['instance']]['status'] = result['status']
            self.core.delta_update_object('instance',
                                          monitor['instance'],
                                          {'status': result['status']}
                                         )

    ############################################################################
    def reporting(self):
        """
        report on consumption info
        """
        self.thread_debug("reporting")
        res = resource.getrusage(resource.RUSAGE_SELF)
        self.NOTIFY("",
                    type='internal-usage',
                    maxrss=round(res.ru_maxrss/1024, 2),
                    ixrss=round(res.ru_ixrss/1024, 2),
                    idrss=round(res.ru_idrss/1024, 2),
                    isrss=round(res.ru_isrss/1024, 2),
                    threads=threading.active_count(),
                    proctot=len(self.monitors),
                    procwin=self.stats.procwin)

    ############################################################################
    def heartbeat(self):
        """
        Watch our counters--as long as things are incrementing, send a ping to
        statuscake sayin we are alive and okay.
        """

        self.thread_debug("heartbeat")

        # check stats -- should be incrementing
        if self.last_stats:
            if self.stats.http_run <= self.last_stats.http_run:
                self.NOTIFY("No monitors run since last heartbeat!", service="heartbeat")
                return
            elif self.stats.http_handled <= self.last_stats.http_handled:
                self.NOTIFY("No monitor results handled since last heartbeat!", service="heartbeat")
                return

        # ping statuscake
        result = requests.get(self.config['heartbeat-hook'])
        if result.status_code != 200:
            self.NOTIFY("Heartbeat ping to statuscake failed!", level="ERROR")

        # keep a static copy of the last run stats
        self.last_stats = self.stats.copy()

    ############################################################################
    def start_agent(self):
        """
        CLI interface to start 12-factor service
        """
        self.NOTIFY("Starting monitor Agent")

        if 'APP_SERVICE' not in os.environ:
            self.ABORT("Must be run via launch control service action call")

        core_conf = json.load(sys.stdin)
        conf = core_conf['sensitive']['config']['monitor']
        conf['threads']['result']['function'] = self.handler_thread
        conf['threads']['worker']['function'] = self.worker_thread

        try:
            self.configure(conf).start()
        except KeyboardInterrupt:
            self.thread_stopper.set()
            if self.refresh_stopper:
                self.refresh_stopper.set()
            if self.heartbeat_stopper:
                self.heartbeat_stopper.set()
            if self.reporting_stopper:
                self.reporting_stopper.set()

