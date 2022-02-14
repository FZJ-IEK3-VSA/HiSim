dmypy run -- -p hisim --check-untyped-defs --no-color-output
dmypy run -- examples/ --check-untyped-defs --no-color-output
dmypy run -- tests/ --check-untyped-defs --no-color-output

conda activate nemenv
cd \work\hisim_github\HiSim\examples
python ..\hisim\hisim_main.py basic_household.py basic_household_explicit 
python ..\hisim\hisim_main.py basic_household.py basic_household_with_default_connections
python ..\hisim\hisim_main.py basic_household_boiler.py basic_household_boiler_explicit
python ..\hisim\hisim_main.py basic_household_Districtheating.py basic_household_Districtheating_explicit
python ..\hisim\hisim_main.py basic_household_Oilheater.py basic_household_Oilheater_explicit
python ..\hisim\hisim_main.py examples.py first_example
python ..\hisim\hisim_main.py examples.py second_example