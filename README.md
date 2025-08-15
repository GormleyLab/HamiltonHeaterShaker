# Hamilton Heater Shaker (HHS) Remote Controller

This program provides remote control functionality for Hamilton heater shaker devices. It was developed to enable direct communication with Hamilton heater shakers without dependencies on PyLabRobot.

## Background

The goal of this program is to remotely control a Hamilton heater shaker (HHS) through standalone Python code. The implementation consists of three main files:

- **`heater_shaker.py`** - The core HeaterShaker class providing direct communication with Hamilton heater shakers
- **`main.py`** - Test program demonstrating HeaterShaker functionality and various protocols
- **`pylabrobot.py`** - Reference code from PyLabRobot implementation (for context only, not used in execution)

The `pylabrobot.py` file contains code taken from another developer who created a similar program. Its content and examples were used as reference to create the `heater_shaker.py` implementation, but it is not used anywhere in the actual program execution - it remains in the project folder purely for context and reference.

## Features

- **Direct Hamilton HHS Communication**: Uses real Hamilton command protocol without PyLabRobot dependencies
- **Dual Interface Support**: 
  - RS232 connection (for STAR systems)
  - USB connection (via heater shaker box)
- **Complete Protocol Implementation**: Temperature control, shaking, plate locking, and device initialization
- **Safety Features**: Proper initialization, error handling, and shutdown procedures
- **Test Suite**: Comprehensive testing program with both full protocols and connection-only tests

## Requirements

### Python Dependencies

```bash
pip install pyserial  # For RS232/serial communication
pip install pyusb     # For USB communication
```

### Hardware Requirements

- Hamilton Heater Shaker device
- Communication interface:
  - **RS232**: Direct serial connection to STAR system
  - **USB**: USB connection via Hamilton heater shaker box

## Usage

### Basic Example

```python
from heater_shaker import HeaterShaker

# Create heater shaker instance
hs = HeaterShaker(
    port="COM3",        # Windows: COM3, COM4, etc. | Linux: /dev/ttyUSB0, etc.
    interface="rs232",  # "rs232" for STAR connection or "usb" for heater shaker box
    device_index=1,
    name="My_HHS"
)

# Initialize device
hs.initialize(temp=25.0)

# Run heat-shake protocol
success = hs.heat_shake(
    time_seconds=300,    # 5 minutes
    temperature=37.0,    # 37°C
    speed=800           # 800 steps/second
)

# Shutdown safely
hs.shutdown()
```

### Test Program

Run the included test program to verify functionality:

```bash
python main.py
```

The test program provides three options:
1. **Run full test protocol** - Complete initialization, heat-shake cycle, and individual control tests
2. **Test connection only** - Quick connection verification without running protocols
3. **Exit** - Quit the program

### Individual Controls

```python
# Temperature control
hs.set_temperature(37.0)
current_temp = hs.get_temperature()

# Shaking control
hs.start_shaking(speed=800)
time.sleep(10)  # Shake for 10 seconds
hs.stop_shaking()

# Plate locking
hs.lock_plate()
hs.unlock_plate()
```

## Configuration

### Connection Parameters

- **Port**: 
  - Windows: `COM3`, `COM4`, etc.
  - Linux: `/dev/ttyUSB0`, `/dev/ttyUSB1`, etc.
- **Interface**: 
  - `"rs232"` - Direct RS232 connection to STAR
  - `"usb"` - USB connection via heater shaker box
- **Device Index**: 1-8 for box connections, 1-2 for STAR connections

### Device Specifications

- **Temperature Range**: 0.0°C to 115°C
- **Shaking Speed**: 20 to 2000 steps/second
- **Acceleration**: 500 to 10000 increments/second

## Hamilton Command Protocol

The implementation uses authentic Hamilton HHS commands:

| Command | Description | Example |
|---------|-------------|---------|
| `TA` | Set temperature | `T1TAid0001ta0370` (37.0°C) |
| `RT` | Get temperature | `T1RTid0002` |
| `SB` | Start shaking | `T1SBid0003st0sv0800sr01000` |
| `SC` | Stop shaking | `T1SCid0004` |
| `SW` | Wait for stop | `T1SWid0005` |
| `RD` | Get shaking status | `T1RDid0006` |
| `LP` | Lock/unlock plate | `T1LPid0007lp1` |
| `LI` | Initialize lock | `T1LIid0008` |
| `SI` | Initialize shaker | `T1SIid0009` |
| `TO` | Deactivate heating | `T1TOid0010` |

## Troubleshooting

### Connection Issues

1. **Check device power** - Ensure the heater shaker is powered on
2. **Verify port** - Use the connection test to list available ports
3. **Check drivers** - Ensure proper USB/serial drivers are installed
4. **Permission issues** - On Linux, user may need to be in `dialout` group

### Dependencies

If you encounter import errors:

```bash
# Install required packages
pip install pyserial pyusb

# On Linux, you may also need
sudo apt-get install libusb-1.0-0-dev
```

### USB Device Detection

The program uses Hamilton's official USB IDs:
- **Vendor ID**: `0x8AF` (Hamilton)
- **Product ID**: `0x8002` (Heater Shaker)

## Safety Features

- **Proper Initialization**: All device systems are initialized before use
- **Error Handling**: Comprehensive error checking and reporting
- **Safe Shutdown**: Automatic stop of heating/shaking on exit
- **Parameter Validation**: All inputs are validated against device specifications
- **Connection Management**: Proper connection setup and teardown

## Development

### Project Structure

```
HHS/
├── README.md           # This file
├── CLAUDE.md          # Development context and instructions
├── heater_shaker.py   # Main HeaterShaker implementation
├── main.py            # Test and demonstration program
└── pylabrobot.py      # Reference implementation (context only)
```

### Key Classes

- **`HeaterShaker`**: Main controller class with synchronous interface
- **`HHSCommands`**: Hamilton command protocol implementation
- **`SerialInterface`**: RS232/serial communication handler
- **`USBInterface`**: USB communication handler

## License

This project implements the Hamilton Heater Shaker communication protocol for automation and research purposes.

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify hardware connections and power
3. Test with the included test program (`main.py`)
4. Review device specifications and parameter ranges