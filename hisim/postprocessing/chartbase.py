import os
import re



class Chart:
    """
    Parent class for plots to be exported.
    """
    months_abbrev_uppercase = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEZ']
    label_months_lowercase = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

    def __init__(self, output, data, type, units, directorypath, time_correction_factor, output2=None):
        self.output = output
        self.data = data
        self.type = type
        if hasattr(units, "value"):
            self.units = units.value
            self.ylabel = units.value
        else:
            self.units = units
            self.ylabel = units
        self.time_correction_factor = time_correction_factor

        self.title:str = ""
        matches = re.finditer('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$|#)', self.output)
        matches = [m.group(0) for m in matches] # type: ignore
        pass_sign = False
        property = ""
        object = ""
        for m in matches:
            if pass_sign:
                property = "{} {}".format(property,m)
            else:
                object = "{}{}".format(object,m)

            if m.find("#"):  # type: ignore
                pass_sign = True

            if len(self.title) == 0:
                self.title = str(m)
            else:
                self.title = "{} {}".format(self.title, m)
        self.directorypath = directorypath

        self.object_name = " "
        self.property = property
        if output2 is not None:
            self.output2 = output2
            self.filename = "{}_{}_{}double.png".format(self.type.lower(),
                                                        self.output.split(' # ', 2)[1],
                                                        self.output.split(' # ', 2)[0])
        else:
            self.filename = "{}_{}_{}.png".format(self.type.lower(),
                                                  self.output.split(' # ', 2)[1],
                                                  self.output.split(' # ', 2)[0])
        self.filepath = os.path.join(self.directorypath, self.filename)
