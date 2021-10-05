import os
import shutil
import sys
import inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

import globals

directorypath = globals.HISIMPATH["results"]
filelist = [f for f in os.listdir(directorypath)]
for f in filelist:
    if os.path.isdir(os.path.join(directorypath, f)):
        shutil.rmtree(os.path.join(directorypath, f))
