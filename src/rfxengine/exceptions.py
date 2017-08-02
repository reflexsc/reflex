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

"""
Errors
"""

################################################################################
# exceptions

class InvalidPolicy(Exception):
    """We had a problem houston"""
    pass

class InvalidContext(Exception):
    """We had a problem houston"""
    pass

class AuthFailed(Exception):
    """Unauthorized"""
    pass

class PolicyFailed(Exception):
    """We had a problem houston"""
    pass

class ObjectNotFound(Exception):
    """Returned when a requested object cannot be found"""
    pass

class NoArchive(Exception):
    """The specified object doesn't support Archives"""
    pass

class ObjectExists(Exception):
    """Returned when there are relationship problems"""
    pass

#class RelationshipException(Exception):
#    """Returned when there are relationship problems"""
#    pass

class NoChanges(Exception):
    """Nothing was changed"""
    pass

class CipherException(Exception):
    """Problems w/crypto"""
    pass

class InvalidParameter(Exception):
    """Variant error for catching bad params"""
    pass
