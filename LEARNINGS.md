# HiSim Repository - Agent Learnings

## Repository Structure
- **Core module**: `hisim/component.py` contains the base `Component` class with input/output connection logic
- **Test markers**: Tests use `-m base` for the base test suite (125 tests)
- **Key classes**: `Component`, `ComponentInput`, `ComponentOutput`, `SimulationParameters`
- **Logging**: Uses Python's `log` module with `WRN:` prefix for warnings
- **Heat pump modules**: `advanced_heat_pump_hplib.py` and `more_advanced_heat_pump_hplib.py` share similar patterns

## Component Connection System
- Components connect via `connect_input()` and `connect_dynamic_input()` methods
- Connections are logged to `component_connections.json` in `result_directory` when `log_connections=True`
- Connection data structure: `{"From": {"Component": str, "Field": str}, "To": {"Component": str, "Field": str}}`

## Error Handling Patterns
- **Preferred exceptions**: Use specific exceptions like `ValueError` for invalid configuration, `OSError`/`IOError` for file operations
- **Error messages**: Include relevant context (e.g., manufacturer/model names) in error messages for better debugging
- **Database lookups**: When searching for items in loaded data (e.g., appliances), use descriptive `ValueError` with search parameters when item is not found

## Issue #115: connect_input Error Handling
### Problem
`connect_input()` wrote to `component_connections.json` without directory existence validation or file operation error handling.

### Failed Approaches
- **Testing unwritable directories as non-root**: System permissions allowed file creation anyway - use `/proc` or similar for reliable permission-denied testing
- **Appending to corrupted JSON files**: Existing behavior (not in scope) - would require separate fix for JSON validation

### Solution
Modified `hisim/component.py` lines 337-364:
```python
try:
    # Validate that result_directory exists, create if it doesn't
    if not os.path.exists(self.my_simulation_parameters.result_directory):
        os.makedirs(self.my_simulation_parameters.result_directory, exist_ok=True)
    # Original file writing logic
except
