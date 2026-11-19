"""Verification of the building model against the test cases of ANSI/ASHRAE Standard 140.

The suite describes each test case in a YAML file (``schema``, ``case_loader``), derives the 5R1C
parameters from that description by the rules of ``roadmap/hisim_verification_agent_spec.md``
§3.2 (``derivation``) and builds the HiSim components of a case (``system_builder``). The case
files are private for now and live under ``HISIM_VERIFICATION_DATA`` (spec §7.2); the public part
is the suite file ``suites/ashrae140/suite.yaml`` with the structure of the acceptance ranges and
the deviation register.
"""
