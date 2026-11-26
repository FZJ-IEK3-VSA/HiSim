# Spec: the building absorbs solar radiation on its walls and roof and radiates heat to the sky

Status: draft, rewritten on 2026-11-26 clause by clause from ISO/FDIS 13790:2007; it replaces the draft of 2026-11-12,
whose D1 to D7 were proposals as well. Nothing is built. D5 and D11 are decided (owner, 2026-11-27), as is the build
order: after the first pull request of the building description spec (Q4). Every other decision below is a proposal
with a recommendation until the owner confirms it. Owner: Nicolai Gölz. Epic: none yet. Builds on: nothing on `main`
for the TABULA rules; `roadmap/building_description_spec.md` (branch `building/explicit-description`) for D4 and the
build order.

Clause, equation and page numbers are those of the printed FDIS text. ISO 52016-1:2017, which supersedes ISO 13790,
is cited where it prints a value ISO 13790 leaves open. Neither text is quoted.

## 1. Problem

HiSim's building is the simple hourly method of ISO 13790 (7.2.2, Annex C), the 5R1C network. The standard's solar
heat flow Φ_sol has, per building element k, two parts (11.3.2, Eq. 44): the solar radiation the element collects
and the thermal radiation it loses to the sky. For the windows HiSim computes the first part; for the opaque elements
it computes neither:

| Part of Φ_sol | ISO 13790 | HiSim today |
|---|---|---|
| Solar radiation through the windows, A_sol of Eq. 45 | 11.3.3 | `solar_gains.produce_solar_gains`: one series per run from the TABULA windows per direction |
| Solar radiation absorbed by walls and roof, A_sol of Eq. 46 | 11.3.4 | missing |
| Thermal radiation to the sky, Φ_r of Eq. 47, for every element | 11.3.5, data in 11.4.6 | missing |

The opaque elements exchange heat with the outdoor air temperature alone
(`Building.calc_total_thermal_mass_heat_flux_in_watt`: H_tr,em times θ_e). The verification suite records this as a
model deviation (`roadmap/hisim_verification_agent_spec.md` §3.2.9, branch `verification_spec`).

Size, for the default house `DE.N.SFH.05.Gen.ReEx.001.002` (121.2 m², scaling factor 1) under the Aachen test
reference year 1995–2012, with the values of D2 (§4.7 shows the arithmetic):

| Term | Power | Per year |
|---|---|---|
| Radiation to the sky, constant, day and night | 187 W with the roof at 45° (D5), 207 W with it horizontal | 1.64 to 1.82 MWh |
| Absorbed solar radiation, in sunny hours, peak about 1 kW on the roof | | 1.55 to 1.73 MWh |

Over the year the two nearly cancel, as 11.3.4 says they can, but not in time: the sky loss is there in every winter
night, the absorbed radiation mostly at noon in summer. The sky term alone equals the loss of the whole envelope
(206 W/K including thermal bridges) for 0.9 K, so it acts like an outdoor air that is 0.9 K colder all year. Both
terms grow with the U-value, so they matter most for unrefurbished buildings and least for passive houses.

## 2. Decisions

All decisions are proposals of 2026-11-26 with a recommendation; the owner confirms, amends or rejects each one. The
table names the clause each decision interprets and the open question of §8 it depends on.

| | Decides | Clause | Depends on |
|---|---|---|---|
| D1 | both terms, in the form of the standard | 11.2, 11.3.2, 11.3.4, 11.3.5, C.2 | – |
| D2 | the values of R_se, h_r (ε), α, Δθ_er, F_r | 11.3.4, 11.3.5, 11.4.6, J.1.4 | Q2 |
| D3 | orientation of TABULA walls: four equal quarters | 11.3.2 (I_sol,k needs an orientation) | – |
| D4 | the surface list is a quantity of the building description | – | Q4, decided |
| D5 | tilt of a TABULA roof: 45° pitched, 0° flat; F_r by the cosine formula | 11.4.6 | Q3, decided |
| D6 | windows and doors radiate to the sky; doors absorb nothing | 11.3.2, 7.2.2.1 | – |
| D7 | shading factors of opaque surfaces | 11.4.4 | – |
| D8 | elements with b < 1 and elements of unconditioned spaces are excluded | 11.2 (Eq. 42), 11.4.1 | – |
| D9 | U_c without the thermal-bridging surcharge | 11.3.4, 8.3.1, 8.3.2.7 | – |
| D10 | h_r is a constant, not Eq. 52 per timestep | 11.4.6 | – |
| D11 | Δθ_er is a constant, not the weather's sky temperature | 11.4.6 | Q1, decided |
| D12 | which TABULA roof areas face the sky | TABULA data | – |
| D13 | where each quantity is computed | – | – |
| D14 | configuration, outputs, switch-off | – | – |

