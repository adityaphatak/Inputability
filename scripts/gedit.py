#!/usr/bin/python3
PYTHON_EXECUTABLE = "python3"  # don't touch this, it's modified by debian rules

import subprocess

def run():
    subprocess.Popen("gedit")  


