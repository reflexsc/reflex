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
# pylint: disable=missing-docstring

"""
Configuration Object Fu
"""

import re
try:
    import StringIO as strio # pylint: disable=import-error
except: # pylint: disable=bare-except
    import io as strio # pylint: disable=reimported,ungrouped-imports
import os
import base64
import copy
import rfx
from rfx import json4store
import dictlib

############################################################
def deep_get(obj, key):
    """
    >>> deep_get({"a":{"b":{"c":1}}}, "a.b.c")
    1
    """
    array = key.split(".")
    return _deep_get(obj, *array)

def _deep_get(obj, *key):
    """
    Recursively lookup an item in a nested dictionary,
    using an array of indexes

    >>> _deep_get({"a":{"b":{"c":1}}}, "a", "b", "c")
    1
    """
    if len(key) == 1:
        return obj[key[0]]
    return _deep_get(obj[key[0]], *key[1:])

################################################################################
class VerboseBase(rfx.Base):
    verbose = True

    def __init__(self, *args, **kwargs):
        if not kwargs.get('verbose'):
            self.verbose = False
        super(VerboseBase, self).__init__(*args, **kwargs)

    # pylint: disable=invalid-name,inconsistent-return-statements
    def NOTIFY(self, *msg, **kwargs):
        if self.verbose:
            return super(VerboseBase, self).NOTIFY(*msg, **kwargs)

    # pylint: disable=invalid-name
    def DEPRECATE(self, msg):
        self.NOTIFY("DEPRECATED: " + msg)