**D1 — Both terms are added in the form the standard states them** (proposed, 2026-11-26). Eq. 44 gives the heat
flow of every element k as F_sh,ob,k · A_sol,k · I_sol,k − F_r,k · Φ_r,k; A_sol of an opaque element is Eq. 46 and
Φ_r is Eq. 47; the sum over the elements is Φ_sol of Eq. 42, which C.2 and C.3 split onto the surface node and the
mass node. ISO 52016-1 keeps the same form (hourly Eq. 70, monthly Eq. 123 to 125). Consequence: the 5R1C network,
its conductances, the thermal mass and the window optics stay as they are; the sky term enters as a negative solar
gain on the inner nodes, which 11.3.2 NOTE 2 calls a convenience: the loss happens at the outer surface, which the
network does not have. Not taken: the alternative of 11.3.5, an external operative temperature on H_tr,em, because
it would change the network.

**D2 — The parameter values are those ISO 13790 prints, with one choice of ours, ε = 0.9** (proposed, 2026-11-26;
revises the draft of 2026-11-12, which took the ISO 52016-1 values).

| Symbol | Value | Where it stands in ISO 13790 | Source of the number |
|---|---|---|---|
| h_r | 5 ε = 4.5 W/(m²K) | normative text, 11.4.6: the first approximation of Eq. 52 at a mean temperature of 10 °C | the standard, given ε |
| Δθ_er | 11 K, configurable | normative text, 11.4.6: intermediate zones (9 K sub-polar, 13 K tropics); ISO 52016-1 Table B.19 the same | the standard |
| F_r | (1 + cos β) / 2 for a tilt β | normative text, 11.4.6: 1 for a horizontal roof, 0.5 for a vertical wall; no value for a tilted plane | the standard at 0° and 90°; the formula is the isotropic sky view factor that yields both values and the one the window optics use for the diffuse share; 0.854 at 45° is our value (D5) |
| R_se | 0.04 m²K/W | not in the normative text: 11.3.4 and 11.3.5 refer to ISO 6946 for R_se and U_c; 0.04 is printed in the informative worked example J.1.4 only | ISO 6946's conventional external surface resistance, not at hand; the TABULA U-values are ISO 6946 values and carry the same R_se |
| α | 0.6, configurable | not in the normative text: 11.3.4 refers to national sources; 0.6 is printed in the informative worked example J.1.4 only | the example; ISO 52016-1 Table B.29 makes 0.6 ("intermediate colour") the default of the three categories 0.3 / 0.6 / 0.9 |
| ε | 0.9 | nowhere: ISO 13790 leaves ε a symbol in Eq. 52 and in 5 ε | our choice: the emissivity of ordinary building surfaces; contained in ISO 52016-1's h_lr;e = 4.14 W/(m²K) (0.9 times the black-body coefficient near 0 °C); the exterior emittance of the ASHRAE 140 base cases [verify] |

So the normative text of ISO 13790 fixes h_r = 5 ε, Δθ_er and the two F_r values; R_se and α are printed in its
informative example only, and ε nowhere in it. J.1.4 confirms the reading of Eq. 46: its opaque facade has α 0.6,
R_se 0.04 and U 0.493 and a stated "g-value" of 0.012, which is their product. The alternative is the ISO 52016-1
set, R_se = 1/(20 + 4.14) = 0.0414 m²K/W and
h_r = 4.14 W/(m²K) from Table 25 of its verification cases, without an ε, chosen on 2026-11-12 because every number
had a printed source. The two sets differ by 3.5 % in the absorbed radiation and 5 % in the sky term (§4.7).
Recommendation: the ISO 13790 set, because the model is ISO 13790, the TABULA U-values rest on the same R_se, and the
one unprinted number, ε, is the one every source agrees on. Open as Q2.

**D3 — A TABULA building spreads its exposed wall area in four equal quarters over south, east, north and west**
(proposed, 2026-11-26; the owner agreed in the session of 2026-11-12). I_sol,k of Eq. 44 needs an orientation per
element, and TABULA states wall areas without one; the windows are the only oriented elements of a row, and their
four vertical planes are the planes the walls get. Consequence: no irradiance of a new plane is needed; a terraced
house with two free facades is approximated as if it had four, which the building's report states.

**D4 — The sky-facing surfaces are a quantity of the building description: the TABULA interpretation derives them,
a described building states them** (proposed, 2026-11-26; replaces the draft's optional surface list on
`BuildingConfig`). `roadmap/building_description_spec.md` (owner, 2026-11-26) states every quantity the physics needs
in final form, with TABULA as one source of it. The list of surfaces with area, U-value, tilt, azimuth and shading
factor is such a quantity: for a TABULA row stage 1 derives it by D3, D5, D7, D8 and D12; a described building, first
the verification cases, writes it directly, every wall at its own orientation. Consequence: the field joins
`BuildingDescription` as a mandatory tuple, empty allowed (no absorbed radiation and no sky term for opaque elements);
the sky term of the windows and the door follows from `windows`, `window` and `door` without a new field. The
build order is decided with Q4: after the first pull request of that spec.

