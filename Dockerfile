#FROM continuumio/miniconda3:4.10.3-alpine
FROM python

WORKDIR /app

# install dependencies
RUN apt-get update
RUN apt-get upgrade -y
RUN apt-get install -y python-tk
RUN apt-get install -y xdg-utils

# Copy relevant files
COPY setup.py setup.py
# These files are needed by setup.py
COPY requirements.txt requirements.txt
COPY README.rst README.rst
COPY HISTORY.rst HISTORY.rst
# Copy source code to image
COPY hisim hisim

# Copy modular_household.py
COPY examples/modular_household.py modular_household.py

# Install hisim
RUN pip install -e . 

# Set an environment variable flag so HiSim can check whether it runs in a container or not
ENV HISIM_IN_DOCKER_CONTAINER true

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

# Temporary solution for the custom json interface for the WHY toolkit: always uses modular_household_explicit in modular_household.py
ENTRYPOINT mv /input/request.json system_config.json && python3 hisim/hisim_main.py modular_household modular_household_explicit && cd results/modular_household_explicit* && mv *.csv /results
