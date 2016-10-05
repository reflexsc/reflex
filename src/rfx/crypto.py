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

"""Encryption Wrapper"""

import base64
import nacl.secret
import nacl.utils

################################################################################
# pylint: disable=too-few-public-methods
class Key(object):
    """
    Key wrapper

    >>> Key("MmRnFLmmF4iqXxKAhjkrkC/+pdABVQipeKSv2EZAqOY=").encode()
    'MmRnFLmmF4iqXxKAhjkrkC/+pdABVQipeKSv2EZAqOY='
    """

    value = b''

    def __init__(self, *existing):
        if existing:
            self.value = base64.b64decode(existing[0])
        else:
            self.value = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)

    def encode(self):
        """Return key encoded"""
        return base64.b64encode(self.value).decode()

################################################################################
class Cipher(object):
    """
    Generic Cipher Wrapper

    >>> key = Key('MmRnFLmmF4iqXxKAhjkrkC/+pdABVQipeKSv2EZAqOY=')
    >>> result = Cipher(key).key_encrypt("test", raw=True)
    >>> Cipher(key).key_decrypt(result, raw=True)
    'test'
    """

    key = None

    ############################################################################
    def __init__(self, key):
        """Create a secret key"""
        if isinstance(key, Key):
            self.key = key
        else:
            raise ValueError("argument is not a Key object")

    ############################################################################
    def key_encrypt(self, data, raw=False):
        """
        Encrypt data.
        Note: insert 'struct' to pull the first byte of data out as a "version"
        """
        nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
        encrypted = nacl.secret.SecretBox(self.key.value).encrypt(data.encode(), nonce)
        if raw:
            return encrypted
        return base64.b64encode(encrypted).decode()

    ############################################################################
    def key_decrypt(self, data, raw=False):
        """Decrypt data"""
        if not raw:
            data = base64.b64decode(data)
        return nacl.secret.SecretBox(self.key.value).decrypt(data).decode()

    # future: add pki_encrypt/decrypt
