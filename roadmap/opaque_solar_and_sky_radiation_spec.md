# Spec: the building absorbs solar radiation on its walls and roof and radiates heat to the sky

Status: draft for the owner's review (2026-11-12), revised the same day after the default values were checked
against ISO 52016-1; nothing is built. The decisions in §2 are proposals until the owner confirms them. Builds on:
nothing for the TABULA default (D3); the optional surface list (D4) builds on the configured windows of branch
`verification/step2-building-overrides`.

## 1. Problem

HiSim's building is the simple hourly method (5R1C) of ISO 13790. The standard's solar heat flow has three parts,
and HiSim computes only the first:

| Part | ISO 13790 | HiSim today |
|---|---|---|
| Solar radiation through the windows | 11.3.3 | yes |
| Solar radiation absorbed by the opaque elements (walls, roof) | 11.3.4, Eq. 46 | missing |
| Thermal radiation from the envelope to the sky | 11.3.5, Eq. 47; data in 11.4.6 | missing |

In the code, `solar_gains.produce_solar_gains` sums over the windows only, and the opaque elements exchange heat
with the outdoor air temperature alone (`Building.calc_total_thermal_mass_heat_flux_in_watt`).

Size of the two missing terms, with the default values of D2, per m² of exposed surface with U = 1 W/(m²K):

- sky radiation: a constant loss of 0.94 W for a wall and 1.89 W for a roof, day and night;
- absorbed solar radiation: 0.025 W per W/m² of irradiance on the surface, so 12.4 W at 500 W/m².

Both scale with the U-value, so they matter most for poorly insulated buildings.

## 2. Decisions

**D1 — Both terms are added as the standards state them** (proposed, 2026-11-12). They join the window gains in
the solar heat flow (ISO 13790 Eq. 44, 46, 47) and are split onto the model's nodes by the same rule (C.1 to C.3).
ISO 52016-1, which supersedes ISO 13790, keeps the same two formulas (Eq. 124, 125). Consequence: the 5R1C
network, its conductances and the window optics stay as they are.

**D2 — Fixed default values, each with a printed source** (proposed, 2026-11-12). The values are those of
ISO 52016-1:2017; its Annex B tables are informative defaults.

| Quantity | Value | Source in ISO 52016-1 |
|---|---|---|
| Solar absorptance α of opaque surfaces | 0.6, configurable | Table B.15, default category "intermediate colour" |
| Sky form factor F_r | 1 for a roof, 0.5 for a vertical surface | Table B.18 |
| Air-to-sky temperature difference Δθ_er | 11 K, configurable | Table B.19, intermediate zones |
| External radiative coefficient h_r | 4.14 W/(m²K) | Table 25 |
| External convective coefficient h_ce | 20 W/(m²K) | Table 25 |
| External surface resistance R_se | 1 / (h_ce + h_r) = 0.0414 m²K/W | Eq. 124, 125 |

ISO 13790 11.4.6 states the same F_r and Δθ_er. It gives no value for α and takes h_r from an emissivity that
it does not state either; that is why the values of ISO 52016-1 are used. ISO 52016-1 takes the two coefficients
of Table 25 from ISO 13789, which was not consulted.

Consequence: the sky term is a constant for a given building, and the model needs no emissivity. Δθ_er is
configurable because the standard states it per climate (9 K sub-polar, 13 K tropics). Setting α = 0 and
Δθ_er = 0 switches both terms off and reproduces today's results.

**D3 — A TABULA building spreads its exposed wall area in equal quarters over south, east, north and west**
(proposed, 2026-11-12). TABULA states wall areas without an orientation; only the windows have one. Equal
quarters fit a detached house and need no further input. Consequence: a terraced house, which has two free
facades, is approximated; this is stated in the building's documentation.

**D4 — A configured building may state its opaque surfaces itself** (proposed, 2026-11-12): an optional list of
surfaces, each with area, U-value, azimuth and tilt, in the same way a building states configured windows. When
the list is given, it replaces the rules of D3 and D5. Reason: a verification case defines the orientation of
every wall, and the quarter rule would be an avoidable deviation there. Consequence: built after the configured
windows have reached `main`.

**D5 — Every roof is treated as horizontal** (proposed, 2026-11-12). TABULA states a roof pitch and orientation
for about 420 of its 3281 rows only, and neither standard gives a sky form factor for a pitched roof. This is a
known simplification, stated in the building's documentation.

**D6 — Windows and doors radiate to the sky as well; doors absorb no solar radiation** (proposed, 2026-11-12).
Both standards state the sky term per building element (ISO 13790 Eq. 44; ISO 52016-1 Eq. 123 for windows).
ISO 52016-1 treats doors together with the windows and gives them no solar absorption (6.5.7.4).

**D7 — Opaque surfaces are shaded by the same factor as the windows** (proposed, 2026-11-12): TABULA's
`F_sh_vert`, applied to the total irradiance, as the windows apply it today. ISO 52016-1 Eq. 124 carries a
shading factor for opaque elements too. Consequence: no new shading input.

