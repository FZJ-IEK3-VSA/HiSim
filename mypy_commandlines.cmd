dmypy run -- -p hisim --check-untyped-defs --no-color-output
dmypy run -- system_setups/ --check-untyped-defs --no-color-output
dmypy run -- tests/ --check-untyped-defs --no-color-output

conda activate nemenv
cd \work\hisim_github\HiSim\system_setups
python ..\hisim\hisim_main.py basic_household.py
python ..\hisim\hisim_main.py basic_household.py
python ..\hisim\hisim_main.py basic_household_boiler.py
python ..\hisim\hisim_main.py basic_household_Districtheating.py
python ..\hisim\hisim_main.py basic_household_Oilheater.py
python ..\hisim\hisim_main.py simple_system_setup_one.py
python ..\hisim\hisim_main.py simple_system_setup_two.py