################################################################################
class ConfigProcessor(VerboseBase):
    """
    Process configuration objects.  Keeps state and does recursion.

    .flatten() will bring everything into one object, with variables substituted
    .commit() will take the object and put it out to disk

    Notes on data elements:

    - extends:
        * processed by .flatten()
        * goes up a heirarchy, recursively
        * preference given to values lower in tree (child overrides parent)
        * ignored on exported target
    - imports:
        * processed by .flatten()
        * merges a single object into current
        * preference given to values on imported object, not current
        * imports does not merge 'extends' nor 'imports' values
        * can be used on exported targets
    - exports:
        * processed by .commit(), which also re-triggers .flatten() on exported objects
        * target objects are merged with original object
        * preference given to target object values
        * extends is not processed on target objects (no hierarchy processed)
        * imports is processed on target objects
        * exported objects should be type=file, otherwise nothing happens
    """

    cfgdir = ''
    did = None
    rx_var = re.compile(r"%\{([a-zA-Z0-9_.-]+)\}") # macro_expand
    rx_env = re.compile(r"\$(\{([a-zA-Z0-9_]+)\}|([a-zA-Z0-9_]+))") # environ_var
    expanded_vars = None
    rcs = None
    peers = None

    ############################################################################
    # pylint: disable=too-many-arguments
    def __init__(self, verbose=True, base=None, rcs=None, peers=None, engine=None):
        super(ConfigProcessor, self).__init__(verbose=verbose)
        if not base: # sorry, we need rfx.Base
            raise ValueError("Missing Reflex Base (base=)")
        if not rcs: # and rcs
            if engine:
                if engine.session:
                    self.rcs = engine.session
                else:
                    raise ValueError("Missing Reflex Engine (rcs or engine)")
            else:
                raise ValueError("Missing Reflex Engine (rcs=)")
        rfx.Base.__inherit__(self, base)
        self.rcs = rcs # allows for overriding during testing
        self.verbose = verbose
        self.did = dictlib.Obj(exp=dict(), imp=dict(), ext=dict())
        if peers:
            self.peers = peers
        else:
            self.peers = dict()


    ############################################################################
    # pylint: disable=invalid-name,inconsistent-return-statements
    def NOTIFY(self, *msg, **kwargs):
        if self.verbose:
            return super(ConfigProcessor, self).NOTIFY(*msg, **kwargs)

    ############################################################
    def macro_expand_dict(self, dictionary):
        """
        For every entry in dictionary, process macro's on all
        values, using the dict as the lookup table.

        >>> base = rfx.Base()
        >>> test = ConfigProcessor(base=base, rcs=base)
        >>> test.macro_expand_dict({"a":"Hello",
        ...                         "b":"Not %{a}",
        ...                         "c":"c=%{c}",
        ...                         "d":"more %{b}"})
        {'a': 'Hello', 'c': 'c=', 'b': 'Not Hello', 'd': 'more Not Hello'}
        """
        for key in dictionary:
            dictionary[key] = self.macro_expand(dictionary[key], dictionary, source=key)
        return dictionary

    ############################################################

    def macro_expand(self, value, dictionary, source=None):
        """
        Expand our macro structure, referencing dictionary for
        replacement values.  Value may contain multiple macros.

        >>> base = rfx.Base()
        >>> test = ConfigProcessor(base=base, rcs=base)
        >>> test.macro_expand("hi %{VAR}", {"VAR":"there"})
        'hi there'

        >>> test.macro_expand("hi %{VAR}", {"VAR":"more %{T}", "T":"there"})
        'hi more there'

        >>> test.macro_expand("hi %{NOT_A_VAR}", {})
        'hi '

        >>> os.environ['NOT_A_VAR'] = 'from environ'
        >>> test.macro_expand("hi %{NOT_A_VAR}", {"NOT_A_VAR": "from dict"})
        'hi from environ'
        """

        def do_match(match):
            """Internal function for processing a matched variable"""
            match_key = match.group(1)
            if match_key in os.environ:
                return os.environ[match_key]
            dict_match = dictlib.dig_get(dictionary, match_key, None)
            if match_key != source and dict_match != None:
                return str(dict_match)
            self.NOTIFY("Unable to find expansion match for key '" + match_key + "'")
            return None

        # loop to recurse properly (handling macros in macros)
        if isinstance(value, bytes):
            value = value.decode()
        if not isinstance(value, str):
            value = str(value)
        # first do our configs
        while self.rx_var.search(value):
            value = self.rx_var.sub(do_match, value)
        return value

    ############################################################################
    def flatten(self, name, exported=False):
        conf = Config(name, base=self, rcs=self.rcs, verbose=self.verbose).conf
        conf = self._flatten(conf, name, exported=exported, toplevel=True)
        return self._process(conf)

    ############################################################################
    def _process(self, conf):
        subname = (conf.name + ".").split(".")[0]
        self.NOTIFY("Processing config object {0}".format(subname))
        # process the variables
        self.expanded_vars = allvars = dict()
        procvars = set(['sensitive.parameters'] + conf.procvars)

        # pass1, merge all vars into one monster dictionary
        for key in procvars:
            dictlib.union(allvars, deep_get(conf, key))

        # pass2 expand all variables
        self.macro_expand_dict(allvars)

        # pass3 put values back in their place
        for target in procvars:
            tobj = deep_get(conf, target)
            if not isinstance(tobj, dict):
                self.ABORT("Target {} is not a dictionary".format(dict))
            for key in tobj:
                tobj[key] = allvars[key]

        # pass4, do setenv last, it does not merge into allvars view
        for key in conf.setenv:
            conf.setenv[key] = self.macro_expand(conf.setenv[key], allvars)
            #conf.setenv[key] = self.sed_env(conf.setenv[key], allvars, key)

        if conf.content.get('dest'):
            conf.content.dest = self.macro_expand(conf.content.dest, allvars)
        if conf.get('file'):
            self.DEPRECATE("conf.file should be conf.content.dest")
            conf.file = self.macro_expand(conf.file, allvars)

        if conf.content.get('source'):
            conf.content.source = self.macro_expand(conf.content.source, allvars)

        return conf

    ############################################################################
    def _flatten(self, conf, name, exported=False, toplevel=False):
        """Merge in all relatives"""
        if name in self.did.ext:
            return conf
        self.did.ext[name] = True

        new = Config(name, base=self, rcs=self.rcs, verbose=self.verbose).load()

        def vals_are_same(key, d1, d2):
            if type(d1[key]) == type(d2[key]): # pylint: disable=unidiomatic-typecheck
                return True
            else:
                raise TypeError("Cannot merge {}: key={}, values are not the same!"
                                .format(name, key))

        def dmerge_outer(conf, new, key):
            if key in new and new[key]:
                vals_are_same(key, new, conf)
                conf[key] = dictlib.union(conf[key], new[key])

        def dmerge_inner(conf, new, key):
            if key in new and new[key]:
                vals_are_same(key, new, conf)
                conf[key] = dictlib.union(new[key], conf[key])

        def lmerge(conf, new, key):
            if key in new and new[key]:
                vals_are_same(key, new, conf)

                # sets are not ordered, so do it by hand; set-add old array to new
                for item in conf[key]:
                    if not item in new[key]:
                        new[key].append(item)
                conf[key] = new[key]

        lmerge(conf, new, 'imports')

        # single level inheritance
        if conf.imports:
            for iname in conf.imports:
                if iname in self.did.imp:
                    continue
                self.did.imp[iname] = True
                iconf = Config(iname, base=self, rcs=self.rcs, verbose=self.verbose).load()

                # imports ignores `extends` and `imports`
                dmerge_outer(conf, iconf, 'sensitive')
                dmerge_outer(conf, iconf, 'setenv')
                dmerge_outer(conf, iconf, 'content')
                if not exported:
                    lmerge(conf, iconf, 'exports')

        # and merge into self
        lmerge(conf, new, 'extends')
        dmerge_inner(conf, new, 'sensitive')
        dmerge_inner(conf, new, 'setenv')
        if not exported:
            lmerge(conf, new, 'exports')

        # inheriting content is difficult
        # if we are top level or exporting, give that precedence
        if exported or toplevel:
            dmerge_outer(conf, new, 'content')

        # otherwise, check per value
        elif new.content and not conf.content:
            dmerge_inner(conf, new, 'content')