## 3. Design

### 3.1 The two terms

```
absorbed solar radiation   Φ_opaque(t) = Σ_k  α · R_se · U_k · A_k · F_sh · I_k(t)        [W]
radiation to the sky       Φ_sky       = Σ_j  F_r,j · R_se · U_j · A_j · h_r · Δθ_er      [W]
solar heat flow            Φ_sol(t)    = Φ_windows(t) + Φ_opaque(t) − Φ_sky              [W]
```

k runs over the exposed walls and roof, j over the same surfaces plus the windows and doors. I_k is the total
irradiance on the surface's plane.

Φ_sol replaces the window gain wherever the model uses it today: in the node split, in the theoretical heating
and cooling demand and in the share of the gains lost through the windows. At night Φ_sol is negative.

### 3.2 Which surfaces count

A surface counts when it borders the outside air. The floor never counts.

- **Walls:** the TABULA wall sub-areas whose transmission adjustment factor is 1, each with its own U-value.
  A wall against a cellar, the soil or a neighbour does not count. The walls stand at a tilt of 90° on the four
  planes of D3.
- **Roof:** the share `A_Estim_Roof / (A_Estim_Roof + A_Estim_UpperCeiling)` of the building's roof area, with
  the building's roof U-value, on the horizontal plane (D5).
- **Windows and doors:** the areas and U-values the model already uses; a window's form factor follows its
  plane, a door is vertical.

The roof share needs TABULA's two area estimates because they alone say what closes the heated zone at the top.
Of the 2662 rows with a roof area, 1939 state a roof only (share 1), 643 an upper ceiling under an unheated
attic only (share 0) and 80 both. The transmission adjustment factor is 1 in almost all of these rows, and
`Code_RoofType` names the upper ceiling in only 267 of the 643. In the 80 rows with both, the share is an
approximation. Every row with a roof area states at least one estimate; a row without one would count its roof
in full.

### 3.3 Where it is computed

- **Absorbed solar radiation** is produced together with the window gains in `solar_gains.py`, as a second column
  of the cached series. It uses the same plane irradiance as the windows (isotropic sky, ground reflectance 0.2).
  The four wall planes and the horizontal plane are the planes of the TABULA windows, so the irradiance of each
  plane is needed once.
- **Radiation to the sky** is one number, computed when the building is built.
- The timestep loop gains two additions and no new calculation. Run time does not change in a measurable way.

The dark-timestep shortcut of the producer applies to the solar part only; the sky term acts in every timestep.

### 3.4 Configuration and outputs

- New configuration fields of `BuildingConfig`: `opaque_solar_absorptance` (0.6),
  `sky_temperature_difference_in_kelvin` (11) and, with D4, the optional list of opaque surfaces.
- New outputs, each with an energy port so that the heated zone's balance still closes:
  `SolarGainThroughOpaqueElementsInWatt` (solar, like the window gain) and `SkyRadiationLossInWatt` (a loss of
  room heat). `SolarGainThroughWindows` keeps its name and its meaning.

## 4. What changes and what does not

- **Results:** every building moves. The sky term raises the heating demand throughout the year; the absorbed
  radiation lowers it in sunny hours and raises summer temperatures and the cooling demand. How far the KPIs move
  is measured when the change is built (§6).
- **Public interfaces:** two new outputs and two new configuration fields (three with D4). Nothing is renamed.
  RenoVisor gets the heads-up for a result change (`system_docs/results_and_interfaces.md` §5).
- **Goldens and twins:** all pairs are re-blessed through `golden-update.yml`; the twins are re-recorded because
  the configuration gains fields. The cache entries of the solar gains are recomputed once.
- **Verification suite** (branch stack `verification/…`, not on `main`): its gap analysis records both terms as
  missing (finding F4, register entry `opaque_solar_absorption_and_sky_radiation`). Once this is built, the entry
  goes, and the cases state their walls through D4 with their air-to-air U-values.
- **Unchanged:** the window optics, the conductances, the thermal mass and the weather.

## 5. Verification

- Hand-checked values of the two pure functions. An unshaded wall of 10 m² with U = 1 W/(m²K) under 500 W/m²
  absorbs 0.6 · 1 · 10 · 500 / 24.14 = 124.3 W and radiates 0.5 · 1 · 10 · 4.14 · 11 / 24.14 = 9.43 W to the
  sky; the same surface as a roof radiates 18.86 W.
- With α = 0 and Δθ_er = 0 a run reproduces the results from before the change.
- In a dark timestep the absorbed radiation is 0 and the sky term is unchanged.
- The quarter rule: the four wall areas sum to the exposed wall area.
- The roof share is 1, 0 and between the two for one TABULA row of each kind (§3.2).
- The heated zone's energy balance closes with the two new ports.
- The golden gate, with the KPI deltas listed in the pull request.

## 6. Measured results

None yet.

## 7. Open questions

None.

## 8. Beads

The epic and its beads are created once the draft is agreed.
