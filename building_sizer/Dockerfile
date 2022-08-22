FROM python

WORKDIR /app

# Copy source code to image
COPY building_sizer.py building_sizer.py

# Create a folder for the input files
RUN mkdir /input
# Create a folder for the result files
RUN mkdir /results

ENTRYPOINT python3 building_sizer.py