**D5 — A TABULA pitched roof stands at 45° in four equal quarters, a flat roof is horizontal** (owner, 2026-11-27;
revises D5 of the draft, every roof horizontal). TABULA states the pitch through `Code_RoofType` (German rows: TR
pitched 415, FR flat 108, UC upper ceiling 39, unknown 24) and the roof area as the sloped surface: the default
house has 168.9 m² of roof over 115.8 m² of floor, close to the 1.41 of a 45° pitch. Where TABULA states an
inclination for the solar potential of a pitched roof it is 45° (36 rows; 18 further rows 15°). A sloped roof taken
as horizontal sees the whole sky (F_r 1 instead of 0.854) and receives the horizontal irradiance (1067 kWh/m²a in
Aachen instead of 933, the mean of the four 45° planes); both errors go the same way. The four quarters are D3's
rule, so the two unknown orientations of a gable roof average out as the walls' do. Consequence: four more planes for
the producer; F_r = 0.854 is a value neither standard prints (ISO 52016-1 Tables B.18 and B.30 stop at horizontal and
vertical) and is stated as ours. Q3 records the horizontal alternative.

**D6 — Windows and doors radiate to the sky as well; a door absorbs no solar radiation** (proposed, 2026-11-26).
Eq. 44 subtracts F_r,k · Φ_r,k for every element k, and Φ_r of 11.3.5 is written for a building envelope element,
not for opaque ones; 7.2.2.1 counts doors with the windows (the "window part"); ISO 52016-1 writes the sky term into
its window equation (Eq. 123) and treats doors with the windows (6.5.7.4). A window's solar part stays Eq. 45 as
computed today; a door has no g-value and no α in either standard. Consequence: each window radiates at its own
tilt (F_r 0.5 vertical, 1 horizontal) with the window U-value; the door is vertical with the door U-value.

**D7 — An opaque surface is shaded by TABULA's factor for its plane, the walls by `F_sh_vert` and the roof by
`F_sh_hor`, applied to the total irradiance** (proposed, 2026-11-26; refines D7 of the draft). 11.4.4 defines
F_sh,O for the surface concerned and lets a national body prescribe tabulated values; TABULA prescribes 0.6 for
vertical and 0.8 for horizontal surfaces in every German row, and the windows apply their factor to the total plane
irradiance today. The form factor F_r keeps the unshaded value of 11.4.6; neither standard reduces it for obstacles.
A described surface states its own factor. Consequence: no new input. Observation, not changed here: the TABULA
windows apply `F_sh_vert` to a horizontal window too (`Building.get_windows`), where `F_sh_hor` is the row's value;
48 German rows have such a window.

**D8 — An element whose adjustment factor b is below 1, and every element of an unconditioned space, is excluded
from both terms** (proposed, 2026-11-26). Eq. 42 sums Φ_sol,k over the elements of the conditioned zone and adds
(1 − b_tr,l) · Φ_sol,u,l for solar sources inside unconditioned spaces: an element between the zone and such a space
(b < 1) sees neither sun nor sky, and the sun that warms the space behind it is the second sum, which 11.4.1 NOTE
allows to be ignored (except sunspaces). The floor never counts (ground or cellar). In the German rows the rule
removes the second wall sub-area of 24 rows (b 0.5 or 0); every roof sub-area with an area has b = 1. Consequence:
the transmission keeps its own rule, the row's largest b for the whole element (`BuildingInformation.EnvelopeElement`),
so a wall sub-area can count in the transmission and not here; the two uses of the row differ there on purpose. A
configured wall or roof area, or a configured U-value, counts as fully exposed (b = 1), as the transmission treats it.

**D9 — U_c is the element's U-value without the thermal-bridging surcharge** (proposed, 2026-11-26). Eq. 46 and 47
take the transmittance of the opaque part in accordance with ISO 6946; thermal bridges enter ISO 13790 through H_tr
alone (8.3.1 Eq. 18, 8.3.2.7), as HiSim adds them (`BuildingInformation._thermal_bridging_conductance_in_watt_per_kelvin`,
ΔU times the envelope area). Consequence: the terms use the element values of `element_values()`; for the default
house the surcharge is 46 W/K beside 140 W/K of exposed element conductance, so spreading it over the elements would
raise both terms by a third, without a basis in the standard.

**D10 — h_r is the constant 5 ε of 11.4.6, not Eq. 52 evaluated per timestep** (proposed, 2026-11-26). Eq. 52 needs
the mean of the outer surface temperature and the sky temperature; the 5R1C network has no outer surface temperature
and HiSim's weather no sky temperature (D11); 11.4.6 states the constant as the first approximation, and its NOTE 2
asks even detailed methods to keep the surface coefficients at the standard values. Consequence: the sky term of a
building is one number (D13); ε enters only through h_r and is not a configuration field.

