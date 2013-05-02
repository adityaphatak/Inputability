#!/usr/bin/python3
PYTHON_EXECUTABLE = "python3"  # don't touch this, it's modified by debian rules

import os

def run():
    
    os.system("gnome-session-quit --power-off --no-prompt")

