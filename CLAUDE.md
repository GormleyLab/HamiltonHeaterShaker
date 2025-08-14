# CLAUDE.md - Hamilton Heater Shaker Project Context

## Project Overview

This project implements a standalone Python controller for Hamilton heater shaker (HHS) devices, enabling remote control without PyLabRobot dependencies.

## Project Background

**Goal**: Create a program to remotely control a Hamilton heater shaker (HHS)

**Development History**:
1. `pylabrobot.py` was obtained from another developer who created a similar Hamilton HHS control program
2. The content and examples from `pylabrobot.py` were used as reference material to understand the Hamilton command protocol
3. `heater_shaker.py` was created as a standalone implementation based on this reference
4. `main.py` was created to test and demonstrate the functionality in `heater_shaker.py`
5. **Important**: `pylabrobot.py` is NOT used in program execution - it exists purely for context and reference

## File Structure and Roles

```
HHS/
├── README.md           # User documentation and usage guide
├── CLAUDE.md          # This file - development context
├── heater_shaker.py   # Core implementation - HeaterShaker class
├── main.py            # Test program demonstrating functionality
└── pylabrobot.py      # Reference code (CONTEXT ONLY - not executed)
```

### Key Files

1. **`heater_shaker.py`** - The main implementation
   - `HeaterShaker` class with synchronous interface
   - `HHSCommands` class with Hamilton protocol implementation
   - `SerialInterface` and `USBInterface` for communication
   - Real Hamilton command protocol based on PyLabRobot analysis

2. **`main.py`** - Test and demonstration program
   - Full protocol testing with heat-shake cycles
   - Connection-only testing for troubleshooting
   - Individual control testing (temperature, shaking, locking)
   - Comprehensive error handling and user feedback

3. **`pylabrobot.py`** - Reference material ONLY
   - Original PyLabRobot implementation code
   - Used as reference for understanding Hamilton protocol
   - Contains authentic Hamilton USB IDs and command structures
   - **NOT imported or used in execution**

## Technical Implementation

### Hamilton Command Protocol

The implementation uses authentic Hamilton HHS commands extracted from PyLabRobot:

- **Command Format**: `T{index}{command}id{id}{args}`
- **Example**: `T1TAid0001ta0370` (Set device 1 to 37.0°C)

### Communication Interfaces

1. **RS232/Serial** (`SerialInterface`)
   - Direct connection to STAR systems
   - Uses pyserial library
   - Standard 9600 baud, 8N1 configuration

2. **USB** (`USBInterface`) 
   - Connection via Hamilton heater shaker box
   - Uses pyusb library
   - Hamilton VID:PID = 0x8AF:0x8002

### Device Specifications

- Temperature: 0.1°C to 105°C
- Speed: 20 to 2000 steps/second  
- Acceleration: 500 to 10000 increments/second
- Multi-device support: 1-8 devices per box, 1-2 per STAR

## Development Notes

### Dependencies
- `pyserial` - Required for RS232/serial communication
- `pyusb` - Required for USB communication
- Both are optional - program detects availability and warns if missing

### Architecture Decisions

1. **Synchronous Interface**: Main HeaterShaker class provides sync methods wrapping async implementations
2. **Protocol Fidelity**: Uses exact Hamilton command format from PyLabRobot analysis
3. **Safety First**: Proper initialization, shutdown, and error handling throughout
4. **Dual Interface**: Supports both RS232 (STAR) and USB (box) connections

### Testing Strategy

The `main.py` program provides comprehensive testing:
- Full protocol test with initialization, heat-shake cycle, and individual controls
- Connection-only test for troubleshooting
- Parameter validation and error reporting
- Graceful shutdown with proper device cleanup

## Command Reference

### Core Commands Used

| Command | Purpose | Parameters | Example |
|---------|---------|------------|---------|
| `LI` | Initialize lock system | none | `T1LIid0001` |
| `SI` | Initialize shaker drive | none | `T1SIid0002` |
| `TA` | Set temperature | `ta=temp*10` | `T1TAid0003ta0370` |
| `RT` | Get temperature | none | `T1RTid0004` |
| `LP` | Lock/unlock plate | `lp=0/1` | `T1LPid0005lp1` |
| `SB` | Start shaking | `st=dir,sv=speed,sr=accel` | `T1SBid0006st0sv0800sr01000` |
| `SC` | Stop shaking | none | `T1SCid0007` |
| `SW` | Wait for stop | none | `T1SWid0008` |
| `RD` | Get shaking status | none | `T1RDid0009` |
| `TO` | Deactivate heating | none | `T1TOid0010` |

### Response Parsing

- Temperature responses: `"...rt+0370 +0365..."` (middle, edge temps in tenths)
- Shaking status: Response ending in "1" means shaking, "0" means stopped
- General success: Non-empty response typically indicates success

## Usage Patterns

### Typical Workflow
1. Create `HeaterShaker` instance with port/interface
2. Call `initialize(temp=25.0)` - sets up lock, shaker, initial temp
3. Run protocols with `heat_shake()` or individual controls
4. Always call `shutdown()` for safe cleanup

### Error Handling
- Connection errors are caught and reported clearly
- Parameter validation prevents invalid commands
- Device state is tracked to prevent invalid operations
- Safe shutdown ensures device is left in good state

## Troubleshooting Guide

### Common Issues
1. **Import errors**: Install `pyserial` and/or `pyusb`
2. **Connection failed**: Check device power, port, permissions
3. **No response**: Verify correct port/interface selection
4. **Protocol errors**: Ensure device is properly initialized

### Development Testing
- Use `main.py` for full testing
- Connection-only test for basic troubleshooting  
- Check available ports with `HeaterShaker.list_available_ports()`
- Enable debug logging for protocol analysis

## Future Enhancements

Potential improvements:
- Configuration file support
- Extended temperature monitoring
- Multi-device orchestration
- Protocol scripting/automation
- Real-time status monitoring
- Data logging and export

## Integration Notes

This implementation is designed to be:
- **Standalone**: No PyLabRobot dependencies
- **Compatible**: Uses authentic Hamilton protocol
- **Extensible**: Clean class structure for enhancements  
- **Reliable**: Comprehensive error handling and safety features
- **Portable**: Works on Windows, Linux, macOS with appropriate drivers