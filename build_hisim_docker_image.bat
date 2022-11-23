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

docker cp examples\system_config.json %ID%:/input/request.json

docker start -a %ID%
if %errorlevel% neq 0 exit /b %errorlevel%

echo "Copying results from container to examples\results\docker_test. Please specify whether you want to clear this folder first."
rmdir /s examples\results\docker_test\
docker cp %ID%:/results/ ./examples/results/docker_test/
