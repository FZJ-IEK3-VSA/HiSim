FROM python

WORKDIR /app

# Install utsp-client
RUN pip install git+https://github.com/FZJ-IEK3-VSA/UTSP_Client.git

# Copy source code to image
COPY building_sizer_algorithm.py building_sizer_algorithm.py
COPY evolutionary_algorithm.py evolutionary_algorithm.py
COPY system_config.py system_config.py
COPY heating_system_enums.py heating_system_enums.py

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

ENTRYPOINT python3 building_sizer.py
