import os

from hisim import hisim_main
import shutil

def test_basic_household():
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    hisim_main.main(path, func)
    print(os.getcwd())
    #if os.path.isdir('results'):
     #   shutil.rmtree('results')

def test_basic_household_boiler():
    path = "../examples/basic_household_boiler.py"
    func = "basic_household_boiler_explicit"
    hisim_main.main(path, func)
    shutil.rmtree('results')


def test_basic_household_districtheating():
    path = "../examples/basic_household_Districtheating.py"
    func = "basic_household_Districtheating_explicit"
    hisim_main.main(path, func)
    shutil.rmtree('results')


def test_basic_household_oilheater():
    path = "../examples/basic_household_Oilheater.py"
    func = "basic_household_Oilheater_explicit"
    hisim_main.main(path, func)
    shutil.rmtree('results')


def test_first_example():
    path = "../examples/examples.py"
    func = "first_examples"
    hisim_main.main(path, func)
    shutil.rmtree('results')
