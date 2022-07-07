REM Simple script to build a hisim image and test it
docker build -t hisim:latest .
if %errorlevel% neq 0 exit /b %errorlevel%

REM create container and save its ID
for /f %%i in ('docker create hisim:latest') do set "ID=%%i"

docker cp examples\system_config.json %ID%:/input/request.json

docker start -a %ID%
if %errorlevel% neq 0 exit /b %errorlevel%

docker cp %ID%:/app/results/ ./examples/results/docker_test/

docker save -o hisim-latest.tar hisim:latest
