# vim:set expandtab ts=4 sw=4 ai ft=python:
# vim modeline (put ":set modeline" into your ~/.vimrc)
# This is work extracted, drived or from Reflex, and is licensed using the GNU AFFERO License, Copyright Brandon Gillespie

"""Library for building"""

import common
import time
import rfx
import rfx.client

valid_statuses = ('incomplete', 'started', 'success', 'failure', 'skipped')
valid_states = ('ready', 'failed', 'working', 'unknown', 'done')

################################################################################
class Obj(object):
    """
    Interface with a reflex build object.
    call status, state and promote methods.
    call commit when done
    """

    rcs = None
    obj = None
    create = False
    name = ''
    changes = None
    core = None

    ############################################################################
    def __init__(self, rcs, target=name, svcobj=None, app=None, version=None, core=None):
        """
        two behaviors:
         - implicit create -- If app+version is specified, create a new
                              build if it is not defined.
         - pre-existing    -- specify `target` as the name of the build, OR
                              specify `svcobj` as the svc object data
        """
        self.rcs = rcs
        self.changes = list()
        if svcobj:
            self.name = target = svcobj.get('release', {}).get('current')
            if not self.name:
                raise ValueError("svcobj {} does not have a defined release.current".format(svcobj.obj.get('name')))
        elif target:
            self.name = target
        elif app and version:
            self.name = app + "-" + version.replace('.', '-')
        else:
            raise ValueError("Invalid initialization for Build object")

        try:
            self.obj = self.rcs.get('build', self.name)
        except rfx.client.ClientError:
            if target:
                raise ValueError("Unable to find build {}".format(self.name))
            else:
                self.obj = {
                    "name": self.name,
                    "application": app,
                    "version": version,
                    "state": "unknown",
                    "status": {},
                    "lanes": {},
                    # references
                }
                self.create = True

    ############################################################################
    def status(self, key, value):
        """Update the status of a build"""
        value = value.lower()
        if value not in valid_statuses:
            raise ValueError("Build Status must have a value from:\n{}".format(", ".join(valid_statuses)))

        self.obj['status'][key] = value
        self.changes.append("Updating build:{}.status.{}={}"
                            .format(self.obj['name'], key, value))
        return self

    ############################################################################
    def state(self, state):
        """Update the status of a build"""
        state = state.lower()
        if state not in valid_states:
            raise ValueError("Build state must have a value from:\n{}".format(", ".join(valid_state)))

        self.obj['state'] = state
        self.changes.append("Updating build:{}.state={}"
                            .format(self.obj['name'], state))
        return self

    ############################################################################
    def change(self, key, value):
        """Update any other attribute on the build object"""
        self.obj[key] = value
        self.changes.append("Updating build:{}.{}={}"
                            .format(self.obj['name'], key, value))
        return self

    ############################################################################
    # a list of services matching my pipeline (application name)
    def _get_services(self, lane, svcs=None):
        if svcs:
            return svcs.copy() # because python isn't functionally safe

        svcs = list()
        for svc in self.rcs.list('service',
                                 match=self.obj['application'],
                                 cols=['name', 'disabled', 'lane']):

            if svc.get('disabled', False):
                continue

            svc_lane = svc.get('lane')
            if not svc_lane:
                continue
            svc_lane = svc_lane.lower()

            if svc_lane == lane:
                svcs.append(svc['name'])

        if not svcs:
            raise ValueError("Unable to find any upstream services")

        return svcs

    ############################################################################
    def _prep_for_release(self, lane, svcs=None, meta=None):
        # avoid case sensitivity problems
        lane = lane.lower()

        # cannot promote if not ready
        if self.obj.get('state') not in ('done', 'ready'):
            raise ValueError("\nError: State is incorrect for releasing\n")

        # get services matching my name and designated lane
        svcs = self._get_services(lane, svcs=svcs)

        if not meta:
            meta = dict()

        return svcs, meta, lane

    ############################################################################
    def release(self, lane, status, target=None, meta=None, svcs=None):
        """Set release information on a build"""

        if target not in (None, 'current', 'future'):
            raise ValueError("\nError: Target must be None, 'current', or 'future'\n")

        svcs, meta, lane = self._prep_for_release(lane, svcs=svcs, meta=meta)
        when = time.time()

        # loathe non-functional dictionaries in python
        rel_data = meta.copy()
        rel_data.update({
            "_time": when,
            "status": status,
            "services": list(svcs.keys()),
        })
        rel_lane = self.obj.get('lanes', {}).get(lane, dict(log=[],status=status))
        rel_lane['status'] = status
        rel_lane['log'] = [rel_data] + rel_lane.get('log', [])

        self.rcs.patch('build', self.name, {
            "lanes": {
                lane: rel_lane,
            }
        })

        if target:
            for svc in svcs:
                rel_data = {target: self.name}

            # if target is specified, then also update svc.release
            #    {current/previous/future}
            if target == "current":
                mysvc = svcs[svc]
                curver = mysvc.get('release', {}).get('current', '')
                prev = []
                if curver:
                    prev = mysvc.get('release', {}).get('previous', [])
                    if not prev or prev[0] != curver:
                        prev = [curver] + prev
                    while len(prev) > 5: # magic values FTW
                        prev.pop() # only keep history of 5 previous
                rel_data['previous'] = prev

            self.rcs.patch('service', svc, {
                "release": rel_data,
                "statuses": {status: when},
                "status": status
            })

    ############################################################################
    def promote(self, lane, svcs=None, meta=None):
        """promote a build so it is ready for an upper lane"""

        svcs, meta, lane = self._prep_for_release(lane, svcs=svcs, meta=meta)

        # iterate and mark as future release
        for svc in svcs:
            self.changes.append("Promoting: {}.release.future={}".format(svc, self.name))
            self.rcs.patch('service', svc, {
                "release": {"future": self.name}, # new way
                "statuses": {"future": time.time()},
            })

        return self

    ############################################################################
    def add_info(self, data):
        """add info to a build"""
        for key in data:
            # verboten
            if key in ('status','state','name','id','application','services','release'):
                raise ValueError("Sorry, cannot set build info with key of {}".format(key))
            self.obj[key] = data[key]
        self.changes.append("Adding build info")
        return self

    ############################################################################
    def log(self, msg):
#        if self.rcs:
#            self.rcs.log(msg, level=common.log_msg)
#        else:
        print(msg)

    ############################################################################
    def commit(self):
        if not self.changes:
            self.log("No updates to build " + self.name)
            return self

        for change in self.changes:
            self.log(change)

        try:
            if self.create:
                self.rcs.update('build', self.name, self.obj)
            else:
                self.rcs.patch('build', self.name, self.obj)
        except rfx.client.ClientError as err:
            self.log("Unable to update reflex build object: " + str(err))

        return self

