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
Tabular formatting of output
"""

import sys

###########################################################################
# pylint: disable=dangerous-default-value, too-many-locals
def cols(columns, header=False, stderr=list(), sort=None):
    """
    Print columns tabularly, supporting optional headers and mixed
    stdout/stderr streams.

    >>> cols([[1,2,3], ["ba","bc","d"]])
    1  2  3
    ba bc d
    >>> def sort_second(row): return row[1]
    >>> cols([["longerthanlongerthanlong","ab","zz","d"], ["ba","bc","d", "z"]], sort=sort_second)
    longerthanlongerthanlong ab zz d
    ba                       bc d  z
    >>> cols([["dc","ab","zz","d"], ["ba","bc","d", "z"]], stderr=[1,2,3])
    ba
    dc
    >>> cols([["this","that","there","then"],
    ...       ["dc","ab","zz","d"],
    ...       ["ba","bc","d", "z"]], header=True)
    this that there then
    ba   bc   d     z
    dc   ab   zz    d
    """

    maxs = [1 for x in columns[0]]
    fmts = ['{}' for x in columns[0]]
    rng = range(0, len(columns[0]))
    for row in columns:
        for rnx in rng:
            if not isinstance(row[rnx], str):
                row[rnx] = str(row[rnx])
            len_cell = len(row[rnx])
            if len_cell > maxs[rnx]:
                maxs[rnx] = len_cell

    for rnx in rng:
        fmt = "{:" + str(maxs[rnx]) + "}"
        if rnx != 0:
            fmt = " " + fmt
        fmts[rnx] = fmt

    def sort_first(rowx): # pylint: disable=missing-docstring
        return rowx[0]
    if not sort:
        sort = sort_first

    headers = list()
    if header:
        headers = columns[0]
        columns = columns[1:]

    flattened = "".join(fmts) + "\n"

    def output_mixed(rowx, stderr): #pylint: disable=missing-docstring
        for rnx in rng:
            stream = sys.stdout
            if rnx in stderr or headers and headers[rnx] in stderr:
                stream = sys.stderr
            stream.write(fmts[rnx].format(rowx[rnx]))
            stream.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()

    def output_stdout(rowx, stderr): #pylint: disable=missing-docstring,unused-argument
        sys.stdout.write(flattened.format(*rowx))

    if stderr:
        output = output_mixed
    else:
        output = output_stdout

    if headers:
        output_mixed(headers, rng) # all header goes to stderr

    for row in sorted(columns, key=sort):
        output(row, stderr)
