#!/app/local/bin/virtual-python
#$#HEADER-START
#
#   Reactor Configuration Event Engine
#
#   Copyright (C) 2016 Brandon Gillespie
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as published
#   by the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#$#HEADER-END

import os
import os.path
import sys

# wrapper for virtual python in "local" folder
#!/app/local/bin/virtual-python

import os
import os.path
import sys

# wrapper for virtual python in "local" folder

if len(sys.argv) < 2:
    sys.exit("no executable specified")

base = os.path.dirname(sys.argv[1])
argv = sys.argv[1:]

for d in ("/.", "/..", "/../.."):
    path = os.path.abspath(base + d + "/python")
    pathx = path + "/bin/python"
    if os.path.isdir(path) and os.access(pathx, os.X_OK):
        os.environ['VIRTUAL_ENV'] = base
        os.environ['PATH'] = base + "/bin:" + os.environ['PATH']
        os.execv(pathx, [pathx] + argv)

sys.exit("Unable to find virtual python")
