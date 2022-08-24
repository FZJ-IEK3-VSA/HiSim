FROM python

WORKDIR /app

# Install utsp-client
COPY UTSP_client-0.1.0-py3-none-any.whl UTSP_client-0.1.0-py3-none-any.whl
RUN pip install  UTSP_client-0.1.0-py3-none-any.whl

# Copy source code to image
COPY building_sizer.py building_sizer.py

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

ENTRYPOINT python3 building_sizer.py