**D11 — Δθ_er is the constant of 11.4.6, configurable per climate; the weather's longwave radiation is not read**
(owner, 2026-11-27). 11.4.6 states the constant for the case that the sky temperature is not available from the
climatic data. It is available in two of HiSim's sources: the DWD test reference years carry the atmospheric longwave
irradiance on the horizontal plane (column A of the `.dat` files, W/m²) and EPW files the horizontal infrared
radiation; the weather producer carries neither (`weather/calculation.py`, `RAW_COLUMNS`), and NSRDB, ERA5 and the
DWD 10- and 15-minute sources have no such column. In the Aachen reference year the difference θ_e − θ_sky derived
from column A (θ_sky = (A/σ)^¼) has a mean of 12.7 K and a median of 11.7 K, 4.3 K at the 10th and 22.6 K at the
90th percentile. Consequence: the constant is within 15 % of the reference year's mean and every weather source
works; the dynamic term stays a candidate for a later spec (Q1).

**D12 — A TABULA roof faces the sky with the share A_Estim_Roof / (A_Estim_Roof + A_Estim_UpperCeiling) of its
area** (proposed, 2026-11-26; was §3.2 of the draft). TABULA's roof element is the construction that closes the
heated zone at the top: the roof itself where the attic is heated or absent, the upper ceiling under an unheated
attic otherwise, and the row says which only through its two area estimates (`b_Transmission_Roof_*` is 1 in both
cases; `Code_RoofType` names the ceiling in 267 of the 643 ceiling-only rows of the catalogue). Of the 2662 rows with
a roof area, 1939 estimate a roof only (share 1), 643 a ceiling only (share 0) and 80 both; in the German rows the
share is 1 for every FR row and all but three TR rows and 0 for every UC and unknown row. Consequence: a ceiling
under an unheated attic has no sky term and no absorbed radiation (the attic's roof is D8's second sum); the 80 mixed
rows are approximated by the share; a configured roof area counts in full.

**D13 — The absorbed radiation is a second column of the solar-gains producer, the sky term is one number computed
with the building, and the building composes Φ_sol per timestep** (proposed, 2026-11-26). The absorbed radiation is,
like the window gain, A_sol times the plane irradiance, so it belongs to the producer that computes the plane
irradiance: it is a pure function of the same weather and of the surfaces and is cached under the same key
discipline (`roadmap/cache_service_spec.md` §3.1). The sky term depends on nothing that changes within a run.
Consequence: §4.4; the timestep loop gains two additions and no calculation.

**D14 — Two configuration fields, two outputs with energy ports; α = 0 and Δθ_er = 0 reproduce today's results
exactly** (proposed, 2026-11-26). Consequence: §4.6.

## 3. Terms

Symbols as in clause 4 of ISO 13790; HiSim's names in §4.4.

- **Φ_sol** (W): the heat flow from the solar sources of the zone, Eq. 42, the sum of Φ_sol,k over the elements k;
  it enters the nodes through C.2 and C.3 and is negative at night under this spec.
- **Φ_sol,k** (W): the heat flow of element k, Eq. 44: F_sh,ob,k · A_sol,k · I_sol,k − F_r,k · Φ_r,k.
- **A_sol,k** (m²): the effective collecting area of element k, the area of a black body with the same solar gain
  (11.3.2 NOTE 1). Window: Eq. 45, F_sh,gl · g_gl · (1 − F_F) · A_w,p, what the window optics compute today. Opaque
  element: Eq. 46, α_S,c · R_se · U_c · A_c.
- **I_sol,k** (W/m²): the mean irradiance on the plane of element k over the timestep, from direct, diffuse and
  ground-reflected radiation by a recognised conversion (Annex F.2.2); HiSim: `pvlib.irradiance.get_total_irradiance`,
  isotropic sky, albedo 0.2 (`window.Window`).
- **F_sh,ob,k** (–): the shading reduction factor for external obstacles of element k, 11.4.4; TABULA's `F_sh_vert`
  and `F_sh_hor`.
- **α_S,c** (–): the solar absorption coefficient of an opaque surface.
- **R_se** (m²K/W): the external surface heat resistance of ISO 6946, 1 / (h_ce + h_r).
- **U_c** (W/(m²K)), **A_c** (m²): the thermal transmittance and the projected area of the element.
- **Φ_r** (W): the extra heat flow of an element by thermal radiation to the sky, Eq. 47: R_se · U_c · A_c · h_r · Δθ_er.
- **F_r** (–): the form factor between the element and the sky, 11.4.6.
- **h_r** (W/(m²K)): the external radiative heat transfer coefficient, Eq. 52 or its approximation 5 ε.
- **ε** (–): the emissivity of the external surface for thermal radiation.
- **Δθ_er** (K): the mean difference between the external air temperature and the apparent sky temperature.
- **b_tr** (–): the adjustment factor of an element whose other side is not the external environment (8.3.1); 1 for
  a surface to outdoor air.
- **β** (°): the tilt of a plane against the horizontal; **azimuth** (°): 180 south, 90 east, 270 west, 0 north, as
  the windows use it.
