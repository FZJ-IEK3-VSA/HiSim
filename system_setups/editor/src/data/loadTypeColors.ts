const COLORS: Record<string, string> = {
  Electricity: '#facc15',
  ElectricityInWatt: '#facc15',
  Heat: '#f97316',
  Heating: '#f97316',
  Thermal: '#f97316',
  WarmWater: '#38bdf8',
  Gas: '#a78bfa',
  NaturalGas: '#a78bfa',
  Hydrogen: '#34d399',
  Temperature: '#94a3b8',
  Irradiance: '#fde68a',
  Occupancy: '#86efac',
  Speed: '#6ee7b7',
  Percentage: '#c4b5fd',
  Volume: '#7dd3fc',
  Mass: '#fca5a5',
  Pressure: '#fdba74',
  OnOff: '#d1d5db',
  MassFlowRate: '#bfdbfe',
  HeatingLoad: '#fb923c',
  CoolingLoad: '#67e8f9',
  Any: '#9ca3af',
}

export function getLoadTypeColor(loadType: string): string {
  return COLORS[loadType] ?? '#9ca3af'
}
