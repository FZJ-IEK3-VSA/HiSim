FROM python

WORKDIR /app

# Install utsp-client
RUN pip install git+https://github.com/FZJ-IEK3-VSA/UTSP_Client.git

# Copy source code to image
COPY . building_sizer
RUN mv building_sizer/setup.py ./
# Install building sizer
RUN pip install .

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

ENTRYPOINT python3 building_sizer/building_sizer_algorithm.py
