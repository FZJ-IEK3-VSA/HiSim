﻿By default, pytest go through all the files named the word "test" as a preffix or a suffix:
There are two platforms to perform tests: in the terminal or with your IDE.

Assume the test is to be performed in the terminal. If your test file is called test_component.py,
 which should be located at HiSim/tests, you should:

* Change your directory path to HiSim/hisim
* Run in your terminal "python -m pytest ../tests/test_component.py"

To perform with your IDE of your tast:
* Add HiSim/hisim to PYTHONPATH
* Run test_component.py using your IDE
