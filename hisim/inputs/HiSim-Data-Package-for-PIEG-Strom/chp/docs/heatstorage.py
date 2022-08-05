class HeatStorage():
    def __init__(self,
                 Volume= 1000, #in l
                 ambient_temperature = 15,
                 c_w=4180):
        self.V_sp=Volume
        self.c_w=c_w
        self.T_amb=ambient_temperature
    def calculate_new_storage_temperature(self, T_sp, dt, P_hp, P_ld):
        P_loss=0.0038 *self.V_sp + 0.85
        T_sp=T_sp + (1/(self.V_sp*self.c_w))*(P_hp - P_ld - P_loss*(T_sp-self.T_amb))*dt
        return T_sp