- **Surface**: one entry of the sky-facing list of D4: area, U-value, tilt, azimuth, shading factor, and whether it
  absorbs (opaque) or not (window, door).

## 4. Design

### 4.1 The two terms, and why R_se · U appears in both

Both terms are a heat flow at the outer surface of an element times the share of it that reaches the inside. An
opaque surface absorbs α · I of the irradiance I on its plane. Under steady conditions with the same temperature
inside and out, the absorbed heat leaves the surface in two directions: outward through R_se to the air, inward
through the rest of the element, 1/U − R_se, to the room. The inward share is R_se / (R_se + 1/U − R_se) = R_se · U.
Eq. 46 is that share times the absorbed flux times the area, Φ = α · I · R_se · U · A, and A_sol = α · R_se · U · A
is the area of a black body that would let the same heat in. Thermal radiation to the sky is the same mechanism with
the sign reversed: the surface loses h_r · Δθ_er per m² to a sky that is Δθ_er colder than the air, reduced by its
view factor F_r, and the inward share R_se · U of that loss is drawn from the room: Eq. 47, Φ_r = R_se · U · A · h_r
· Δθ_er, with F_r in Eq. 44. Both terms vanish for a perfectly insulated element and grow in proportion to U: with the
values of D2 a vertical wall of U = 1 W/(m²K) draws 0.99 W/m² to the sky all year and gains 0.024 W per W/m² of
irradiance, 12 W/m² under 500 W/m².

The standard adds the sky term to the solar gains "for convenience" (11.3.2 NOTE 2): it is a loss at the outer
surface, but the simple hourly method knows no outer surface, so the loss enters the inner nodes as a negative gain
through C.2 and C.3. The alternative of 11.3.5, an external operative temperature below the air temperature on
H_tr,em, is the same loss on the other side of the element; D1 does not take it.

### 4.2 Which surfaces count, with which geometry

For a TABULA row, after the floor-area scaling the transmission uses (`BuildingInformation.get_building_area_parameters`):

| Surface | Counts when | Area, U-value | Tilt β, azimuth | F_r | F_sh | Absorbs |
|---|---|---|---|---|---|---|
| Wall sub-area `A_Wall_i` | `b_Transmission_Wall_i` = 1 and area > 0 (D8) | scaled sub-area, `U_Actual_Wall_i` | 90°; a quarter each at 180, 90, 0, 270 (D3) | 0.5 | `F_sh_vert` | yes, α |
| Roof `A_Roof_1` + `A_Roof_2` | share of D12 > 0 | scaled area times the share, roof U-value of the transmission | TR: 45°, four quarters (D5); FR: 0°, no azimuth; UC, unknown: share 0 | 0.854 / 1 | `F_sh_hor` | yes, α |
| Window per direction | always | `scaled_window_areas_in_m2`, window U-value | 90° at the direction's azimuth; horizontal 0° | 0.5 / 1 | – (its solar part is Eq. 45 as today) | no |
| Door | always | door area, door U-value | 90°, no irradiance needed | 0.5 | – | no |
| Floor | never | | | | | |

The wall sub-areas are summed as U · A per quarter: A_sol and Φ_r are linear in U · A, so each quarter carries a
quarter of Σ_i U_i · A_i and no area-weighted U-value is needed. A configured `facade_area_in_m2`, `roof_area_in_m2`
or U-value enters as the transmission uses it, with b = 1 (D8) and the roof share of D12.

For a described building (D4) the list is used as written; each entry is one surface of the table with its own tilt,
azimuth and shading factor. The windows and the door of the description give the sky term of the last two rows.

### 4.3 The per-surface terms

R_se, h_r (through ε) and the F_r formula are constants of the code; α and Δθ_er are configuration fields with the
defaults of D2.

```
A_sol,k  = α · R_se · U_k · A_k · F_sh,k                 [m²]   opaque surfaces only
Φ_r,k    = F_r(β_k) · R_se · U_k · A_k · h_r · Δθ_er     [W]    every surface, windows and door included
F_r(β)   = (1 + cos β) / 2                                [–]
```

### 4.4 Where each quantity is computed

