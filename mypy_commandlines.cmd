dmypy run -- -p hisim --check-untyped-defs --no-color-output
dmypy run -- examples/ --check-untyped-defs --no-color-output
dmypy run -- tests/ --check-untyped-defs --no-color-output

conda activate nemenv
cd \work\hisim_github\HiSim\examples
python ..\hisim\hisim_main.py examples.py first_example
python ..\hisim\hisim_main.py examples.py second_example