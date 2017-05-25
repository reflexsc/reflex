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
# pylint: disable=no-member

"""
Internal Monitoring subsystem.

Monitors are defined on Reflex Engine pipeline->services and correlated to instances.

Multithreaded queue driven system which runs tests every 60 seconds.

Telemetry is sent to stdout in JSON format, and Reflex Engine instances are updated when
status changes.  Heartbeat is sent to statuscake to demonstrate it is operating.
"""

# System modules
try:
    import Queue as queue # pylint: disable=import-error
except: # pylint: disable=bare-except
    import queue # pylint: disable=import-error,wrong-import-order
import threading
import re
import time
import os
import traceback
import sys
import resource
import base64
import requests
import requests.exceptions
import ujson as json
import dictlib
import rfx
from rfx import set_interval
#from rfx.backend import Engine
import rfx.client

################################################################################
# this is an odd limit
# pylint: disable=too-many-instance-attributes
class Monitor(rfx.Base):
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
    rcs = None

    ###########################################################################
    # pylint: disable=super-init-not-called
    def __init__(self, base=None):
        if not base:
            raise ValueError("Missing reflex base definition")
        rfx.Base.__inherit__(self, base)
        self.rcs = rfx.client.Session(base=base)

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
        Periodically check in with Reflex Engine and refresh the list of what to monitor
        """
        self.thread_debug("Starting monitor refresh", module="update_monitors")

        # need to make a more efficient way of doing this via Reflex Engine
        monitors = []
        self.rcs.cache_reset()

        svcs = self.rcs.cache_list('service',
                                   cols=['pipeline', 'name',
                                         'active-instances'])
        for svc in svcs:
            try:
                pipeline = self.rcs.cache_get('pipeline', svc['pipeline'])
                for mon in pipeline.get('monitor', []):
                    self.DEBUG("monitor {}".format(mon))
                    mon['service'] = svc['name']
                    mon['pipeline'] = svc['pipeline']
                    for inst_name in svc.get('active-instances', []):
                        inst = self.rcs.cache_get('instance', inst_name)

                        # todo: insert: macro flatten

                        mymon = mon.copy()
                        mymon['instance'] = inst_name
                        mymon['target'] = inst['address']
                        mymon['title'] = svc['name'] + ": " + mon['name']
                        monitors.append(mymon)
            except KeyboardInterrupt:
                raise
            except: # pylint: disable=bare-except
                self.NOTIFY("Error in processing monitor:", err=traceback.format_exc())

        self.NOTIFY("Refreshed monitors", total_monitors=len(monitors))
        self.DEBUG("Monitors", monitors=monitors)

        # mutex / threadsafe?
        self.monitors = monitors
        cache = self.rcs._cache # pylint: disable=protected-access
        self.instances = cache['instance']
        self.services = cache['service']
        self.pipelines = cache['pipeline']
        self.thread_debug("Refresh complete", module="update_monitors")

    ############################################################################
    def thread_debug(self, *args, **kwargs):
        """
        Wrap debug to include thread information
        """
        if 'module' not in kwargs:
            kwargs['module'] = "Monitor"
        if kwargs['module'] != 'Monitor' and self.do_DEBUG(module='Monitor'):
            self.debug[kwargs['module']] = True
        if not self.do_DEBUG(module=kwargs['module']):
            return
        thread_id = threading.current_thread().name
        key = "[" + thread_id + "] " + kwargs['module']
        if not self.debug.get(key):
            self.debug[key] = True
        kwargs['module'] = key
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

        # if our status has changed, also update Reflex Engine
        if result['status'] != self.instances[monitor['instance']]['status']:
            # do some retry/counter steps on failure?
            self.instances[monitor['instance']]['status'] = result['status']
            self.rcs.patch('instance',
                           monitor['instance'],
                           {'status': result['status']})

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

        # ping heartbeat as a webhook
        if self.config.get('heartbeat-hook'):
            result = requests.get(self.config.get('heartbeat-hook'))
            if result.status_code != 200:
                self.NOTIFY("Heartbeat ping to statuscake failed!", level="ERROR")

        # keep a static copy of the last run stats
        self.last_stats = self.stats.copy()

    ############################################################################
    def stop_agent(self):
        """ TODO: ofind pid and kill """
        pass

    ############################################################################
    def start_agent(self, cfgin=True):
        """
        CLI interface to start 12-factor service
        """

        default_conf = {
            "threads": {
                "result": {
                    "number": 0,
                    "function": None
                },
                "worker": {
                    "number": 0,
                    "function": None
                },
            },
            "interval": {
                "refresh": 900,
                "heartbeat": 300,
                "reporting": 300,
                "test": 60
            },
            "heartbeat-hook": False
        }
        indata = {}
        if cfgin:
            indata = json.load(sys.stdin)
        elif os.environ.get("REFLEX_MONITOR_CONFIG"):
            indata = os.environ.get("REFLEX_MONITOR_CONFIG")
            if indata[0] != "{":
                indata = base64.b64decode(indata)
        else:
            self.NOTIFY("Using default configuration")

        conf = dictlib.union(default_conf, indata)

        conf['threads']['result']['function'] = self.handler_thread
        conf['threads']['worker']['function'] = self.worker_thread

        self.NOTIFY("Starting monitor Agent")
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
