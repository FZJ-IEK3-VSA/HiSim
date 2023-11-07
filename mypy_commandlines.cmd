dmypy run -- -p hisim --check-untyped-defs --no-color-output
dmypy run -- system_setups/ --check-untyped-defs --no-color-output
dmypy run -- tests/ --check-untyped-defs --no-color-output

conda activate nemenv
cd \work\hisim_github\HiSim\system_setups
python ..\hisim\hisim_main.py basic_household.py setup_function
python ..\hisim\hisim_main.py basic_household.py setup_function
python ..\hisim\hisim_main.py basic_household_boiler.py setup_function
python ..\hisim\hisim_main.py basic_household_Districtheating.py setup_function
python ..\hisim\hisim_main.py basic_household_Oilheater.py setup_function
python ..\hisim\hisim_main.py simple_system_setup.py setup_function_first
python ..\hisim\hisim_main.py simple_system_setup.py setup_function_second