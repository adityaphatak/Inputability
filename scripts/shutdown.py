#!/usr/bin/python3
PYTHON_EXECUTABLE = "python3"  # don't touch this, it's modified by debian rules

import os

def run():
    command = "/usr/bin/sudo /sbin/shutdown now"
    import subprocess
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)


