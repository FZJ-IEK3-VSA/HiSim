#Test Function
import csv

Wert1 = 1
Wert2 = 2

import csv

OladelaDamilola = 50000.512312312423


for ii in range(10):
    pathnew = 'C:\\Users\Standard\Privat\profilesABC',str(ii) ,'.csv'
    with open(pathnew, 'w', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        row_list = [
            "name  ", "age ", "country", '\n',
            "Oladele Damilola =  ", str(OladelaDamilola) ," Nigeria ", '\n',
            "Alina Hricko =  ", "23 ", "Ukraine", '\n',
            "Isabel Walter =  ", "50 ", "United Kingdom", '\n',
        ]
        
        writer.writerow(row_list)