#            def content_var(name):
#                if new.content.get(name) and not conf.content.get(name):
#                    conf.content[name] = new.content.get(name)
#            content_var('dest')
#            content_var('source')
#            content_var('type')
#            content_var('varsub')
#            content_var('encoding')
#            content_var('ref')

        if new.file and not conf.file:
            conf.file = new.file

        lmerge(conf, new, 'procvars')

        if not exported:
            for parent in new.extends:
                self._flatten(conf, parent)

        # set type last
        conf.type = new.type
        return conf

    ############################################################################
    def commit(self, conf, dest=None, exported=False):
        """
        Update environment and store files.
        Split into two steps so we can discretely call setenv without exporting files.
        """
        self.prep_setenv(conf, dest=dest, exported=exported)
        self.export_files(conf, dest=dest, exported=exported)

    ############################################################################
    # pylint: disable=unused-argument
    def prep_setenv(self, conf, dest=None, exported=False):
        """export any files"""
        if dest: # override default
            self.cfgdir = dest
        elif 'APP_CFG_BASE' in os.environ:
            self.cfgdir = os.environ['APP_CFG_BASE']
        else:
            raise ValueError("APP_CFG_BASE not defined")

        def envset(key, value):
            if key in conf.setenv and conf.setenv[key] != value:
                self.NOTIFY("Overriding existing setenv: " + key)
            conf.setenv[key] = value

        def envset_from_os(key, default):
            envset(key, os.environ.get(key, default))

        envset('APP_CFG_BASE', self.cfgdir)
        envset_from_os('APP_RUN_BASE', '')
        envset_from_os('APP_PIPELINE', '')
        envset_from_os('APP_SERVICE', '')
        envset_from_os('TMPDIR', '.')

        for key, value in conf.setenv.items():
            os.environ[key] = str(value)

    ############################################################################
    # pylint: disable=unused-argument
    def export_files(self, conf, dest=None, exported=False):
        """
        Export Files.
        """
        # export any objects
        if not exported:
            for name in conf.exports:
                if name in self.did.exp:
                    continue
                self.did.exp[name] = True
                exp = copy.deepcopy(conf)
                exp.name = name
                exp = self._flatten(exp, name, exported=True)
                exp = self._process(exp)
                self.commit(exp, dest=self.cfgdir, exported=True)

        # and process it
        if conf.type == 'file':
            self._commit_file(conf)
        else:
            self.DEBUG("Nothing to commit on {name}.type='{type}'".format(**conf))

    ############################################################################
    # pylint: disable=no-self-use
    def _decode_none(self, in_f):
        """Return contents of a file, with no decoding"""
        return in_f.read()

    ############################################################################
    # pylint: disable=no-self-use
    def _decode_base64(self, in_f):
        """Return contents of a file, with base64 decoding"""
        return base64.b64decode(in_f.read())

    ############################################################################
    def _commit_file(self, conf):
        """Process a conf, as a file type (store output)"""
        if not conf.content:
            raise ValueError("Object '{name}' missing 'content'".format(**conf))

        content = conf.content

        if content.get('dest'):
            fname = content.dest
        elif conf.get('file'):
            fname = conf.file
        else:
            raise ValueError("Cannot find output filename ({name}.content.dest)"
                             .format(**conf))

        # for security all files are relative to APP_CFG_BASE, no
        # .. paths allowed.
        if ".." in fname:
            raise ValueError("File name cannot contain ..")

        fname = self.cfgdir + "/" + fname

        # using ref allows us to store the data in "sensitive"
        # (data at rest) area:
        if content.get('ref'):
            buf = self._get_content_ref(conf)
        elif content.get('source'):
            buf = self._get_content_src(conf)
        else:
            raise ValueError("Cannot find file content for object " + conf.name)

        if content.get('varsub'):
            buf = self.macro_expand(buf, self.expanded_vars)

        if isinstance(buf, str): # writing everything binary
            buf = buf.encode()

        subname = (conf.name + ".").split(".")[0]
        self.NOTIFY("CONFIG {0} into {1}".format(subname, fname))
        with open(fname, 'wb') as ofile:
            ofile.write(buf)

    def _get_content_ref(self, conf):
        ref = conf.content.ref
        if isinstance(ref, str):
            data = deep_get(conf, ref)
        elif isinstance(ref, list):
            self.DEPRECATE("conf.content.ref should be a string in x.z.y notation.")
            data = _deep_get(conf, *ref)
        else:
            raise ValueError("Unrecognized data type for conf.content.ref ({})"
                             .format(type(conf.content.ref)))

        if conf.content.get('type', 'text/plain') == 'application/json':
            if isinstance(data, dictlib.Obj):
                data = data.__original__()
            data = json4store(data)

        # pylint: disable=line-too-long
        return getattr(self, "_decode_" + conf.content.get('encoding', 'none'))(strio.StringIO(data))

    def _get_content_src(self, conf):
        infile = self.cfgdir + "/" + conf.content.source
        if not os.path.exists(infile):
            raise ValueError("Unable to process config file: " + infile)
        with open(infile, 'rt') as infile:
            content_f = strio.StringIO(infile.read())

        return getattr(self, "_decode_" + conf.content.get('encoding', 'none'))(content_f)

    def prune(self, conf):
        """
        Cleanup the config, removing extraneous bits.

        Run /after/ commit.
        """

        def ifdel(key):
            """shortcut"""
            if key in conf:
                del conf[key]

        ifdel('file')
        ifdel('procvars')
        ifdel('extends')
        ifdel('imports')
        ifdel('exports')
        ifdel('type')
        ifdel('macroExpand')

        return conf