| Quantity | Module, function | When | Cached |
|---|---|---|---|
| The sky-facing surfaces of a TABULA row (D3, D5, D8, D12) | `building/information.py`, the stage-1 pipeline (`get_building_heat_transfer_parameters` today, `BuildingDescription.from_tabula` once the description spec is built); the surface dataclass in a new module `building/envelope.py` | with the building | – |
| A_sol,k and Φ_r,k of one surface, F_r(β) | `building/envelope.py`, pure functions of the surface and the constants | with the building | – |
| Φ_sky = Σ_k Φ_r,k, one number | `building/envelope.py`, pure function; kept by `Building.build` | with the building | – |
| I_k(t) per distinct plane (tilt, azimuth) | `building/solar_gains.py`: the plane irradiance now inside `window.Window.calc_solar_heat_gains`, computed once per plane and timestep and multiplied by every A_sol on that plane, windows and surfaces alike | before the first timestep | yes |
| Φ_opaque(t) = Σ_k A_sol,k · I_k(t) | `building/solar_gains.py`, `produce_solar_gains`: second column `solar_gain_through_opaque_elements` beside `solar_gain_through_windows`; the surfaces and α are key material of `SolarGainsInputs` | before the first timestep | yes, in the same entry as the window gains |
| Φ_sol(t) = Φ_windows(t) + Φ_opaque(t) − Φ_sky | `building/building.py`, `i_simulate`, in place of today's `solar_heat_gain_through_windows[timestep]` | every timestep | – |
| The node split C.2 and C.3, the Crank–Nicolson step, the theoretical demand C.4, the losses of the energy balance | the unchanged functions of `building.py`, called with Φ_sol instead of the window gain | every timestep | – |

The producer's shortcut for a dark timestep (no irradiance, no pvlib call) applies to both columns; Φ_sky needs no
timestep. The sharing of planes is new: today each window is one pvlib call per daylight timestep, and with four wall
planes that coincide with the window planes and up to four roof planes the producer evaluates at most nine planes
instead of up to thirteen surfaces. The layering rule of `solar_gains.py` (no component, no simulator, no repository)
holds; `envelope.py` joins its import closure as `window.py` does, so an edit to the surface arithmetic moves the
cache key.

### 4.5 How Φ_sol enters the network and the energy balance

C.1 to C.3 split Φ_int and Φ_sol onto the nodes: Φ_ia = 0.5 · Φ_int to the air, Φ_m = (A_m / A_t) · (0.5 · Φ_int +
Φ_sol) to the mass, Φ_st = (1 − A_m / A_t − H_tr,w / (9.1 · A_t)) · (0.5 · Φ_int + Φ_sol) to the surface, and the
remainder H_tr,w / (9.1 · A_t) reaches no node (`GainsLostThroughWindowsInWatt`). With the new Φ_sol nothing changes
in these expressions (`Building.calc_internal_heat_flows_from_internal_gains_and_solar_gains`); at night the two
inner nodes receive a negative input. The heated zone's balance (`Building.heat_losses_to_outside_in_watt`,
`tests/test_building_energy_ports.py`) reads

```
Φ_int + Φ_windows + Φ_opaque + Φ_HC = ventilation + transmission(w) + transmission(em)
                                     + gains lost through windows + Φ_sky + stored
```

so the sky term is a `LOSS` port of its own and the absorbed radiation a second `CONSUMPTION` port on the solar
carrier; the "gains lost through windows" term uses the net Φ_sol. The theoretical heating and cooling demand
(C.4, `calc_theoretical_thermal_building_demand_for_building`) takes the same Φ_sol.

### 4.6 Configuration, outputs, switch-off

- `BuildingConfig.opaque_solar_absorptance: float = 0.6` (α, D2) and
  `BuildingConfig.sky_temperature_difference_in_kelvin: float = 11.0` (Δθ_er, D11); both refused unless finite and
  ≥ 0, the absorptance also unless ≤ 1.
- `BuildingDescription.sky_facing_surfaces` (D4): a tuple of surfaces, mandatory, may be empty; each with
  `area_in_m2`, `u_value_in_watt_per_m2_per_kelvin`, `tilt_angle_in_degrees` (0 to 90), `azimuth_angle_in_degrees`
  (`null` exactly at tilt 0) and `external_shading_reduction_factor` (0 to 1), validated like the windows of that spec.
- Outputs of `Building`: `SolarGainThroughOpaqueElementsInWatt` (energy port `CONSUMPTION`, `SOLAR`, environment
  `SOLAR`, like the window gain) and `SkyRadiationLossInWatt` (energy port `LOSS`, `ROOM_HEAT`; positive, a loss of
  room heat, constant over the run). `SolarGainThroughWindows` keeps its name, its meaning and its KPI.
- α = 0 makes the second column zero and Δθ_er = 0 the sky term zero; Φ_sol = Φ_windows + 0.0 − 0.0 is bit-identical
  to today's gain, so a run with both reproduces today's results exactly.

### 4.7 Worked numbers for the default house

`DE.N.SFH.05.Gen.ReEx.001.002`, scaling factor 1, Aachen 1995–2012 (`aachen_center.dat`). The annual plane
irradiances are pvlib's isotropic sums of that file: horizontal 1067 kWh/m²; vertical S 840, E 650, N 418, W 688;
45° S 1174, E 934, N 663, W 962. 1.98 W per W/K is R_se · h_r · Δθ_er = 0.04 · 4.5 · 11; 0.024 is α · R_se.

