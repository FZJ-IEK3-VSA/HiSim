.. _tutorial:

Tutorial
================================================

Run Simple Examples
-----------------------
Run the python interpreter in the `hisim/examples` directory with the following command:

```python
python ../hisim/hisim.py examples first_example
```

This command executes `hisim.py` on the setup function `first_example` implemented in the file `examples.py` that is stored in `hisim/examples`. The same file contains another setup function that can be used: `second_example`. The results can be visualized under directory `results` created under the same directory where the script with the setup function is located.

Run Basic Household Example
-----------------------
The directory `hisim\examples` also contains a basic household configuration in the script `basic_household.py`. The first setup function (`basic_household_explicit`) can be executed with the following command:
