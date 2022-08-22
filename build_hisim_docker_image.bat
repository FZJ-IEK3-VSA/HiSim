@REM Simple script to build a hisim image and test it
set /p hisimVersion="Enter HiSim version: "

docker build -t hisim:%hisimVersion% .
if %errorlevel% neq 0 exit /b %errorlevel%

@REM Save the image to a tar file
docker save -o hisim-%hisimVersion%.tar hisim:%hisimVersion%

set /p testImage="Test the newly created image (Y/[N])?"
if /I "%testImage%" neq "y" exit /b 0

@REM Test the newly built image
@REM Create a container and save its ID
for /f %%i in ('docker create hisim:%hisimVersion%') do set "ID=%%i"

@REM Create an input file for the container
echo {"predictive":false,"prediction_horizon":86400,"pv_included":false,"smart_devices_included":true,"boiler_included":"electricity","heatpump_included":false,"battery_included":false,"chp_included":false} > examples\_docker_test_system_config.json

docker cp examples\_docker_test_system_config.json %ID%:/input/request.json

del examples\_docker_test_system_config.json

docker start -a %ID%
if %errorlevel% neq 0 exit /b %errorlevel%

echo "Copying results from container to examples\results\docker_test. Please specify whether you want to clear this folder first."
rmdir /s examples\results\docker_test\
docker cp %ID%:/results/ ./examples/results/docker_test/