| Surface | U · A (W/K) | Rule | Φ_r (W) | A_sol (m²) | Absorbed per year (kWh) |
|---|---|---|---|---|---|
| Wall 1: 141.2 m², U 0.234, b 1 | 33.0 | four quarters at 90° | 0.5 · 1.98 · 33.0 = 32.7 | 0.119 per quarter (α 0.6, F_sh 0.6) | 0.119 · 2597 = 309 |
| Wall 2: 8.7 m², U 0.213, b 0 | – | excluded (D8) | 0 | 0 | 0 |
| Roof: 168.9 m², U 0.41, TR, share 1 | 69.2 | 45°, four quarters (D5) | 0.854 · 1.98 · 69.2 = 117.0 | 0.332 per quarter (F_sh 0.8) | 0.332 · 3732 = 1240 |
| the same roof horizontal (Q3) | 69.2 | 0° | 1 · 1.98 · 69.2 = 137.1 | 1.330 | 1.330 · 1067 = 1418 |
| Windows: 27.1 m², U 1.3 | 35.2 | vertical | 0.5 · 1.98 · 35.2 = 34.9 | – | – |
| Door: 2.1 m², U 1.3 | 2.7 | vertical | 2.7 | – | – |
| Floor: 115.8 m² | – | never | 0 | 0 | 0 |
| **Sum, roof at 45°** | | | **187.3 W = 1641 kWh/a** | | **1549** |
| Sum, roof horizontal | | | 207.4 W = 1817 kWh/a | | 1727 |

With the ISO 52016-1 set (Q2) the two factors are 1.885 and 0.0248. Peaks: a wall quarter under 836 W/m² gains
99 W, a 45° roof quarter under 1022 W/m² 340 W, the horizontal roof under 916 W/m² 1218 W.

## 5. What changes and what does not

- **Results:** every building moves, because every TABULA row has exposed walls. The sky term raises the heating
  demand in every hour of the heating season; the absorbed radiation lowers it in sunny hours and raises the indoor
  temperature and the cooling demand in summer. The default house gets about 190 W of sky loss and 1.5 MWh/a of
  absorbed radiation (§4.7); the KPI deltas of the twins are measured when the change is built (§7). 60 s results are
  not bit-identical; with α = 0 and Δθ_er = 0 they are.
- **Public interfaces:** two outputs and two configuration fields on `BuildingConfig`, one field on
  `BuildingDescription` (D4), a second column in the solar-gains cache entry, a new module `building/envelope.py`.
  Nothing is renamed. RenoVisor gets the heads-up for a result change (`system_docs/results_and_interfaces.md` §5);
  the building's report (`Building.__str__`) states the surfaces and the sky term.
- **Goldens and twins:** all KPI goldens are re-blessed through `golden-update.yml` with the deltas listed in the
  pull request; the realized twins are re-recorded because the configuration gains fields; the characterization
  golden gains the surfaces; every solar-gains cache entry is recomputed once, since the key moves with the producer's
  code.
- **Verification suite** (branch `verification_spec`): §3.2.9 of its spec records both terms as a model deviation;
  once this is built, the entry goes, the cases state their walls and roof as surfaces of the description with their
  orientation, an air-to-air U-value and the α of the case (0.1, 0.6 or 0.9 in the ASHRAE 140 series), and the
  suite's irradiance per orientation feeds the surfaces as it feeds the windows.
- **Unchanged:** the window optics and their gain, the conductances including the thermal bridges (D9), the thermal
  mass, the weather producer (D11), the node split and the solver.

## 6. Verification

- Unit tests of the pure functions of `envelope.py`: F_r(0°) = 1, F_r(90°) = 0.5, F_r(45°) = 0.854; a wall of
  10 m² with U = 1 W/(m²K) and F_sh = 1 under 500 W/m² absorbs 0.6 · 0.04 · 1 · 10 · 500 = 120 W and radiates
  0.5 · 0.04 · 1 · 10 · 4.5 · 11 = 9.9 W; the same surface horizontal radiates 19.8 W, at 45° 16.9 W.
- The TABULA derivation on named rows: the default house (TR, share 1, wall 2 excluded; four wall quarters, four
  roof quarters, the five window entries of the row, one door); a UC row (share 0, no roof surface); one of the 80
  mixed rows (share between 0 and 1); a row with a configured facade area (b = 1) and one with a configured roof
  U-value.
- The quarter rule: the four wall quarters sum to Σ U_i · A_i of the exposed sub-areas, the roof quarters to the
  roof's share.
- The producer: the second column is zero in a dark timestep; a surface on a window's plane uses the window's
  irradiance (one evaluation per plane, asserted by a counting fake); the cache key moves when a surface, α or the
  surface arithmetic changes and stays when the building's name changes; the layering closure of
  `tests/test_building_solar_gains_producer.py` lists `envelope.py`.
- With α = 0 and Δθ_er = 0 the one-day snapshot (`tests/test_building_one_day_snapshot.py`) and a household twin
  reproduce today's results bit for bit.
- The heated zone's energy balance closes with the two new ports in every step (`tests/test_building_energy_ports.py`,
  `tests/test_energy_balance_building.py`), the sky term as a loss and the absorbed radiation as a gain.
