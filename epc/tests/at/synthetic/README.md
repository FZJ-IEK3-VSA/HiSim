# Austrian engine: tests on synthetic data

> **DISCLAIMER - SYNTHETIC TEST DATA.** Every table value in this folder is invented to test the
> code, and any building in it is a placeholder or a HiSim test record: no value comes from an
> OENORM table, and no number computed here is the heating demand of any real building. **Never use
> them for a study or a real calculation.** The standards themselves (OENORM B 8110-5, B 8110-6-1,
> B 8110-6-2, H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards
> International: <https://www.austrian-standards.at>

These tests check every term of the Austrian engine against a hand computation (marker `base`;
`roadmap/epc/design.md` §9). `at_tables.py` writes complete tables in the shape of the data contract
(`epc/data/README.md`) with hand-chosen values; every file it writes starts with the line
`# SYNTHETIC TEST DATA - invented, not from any OENORM table`.

The tests against the real, licensed tables lie in `epc/tests/at/reference_cases/`, local and
git-ignored (requirements D41).
