FROM python:3.11-slim

WORKDIR /app

# Copy relevant files
COPY setup.py setup.py
# These files are needed by setup.py
COPY requirements.txt requirements.txt
COPY README.md README.md
# This file contains some internal parameters
COPY .env .env

# Install hisim
RUN pip install -e .

# Copy source code to image
COPY hisim hisim

# Copy the system_setups folder
COPY system_setups system_setups 

# Set an environment variable flag so HiSim can check whether it runs in a container or not
ENV HISIM_IN_DOCKER_CONTAINER true

# Create a folder for the input files
RUN mkdir /input

ENTRYPOINT python3 hisim/system_setup_starter.py /input/request.json /results