- The golden gate, with the KPI deltas per twin in the pull request.

## 7. Measured results

None yet. To be reported here when built: the KPI deltas of the household twins (heating demand, cooling demand,
hours above the cooling set point) at 60 s and 15 min, and the ASHRAE 140 base cases and the in-depth cases with
α 0.1 and 0.9 of the verification suite before and after, against their reference ranges.

## 8. Open questions

Q2 is open and gets a bead with the epic (§9); Q1, Q3 and Q4 are decided.

- **Q1. Δθ_er: the constant, or the weather's sky temperature.** (a) The constant of D11, 11 K, configurable.
  (b) Per timestep Δθ_er(t) = θ_e(t) − θ_sky(t) with θ_sky = (L_down / σ)^¼ from the DWD column A or the EPW
  horizontal infrared radiation, and the constant where a source has no such column (NSRDB, ERA5, DWD 10 and 15 min);
  Φ_sky becomes a series of the weather producer's cache entry, h_r stays constant (D10). (b) is closer to the physics
  the reference programs of ASHRAE 140 compute (clear nights 20 K and more, overcast nights near 0 K; Aachen: 4 to
  23 K between the 10th and 90th percentile around a mean of 12.7 K); it needs a new weather column, moves every
  weather cache key and gives two code paths. *Decided (owner, 2026-11-27):* (a); D11 holds. (b) stays a candidate
  for a later spec once the EPW weather of the verification stack is on `main`.
- **Q2. The value set.** (a) ISO 13790 with ε = 0.9 (D2): R_se 0.04, h_r 4.5. (b) ISO 52016-1: R_se 0.0414,
  h_r 4.14, no ε. Difference 3.5 % in the absorbed radiation, 5 % in the sky term. With (a) the owner accepts two
  numbers the normative text of ISO 13790 does not print, R_se and α from its worked example, and one it does not
  contain at all, ε (table of D2). Recommendation: (a), see D2.
- **Q3. The tilt of a TABULA pitched roof.** Context: a roof surface needs a tilt and an azimuth for its plane
  irradiance I_sol,k and a form factor F_r for its sky term. TABULA states the roof type (`Code_RoofType`: TR pitched,
  FR flat) and the roof area as the sloped surface (default house: 168.9 m² over a 115.8 m² footprint), but no
  orientation; ISO 13790 11.4.6 prints F_r for a horizontal roof (1) and a vertical wall (0.5) and nothing for a
  tilted plane. (a) A pitched roof stands at 45° and is split into four equal quarters facing south, east, north and
  west, the rule of the walls (D3); F_r = (1 + cos 45°) / 2 = 0.854 from the isotropic view-factor formula, which
  returns the two printed values at 0° and 90° and is the formula the window optics use for the diffuse share; a flat
  roof stays horizontal. This follows the geometry TABULA states, at the price of one value of ours, 0.854, and four
  roof planes in the producer. (b) Every roof is horizontal: F_r = 1 as printed, the irradiance is the horizontal one,
  one roof plane. Simpler and fully printed, but a 45° roof then sees the whole sky and receives 14 % more irradiance
  than the mean of its four slopes (Aachen: 1067 against 933 kWh/m²a); for the default house the sky term rises from
  187 to 207 W and the absorbed radiation from 1549 to 1727 kWh/a (§4.7). *Decided (owner, 2026-11-27):* (a); D5
  holds.
- **Q4. The build order against the building description spec.** Context: `roadmap/building_description_spec.md`
  (owner, 2026-11-26, nothing built) restructures `BuildingInformation` into stage 1, which turns a TABULA row into a
  `BuildingDescription` with every quantity in final form, and stage 2, the physics computed from a description; a
  described building skips stage 1. Deriving the sky-facing surfaces from a TABULA row (D3, D5, D8, D12) is stage-1
  work, and the field `sky_facing_surfaces` of D4 belongs to the description class, which that spec's first pull
  request creates and its second exposes in the configuration. (a) Build this spec after that first pull request: the
  derivation is written once, in stage 1, and the field is added where it belongs. (b) Build this spec now, in today's
  `BuildingInformation`: the derivation is written into the old pipeline and moved into stage 1 when the description
  spec lands, and a described building gets its surfaces only then. The physics (`envelope.py`, the producer column,
  the outputs, the goldens) is the same in both orders. Recommendation: (a); both specs are the owner's, so this is a
  scheduling choice, not a design one. *Decided (owner, 2026-11-27):* (a); this spec is built after the first pull
  request of the building description spec.

## 9. Beads

The epic and its beads are created once the draft is agreed, and the work starts after the first pull request of
`roadmap/building_description_spec.md` (Q4). The intended split is two pull requests: the first adds
the surfaces, the sky term, the producer column, the outputs and the goldens for the TABULA path; the second adds the
description field of D4 after the description spec's second pull request.
