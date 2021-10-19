import os



if __name__ == '__main__':
    list_Battery = [1,5, 8,40,100]
    list_PV = [4, 8, 12, 16, 20]

    for x in range(1, len(list_Battery) + 1):
        global capacity_variable
        capacity_variable= list_Battery[x - 1]


        command_line = "python hisim.py basic_household_Battery basic_household"
        os.system(command_line)


