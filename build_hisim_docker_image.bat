REM Simple script to build a hisim image and test it
docker build -t hisim:latest .

REM create container and save its ID
for /f %%i in ('docker create hisim:latest') do set "ID=%%i"

docker cp examples\basic_household.py %ID%:/app/basic_household.py

docker start -a %ID%

docker cp %ID%:/app/results/ ./_docker_results/
