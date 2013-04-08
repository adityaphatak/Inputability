#!/usr/bin/python
#
#  Authored by Chris Jones <chris.e.jones@gmail.com>
#  Modified by Francesco Fumanti <francesco.fumanti@gmx.net>
#  Modified by marmuta <marmvta@googlemail.com>
#
#  Copyright (C) 2006, 2007, 2008 Chris Jones
#  Copyright (C) 2010 Francesco Fumanti
#  Copyright (C) 2011 marmuta
#  Copyright (C) 2012 Francesco Fumanti, marmuta
#
# --------------------------------------------------------------------
#
# This file is part of the virtkey library.
#
# virtkey is free software: you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public License
# as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# virtkey is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with virtkey.  If not, see
# <http://www.gnu.org/licenses/>.
#
# --------------------------------------------------------------------
#
# virtkey is an extension for python and python3 to emulate keypresses
# and to get the keyboard geometry from the xserver.
#
# It uses ideas from Fontconfig, libvirtkeys.c, keysym2ucs.c and
# dasher.
#
from distutils.core import setup
from distutils.extension import Extension

try:
    # try python 3
    from subprocess import getoutput
except:
    # python 2 fallback
    from commands import getoutput

# From http://code.activestate.com/recipes/502261-python-distutils-pkg-config/
def pkgconfig(*packages, **kw):
    flag_map = {'-I': 'include_dirs', '-L': 'library_dirs', '-l': 'libraries'}
    for token in getoutput("pkg-config --libs --cflags %s" % ' '.join(packages)).split():
        if token[:2] in flag_map:
            kw.setdefault(flag_map.get(token[:2]), []).append(token[2:])
        else: # throw others to extra_link_args
            kw.setdefault('extra_link_args', []).append(token)
    for k, v in kw.items(): # remove duplicated
        kw[k] = list(set(v))
    return kw

setup(
    name="virtkey",
    version="0.63.0",
    author = 'Chris Jones',
    author_email = 'chris.e.jones@gmail.com',
    maintainer = 'Ubuntu Core Developers',
    maintainer_email = 'ubuntu-devel-discuss@lists.ubuntu.com',
    url = 'https://launchpad.net/virtkey/',
    license = 'LGPL',
    description = 'Extension for python and python3 to emulate keypresses and to get the layout information from the X server.',
    ext_modules=[
        Extension("virtkey", ["src/python-virtkey.c","src/ucs2keysym.c"],
            **pkgconfig('glib-2.0', 'gdk-2.0', 'x11', 'xtst', 'xkbfile'))]
)
