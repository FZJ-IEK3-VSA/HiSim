FROM python:3.9-slim

WORKDIR /app

# Install HDF5, which is required to build h5py during the installation of the requirements
RUN apt-get update
RUN apt-get -y upgrade
RUN apt-get install libhdf5-serial-dev -y

# Copy relevant files
COPY setup.py setup.py
# These files are needed by setup.py
COPY requirements.txt requirements.txt
COPY README.md README.md

# Install hisim
RUN pip install -e .

# Copy source code to image
COPY hisim hisim

# Copy the examples folder containing the modular_household file
COPY examples examples 

# Set an environment variable flag so HiSim can check whether it runs in a container or not
ENV HISIM_IN_DOCKER_CONTAINER true

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

# Use the examples directory as working directory (required by HiSim)
WORKDIR /app/examples

# Temporary solution for the custom json interface for the WHY toolkit: always uses modular_household_explicit in modular_household.py
ENTRYPOINT mv /input/request.json modular_example_config.json && python3 ../hisim/hisim_main.py modular_example modular_household_explicit && cd results/modular_household_explicit* && mv * /results
