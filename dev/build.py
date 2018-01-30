# vim:set expandtab ts=4 sw=4 ai ft=python:
# vim modeline (put ":set modeline" into your ~/.vimrc)
# This is work extracted, drived or from Reflex, and is licensed using the GNU AFFERO License, Copyright Brandon Gillespie

"""Library for building"""

import common
import rfx.client

valid_statuses = ('incomplete', 'started', 'success', 'failure', 'skipped')
valid_states = ('done', 'failed', 'working', 'unknown')

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
    def __init__(self, rcs, name, version, core=None):
        self.rcs = rcs
        self.changes = list()
        self.name = name + "-" + version.replace('.', '-')
        self.core = core

        try:
            self.obj = self.rcs.get('build', self.name)
        except rfx.client.ClientError:
            self.obj = {
                "name": self.name,
                "application": name,
                "version": version,
                "services": [],
                "state": "unknown",
                "status": {}
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
    def promote(self, lane):
        """promote a build to another lane"""

        if self.obj.get('state') != 'done':
            raise ValueError("\nError: Cannot promote where the state is not done\n")

        # a list of services I have been attached to, over all time
        services = set(self.obj.get('services', list()))
        lanes = set(self.obj.get('lanes', [])).add(lane)

        # a list of services matching my pipeline (application name)
        svcs = list()
        for svc in self.rcs.list('service',
                                 match="%" + self.obj['application'] + "%",
                                 cols=['name', 'disabled', 'lane']):

            if svc.get('disabled', False):
                continue

            svc_lane = svc.get('lane')
            if not svc_lane:
                continue
            svc_lane = svc_lane.lower()

            if svc_lane == lane:
                svcs.append(svc['name'])
                services.add(svc['name'])

        if not svcs:
            raise ValueError("Unable to find any upstream services")

        # and update
        for svc in svcs:
            self.changes.append("Promoting: {}.target={}".format(svc, self.name))
            self.rcs.patch('service', svc, {
                "target": self.name
            })

        return self

    ############################################################################
    def add_info(self, data):
        """add info to a build"""
        for key in data:
            # verboten
            if key in ('status','state','name','id','application','services','version'):
                raise ValueError("Sorry, cannot set build info with key of {}".format(key))
            self.obj[key] = data[key]
        self.changes.append("Adding build info")
        return self

    ############################################################################
    def log(self, msg):
        if self.core:
            self.core.log(msg, level=common.log_msg)
        else:
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

