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

# Bake the commit the image was built from into the source tree, so that every document the
# image produces can name the code that produced it. A container is not a git checkout -- there
# is no .git here -- so `git rev-parse` inside the image answers nothing and the mapping report's
# `translator.commit` used to be null on every calculation the image ran (todo H6).
# `hisim.renovisor.report.HiSimCommit` reads this file first, then the HISIM_COMMIT environment
# variable, and only then git. Build with `--build-arg HISIM_COMMIT=$(git rev-parse --short HEAD)`;
# an image built without it writes an empty file, which is read as "no commit" exactly as before.
ARG HISIM_COMMIT=""
RUN printf '%s' "$HISIM_COMMIT" > hisim/COMMIT

# Copy the system_setups folder
COPY system_setups system_setups 

# Set an environment variable flag so HiSim can check whether it runs in a container or not
ENV HISIM_IN_DOCKER_CONTAINER true

# Create a folder for the input files
RUN mkdir /input

ENTRYPOINT python3 hisim/system_setup_starter.py /input/request.json /results
