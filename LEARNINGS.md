# HiSim Repository - Agent Learnings

## Repository Structure
- **Core module**: `hisim/component.py` contains the base `Component` class with input/output connection logic
- **Test markers**: Tests use `-m base` for the base test suite (125 tests)
- **Key classes**: `Component`, `ComponentInput`, `ComponentOutput`, `SimulationParameters`
- **Logging**: Uses Python's `log` module with `WRN:` prefix for warnings

## Component Connection System
- Components connect via `connect_input()` and `connect_dynamic_input()` methods
- Connections are logged to `component_connections.json` in `result_directory` when `log_connections=True`
- Connection data structure: `{"From": {"Component": str, "Field": str}, "To": {"Component": str, "Field": str}}`

## Issue #115: connect_input Error Handling
### Problem
`connect_input()` wrote to `component_connections.json` without:
- Directory existence validation
- Error handling for file operations
- Graceful failure mode

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
    if os.path.exists(file_name):
        # Append to existing file
    else:
        # Create new file
except (OSError, IOError) as e:
    log