################################################################################
class Config(VerboseBase):
    """
    Manage configurations

    .get/put/etc -- just the named object
    .dump() -- return a json form << replaces .export() >>
    """

    conf = None
    rcs = None

    ############################################################################
    def __init__(self, name, base=None, rcs=None, verbose=True):
        super(Config, self).__init__(verbose=verbose)
        if not base: # we need rfx.Base
            raise ValueError("Missing Reflex Base (base=)")
        rfx.Base.__inherit__(self, base)
        if not rcs: # and a rfx.Engine() object
            raise ValueError("Missing Reflex Engine (rcs=)")

        self.conf = dictlib.Obj(
            name=name,
            sensitive=dictlib.Obj(
                parameters=dict(
                )
            ),
            exports=list(),
            imports=list(),
            extends=list(),
            setenv=dict(),
            content=dict(),
            procvars=list(),
            file='',
            type='parameter'
        )

        self.rcs = rcs
        self.peers = base.peers # should come in from ConfigProcessor
        if self.peers:
            self.conf['peers'] = self.peers
            for iplabel in ('0', '1'):
                peers = self.peers['ip' + iplabel]

                self.conf.setenv['LAUNCH_PEERS' + iplabel] = \
                    ",".join([key + "@" + value for key, value in peers.items()])
                self.conf.setenv['LAUNCH_PEER' + iplabel + '_NAMES'] = ",".join(list(peers.keys()))
                self.conf.setenv['LAUNCH_PEER' + iplabel + '_IPS'] = ",".join(list(peers.values()))

    def load(self):
        obj = self.rcs.get('config', self.conf.name)
        if obj:
            dictlib.union(self.conf, obj)

        if 'export' in self.conf:
            self.DEPRECATE("conf.export should be conf.exports")
            self.conf.exports = self.conf.export

        if 'macroExpansion' in self.conf:
            self.conf.content['varsub'] = self.conf.macroExpansion
            self.DEPRECATE("conf.macroExpansion should be conf.content.varsub")

        return self.conf

    ############################################################################
    def __repr__(self):
        """Return a text representation of the object"""
        return json4store(self.conf)

