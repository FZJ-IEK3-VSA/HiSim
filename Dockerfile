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

# Install hisim
RUN pip install -e . 

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

# Start hisim
ENTRYPOINT python3 hisim/hisim_main.py basic_household basic_household_explicit
