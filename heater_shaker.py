"""
heater_shaker.py - Standalone Hamilton Heater Shaker Controller

This module provides a HeaterShaker class for direct communication with Hamilton heater shakers
without dependencies on PyLabRobot. Supports both RS232 (STAR connection) and USB communication.

Usage:
    from heater_shaker import HeaterShaker
    
    hs = HeaterShaker(port="COM3", interface="rs232")
    hs.initialize(temp=25.0)
    hs.heat_shake(300, 37.0, 800)  # 5 min, 37°C, 800 steps/sec
    hs.shutdown()

Protocol Based On: Real Hamilton HHS commands from PyLabRobot implementation
"""

import asyncio
import time
import struct
from typing import Optional, Union, Literal
from enum import Enum
import logging

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("Warning: pyserial not installed. Install with: pip install pyserial")

try:
    import usb.core
    import usb.util
    USB_AVAILABLE = True
except ImportError:
    USB_AVAILABLE = False
    print("Warning: pyusb not installed. Install with: pip install pyusb")


class InterfaceType(Enum):
    """Communication interface types."""
    RS232 = "rs232"  # Direct RS232 connection to STAR
    USB = "usb"      # USB connection via heater shaker box


class HHSCommands:
    """
    Hamilton Heater Shaker command constants.
    
    Based on actual PyLabRobot implementation - these are the real Hamilton commands!
    """
    
    # Temperature commands
    SET_TEMPERATURE = "TA"    # Set temperature (ta=temp*10 as 4-digit string)
    GET_TEMPERATURE = "RT"    # Get temperature reading (returns middle + edge temps)
    DEACTIVATE_HEATING = "TO" # Turn off heating
    
    # Shaking commands  
    START_SHAKING = "SB"      # Start shaking (st=direction, sv=speed, sr=acceleration)
    STOP_SHAKING = "SC"       # Stop shaking
    WAIT_FOR_STOP = "SW"      # Wait for shaking to stop
    GET_SHAKING_STATUS = "RD" # Get shaking status (returns 1 if shaking)
    
    # Lock/plate commands
    LOCK_PLATE = "LP"         # Move plate lock (lp=1 for lock, lp=0 for unlock)
    INITIALIZE_LOCK = "LI"    # Initialize lock system
    
    # Initialization commands
    INITIALIZE_SHAKER = "SI"  # Initialize shaker drive (homing)
    
    # USB device IDs (from PyLabRobot)
    USB_VENDOR_ID = 0x8AF     # Hamilton vendor ID
    USB_PRODUCT_ID = 0x8002   # Heater Shaker product ID
    
    @staticmethod
    def build_command(index: int, command: str, command_id: int, interface_type: str = "usb", **kwargs) -> str:
        """
        Build Hamilton HHS command string.
        
        Args:
            index: Device index (1-8 for box, 1-2 for STAR)
            command: Command code (TA, SB, SC, etc.)
            command_id: Unique command ID
            interface_type: "usb" (includes T{index} prefix) or "rs232" (no prefix)
            **kwargs: Command arguments
            
        Returns:
            Complete command string
            
        Examples:
            USB: build_command(1, "TA", 123, "usb", ta="0370") -> "T1TAid0123ta0370"
            RS232: build_command(1, "TA", 123, "rs232", ta="0370") -> "TAid0123ta0370"
        """
        args = "".join([f"{key}{value}" for key, value in kwargs.items()])
        id_str = str(command_id).zfill(4)
        
        # Only include T{index} prefix for USB box control (not for STAR RS232)
        if interface_type == "usb":
            return f"T{index}{command}id{id_str}{args}"
        else:
            return f"{command}id{id_str}{args}"
    
    @staticmethod
    def format_temperature(temp_celsius: float) -> str:
        """Format temperature for Hamilton protocol (temp * 10 as 4-digit string)."""
        return f"{round(10 * temp_celsius):04d}"
    
    @staticmethod
    def format_speed(speed_increments_per_sec: int) -> str:
        """Format speed for Hamilton protocol (4-digit zero-padded)."""
        return f"{speed_increments_per_sec:04d}"
    
    @staticmethod
    def format_acceleration(accel: int) -> str:
        """Format acceleration for Hamilton protocol (5-digit zero-padded)."""
        return f"{accel:05d}"
    
    @staticmethod
    def parse_temperature_response(response: str) -> dict:
        """
        Parse Hamilton temperature response.
        
        Response format: "...er00rt+0370 +0365..." or "...rt+0370 +0365..."
        (middle temp, edge temp in tenths of degrees)
        """
        result = {'middle': None, 'edge': None, 'success': False}
        
        try:
            # Find the temperature data after 'rt'
            if 'rt' in response:
                temp_part = response.split('rt')[1]
                temps = temp_part.split()
                
                if len(temps) >= 2:
                    # Remove any leading '+' or other characters and convert
                    middle_str = temps[0].strip('+').strip()
                    edge_str = temps[1].strip('+').strip()
                    
                    # Extract numeric part only
                    import re
                    middle_match = re.search(r'[+-]?\d+', middle_str)
                    edge_match = re.search(r'[+-]?\d+', edge_str)
                    
                    if middle_match and edge_match:
                        middle_temp = float(middle_match.group()) / 10
                        edge_temp = float(edge_match.group()) / 10
                        
                        result.update({
                            'middle': middle_temp,
                            'edge': edge_temp,
                            'success': True
                        })
        except Exception as e:
            result['error'] = f"Parse error: {e}"
        
        return result
    
    @staticmethod
    def parse_shaking_response(response: str) -> bool:
        """Parse shaking status response (RD command returns rd0 or rd1)."""
        # Look for rd0 (not moving) or rd1 (moving) pattern
        if 'rd0' in response:
            return False
        elif 'rd1' in response:
            return True
        else:
            # If neither pattern found, assume not shaking
            return False
    
    @staticmethod
    def parse_response(response: str) -> dict:
        """
        Parse Hamilton HHS response string according to official manual.
        
        Format: [Command]id[id]er[error_code][data]
        
        Returns:
            Dictionary with parsed response data
        """
        result = {
            'success': False,
            'command_id': None,
            'error_code': None,
            'data': {},
            'error': None,
            'raw_response': response
        }
        
        if not response:
            result['error'] = "Empty response"
            return result
        
        try:
            # Extract command ID if present
            if 'id' in response:
                id_start = response.find('id') + 2
                if id_start + 4 <= len(response):
                    result['command_id'] = response[id_start:id_start+4]
            
            # Extract error code (Hamilton manual: er## format)
            if 'er' in response:
                er_start = response.find('er') + 2
                if er_start + 2 <= len(response):
                    error_code = response[er_start:er_start+2]
                    result['error_code'] = error_code
                    
                    # Error code 00 means success
                    if error_code == "00":
                        result['success'] = True
                    else:
                        result['success'] = False
                        result['error'] = f"Hamilton error code: {error_code}"
            else:
                # If no error code found, assume success if we got a response
                result['success'] = True
                
        except Exception as e:
            result['error'] = f"Parse error: {e}"
        
        return result


class SerialInterface:
    """RS232/Serial communication interface for Hamilton heater shaker."""
    
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 2.0):
        """
        Initialize serial interface.
        
        Args:
            port: Serial port (e.g., 'COM3' on Windows, '/dev/ttyUSB0' on Linux)
            baudrate: Communication speed (typically 9600 for Hamilton devices)
            timeout: Read timeout in seconds
        """
        if not SERIAL_AVAILABLE:
            raise ImportError("pyserial is required for RS232 communication")
        
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn = None
        self.is_connected = False
    
    async def connect(self) -> bool:
        """Establish serial connection."""
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            self.is_connected = True
            logging.info(f"Connected to {self.port} at {self.baudrate} baud")
            return True
        except Exception as e:
            logging.error(f"Failed to connect to {self.port}: {e}")
            return False
    
    async def disconnect(self) -> bool:
        """Close serial connection."""
        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
            self.is_connected = False
            logging.info(f"Disconnected from {self.port}")
            return True
        except Exception as e:
            logging.error(f"Error disconnecting: {e}")
            return False
    
    async def send_command(self, command: str) -> Optional[str]:
        """Send command and receive response."""
        if not self.is_connected or not self.serial_conn:
            raise ConnectionError("Not connected to device")
        
        try:
            # Clear input buffer
            self.serial_conn.reset_input_buffer()
            
            # Send command as ASCII string with newline
            command_bytes = (command + '\r\n').encode('ascii')
            self.serial_conn.write(command_bytes)
            logging.debug(f"Sent: {command}")
            
            # Read response until newline
            response_bytes = self.serial_conn.read_until(b'\r\n')
            response = response_bytes.decode('ascii').strip()
            logging.debug(f"Received: {response}")
            
            return response if response else None
            
        except Exception as e:
            logging.error(f"Communication error: {e}")
            return None


class USBInterface:
    """USB communication interface for Hamilton heater shaker box."""
    
    def __init__(self, vendor_id: int = None, product_id: int = None):
        """
        Initialize USB interface.
        
        Args:
            vendor_id: Hamilton USB vendor ID (uses real Hamilton ID if None)
            product_id: Heater shaker product ID (uses real Hamilton ID if None)
        """
        if not USB_AVAILABLE:
            raise ImportError("pyusb is required for USB communication")
        
        # Use real Hamilton USB IDs from PyLabRobot
        self.vendor_id = vendor_id or HHSCommands.USB_VENDOR_ID
        self.product_id = product_id or HHSCommands.USB_PRODUCT_ID
        self.device = None
        self.is_connected = False
    
    async def connect(self) -> bool:
        """Establish USB connection."""
        try:
            # Find device
            self.device = usb.core.find(
                idVendor=self.vendor_id,
                idProduct=self.product_id
            )
            
            if self.device is None:
                logging.error(f"Device not found (VID:PID = {self.vendor_id:04X}:{self.product_id:04X})")
                return False
            
            # Set configuration
            self.device.set_configuration()
            
            # Find bulk endpoints instead of hard-coding
            cfg = self.device.get_active_configuration()
            intf = cfg[(0, 0)]
            
            # Find first OUT and IN bulk endpoints
            self.out_endpoint = None
            self.in_endpoint = None
            
            for ep in intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                    if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK:
                        self.out_endpoint = ep.bEndpointAddress
                elif usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK:
                        self.in_endpoint = ep.bEndpointAddress
            
            if not (self.out_endpoint and self.in_endpoint):
                logging.error("Could not find bulk endpoints")
                return False
            
            self.is_connected = True
            logging.info(f"Connected to USB device {self.vendor_id:04X}:{self.product_id:04X}")
            logging.info(f"Using endpoints OUT: {self.out_endpoint:02X}, IN: {self.in_endpoint:02X}")
            return True
            
        except Exception as e:
            logging.error(f"USB connection failed: {e}")
            return False
    
    async def disconnect(self) -> bool:
        """Close USB connection."""
        try:
            if self.device:
                usb.util.dispose_resources(self.device)
            self.is_connected = False
            logging.info("USB device disconnected")
            return True
        except Exception as e:
            logging.error(f"USB disconnect error: {e}")
            return False
    
    async def send_command(self, command: str) -> Optional[str]:
        """Send command via USB and receive response."""
        if not self.is_connected or not self.device:
            raise ConnectionError("Not connected to USB device")
        
        try:
            # Use discovered endpoints instead of hard-coded values
            command_bytes = (command + '\r\n').encode('ascii')
            self.device.write(self.out_endpoint, command_bytes)
            logging.debug(f"USB sent: {command}")
            
            # Read response
            response_bytes = self.device.read(self.in_endpoint, 64, timeout=2000)
            response = bytes(response_bytes).decode('ascii').strip()
            logging.debug(f"USB received: {response}")
            
            return response
            
        except Exception as e:
            logging.error(f"USB communication error: {e}")
            return None


class HeaterShaker:
    """
    Standalone Hamilton Heater Shaker controller.
    
    This class provides direct communication with Hamilton heater shakers
    without PyLabRobot dependencies.
    """
    
    def __init__(self, 
                 port: str = "COM3",  # Default port
                 interface: Literal["rs232", "usb"] = "rs232",
                 device_index: int = 1,
                 name: str = "Hamilton_HHS"):
        """
        Initialize HeaterShaker controller.
        
        Args:
            port: Communication port (COM port for RS232, ignored for USB)
            interface: Communication interface type ("rs232" or "usb")
            device_index: Device index for multi-device setups
            name: Device name identifier
        """
        self.port = port
        self.interface = InterfaceType(interface)
        self.device_index = device_index
        self.name = name
        
        # Communication interface
        self.comm_interface = None
        self.is_initialized = False
        self.is_connected = False     # Initialize connection state
        
        # Device specifications (from Hamilton Manual E289247a)
        self.max_temperature = 115.0  # °C (Manual: 0000..1150 = 0-115°C)
        self.min_temperature = 0.0    # °C (Manual allows 0°C)
        self.max_speed = 2000         # increments/second (Manual: 0020..2000)
        self.min_speed = 20           # increments/second
        self.min_acceleration = 500   # increments/second² (Manual: 00500..10000)
        self.max_acceleration = 10000 # increments/second²
        
        # Current state
        self.current_temperature = None
        self.current_speed = None
        self.is_shaking = False
        self._command_id = 0  # Command ID counter
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
        # Event loop for synchronous operations
        self._loop = None
    
    def _run_async(self, coro):
        """Run async function synchronously."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(coro)
    
    # Synchronous wrapper methods for compatibility
    def initialize(self, temp: float = 25.0) -> bool:
        """Synchronous wrapper for initialize."""
        return self._run_async(self.initialize_async(temperature=temp))
    
    def heat_shake(self, time: float, temperature: float, speed: float, **kwargs) -> bool:
        """Synchronous wrapper for heat_shake."""
        return self._run_async(self.heat_shake_async(time, temperature, speed, **kwargs))
    
    def set_temperature(self, temperature: float) -> bool:
        """Synchronous wrapper for set_temperature."""
        return self._run_async(self.set_temperature_async(temperature))
    
    def get_temperature(self) -> Optional[float]:
        """Synchronous wrapper for get_temperature."""
        return self._run_async(self.get_temperature_async())
    
    def start_shaking(self, speed: float = 800, **kwargs) -> bool:
        """Synchronous wrapper for start_shaking."""
        return self._run_async(self.start_shaking_async(speed, **kwargs))
    
    def stop_shaking(self) -> bool:
        """Synchronous wrapper for stop_shaking."""
        return self._run_async(self.stop_shaking_async())
    
    def wait_for_temperature(self) -> bool:
        """Synchronous wrapper for wait_for_temperature."""
        return self._run_async(self.wait_for_temperature_async())
    
    def get_temperature_controller_state(self) -> Optional[dict]:
        """Synchronous wrapper for get_temperature_controller_state."""
        return self._run_async(self.get_temperature_controller_state_async())
    
    def get_temperature_error(self) -> Optional[str]:
        """Synchronous wrapper for get_temperature_error."""
        return self._run_async(self.get_temperature_error_async())
    
    def get_heating_state(self) -> Optional[bool]:
        """Synchronous wrapper for get_heating_state."""
        return self._run_async(self.get_heating_state_async())
    
    def shutdown(self) -> bool:
        """Synchronous wrapper for shutdown."""
        result = self._run_async(self.shutdown_async())
        if self._loop:
            self._loop.close()
            self._loop = None
        return result
    
    def _generate_command_id(self) -> int:
        """Generate unique command ID."""
        self._command_id += 1
        if self._command_id > 9999:
            self._command_id = 1
        return self._command_id
    
    async def _send_hhs_command(self, command: str, **kwargs) -> dict:
        """
        Send Hamilton HHS command and parse response.
        
        Args:
            command: Command code (TA, SB, SC, etc.)
            **kwargs: Command arguments
            
        Returns:
            Parsed response dictionary
        """
        if not self.is_connected:
            raise RuntimeError("Device not connected")
        
        # Build command string with proper addressing
        cmd_id = self._generate_command_id()
        cmd_str = HHSCommands.build_command(
            index=self.device_index,
            command=command,
            command_id=cmd_id,
            interface_type=self.interface.value,  # "usb" or "rs232"
            **kwargs
        )
        
        # Send command
        response = await self.comm_interface.send_command(cmd_str)
        
        # Parse response
        return HHSCommands.parse_response(response or "")
    
    async def initialize_async(self, temperature: float = 25.0) -> bool:
        """
        Initialize the heater shaker and set initial temperature.
        
        Args:
            temperature: Initial target temperature in Celsius
            
        Returns:
            bool: True if initialization successful
        """
        try:
            # Validate temperature
            if not (self.min_temperature <= temperature <= self.max_temperature):
                raise ValueError(f"Temperature must be between {self.min_temperature}°C and {self.max_temperature}°C")
            
            # Setup communication interface
            if self.interface == InterfaceType.RS232:
                self.comm_interface = SerialInterface(self.port)
            elif self.interface == InterfaceType.USB:
                self.comm_interface = USBInterface()
            else:
                raise ValueError(f"Unsupported interface: {self.interface}")
            
            # Connect to device
            if not await self.comm_interface.connect():
                raise ConnectionError("Failed to connect to device")
            
            self.is_connected = True  # Set connected state before sending commands
            
            # Initialize device systems (from PyLabRobot)
            if not await self._initialize_lock():
                raise RuntimeError("Failed to initialize lock system")
            
            if not await self._initialize_shaker():
                raise RuntimeError("Failed to initialize shaker drive")
            
            # Set initial temperature
            if not await self.set_temperature_async(temperature):
                raise RuntimeError("Failed to set initial temperature")
            
            self.is_initialized = True
            self.logger.info(f"Heater shaker '{self.name}' initialized at {temperature}°C")
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            self.is_initialized = False
            self.is_connected = False
            return False
    
    async def heat_shake_async(self, 
                        time: float, 
                        temperature: float, 
                        speed: float,
                        direction: int = 0,
                        acceleration: int = 1000,
                        wait_for_temperature: bool = True) -> bool:
        """
        Heat and shake for specified duration.
        
        Args:
            time: Duration in seconds
            temperature: Target temperature in Celsius
            speed: Shaking speed in steps/second (20-2000)
            direction: Shaking direction (0=positive, 1=negative)
            acceleration: Acceleration in increments/second (500-10000)
            wait_for_temperature: Wait for temperature stabilization
            
        Returns:
            bool: True if successful
        """
        if not self.is_initialized:
            raise RuntimeError("Device not initialized")
        
        # Validate parameters
        self._validate_temperature(temperature)
        self._validate_speed(speed)
        self._validate_acceleration(acceleration)
        
        if time <= 0:
            raise ValueError("Time must be positive")
        if direction not in [0, 1]:
            raise ValueError("Direction must be 0 (positive) or 1 (negative)")
        
        try:
            self.logger.info(f"Starting heat-shake: {temperature}°C, {speed} steps/sec, direction {direction}")
            
            # Set temperature
            if not await self.set_temperature_async(temperature):
                return False
            
            # Wait for temperature if requested
            if wait_for_temperature:
                if not await self._wait_for_temperature(temperature):
                    self.logger.warning("Temperature stabilization timeout")
            
            # Lock plate (required for shaking)
            if not await self.lock_plate():
                return False
            
            # Start shaking with specified parameters
            if not await self.start_shaking_async(speed=speed, direction=direction, acceleration=acceleration):
                return False
            
            # Run for specified time
            await asyncio.sleep(time)
            
            # Stop shaking
            await self.stop_shaking_async()
            
            self.logger.info("Heat-shake protocol completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Heat-shake failed: {e}")
            await self.stop_shaking_async()  # Safety stop
            return False
    
    async def set_temperature_async(self, temperature: float) -> bool:
        """Set heater temperature."""
        self._validate_temperature(temperature)
        
        # Format temperature according to Hamilton protocol (temp * 10 as 4-digit string)
        temp_str = HHSCommands.format_temperature(temperature)
        
        response = await self._send_hhs_command(
            HHSCommands.SET_TEMPERATURE, 
            ta=temp_str  # Hamilton uses 'ta' parameter
        )
        
        if response['success']:
            self.logger.info(f"Temperature set to {temperature}°C")
            return True
        
        self.logger.error(f"Failed to set temperature: {response.get('error', 'Unknown error')}")
        return False
    
    async def get_temperature_async(self) -> Optional[float]:
        """Get current middle temperature reading."""
        response = await self._send_hhs_command(HHSCommands.GET_TEMPERATURE)
        
        if response['success']:
            # Parse Hamilton temperature response
            temp_data = HHSCommands.parse_temperature_response(response['raw_response'])
            
            if temp_data['success'] and temp_data['middle'] is not None:
                temperature = temp_data['middle']
                self.current_temperature = temperature
                return temperature
        
        self.logger.error(f"Failed to get temperature: {response.get('error', 'Unknown error')}")
        return None
    
    async def get_edge_temperature(self) -> Optional[float]:
        """Get current edge temperature reading."""
        response = await self._send_hhs_command(HHSCommands.GET_TEMPERATURE)
        
        if response['success']:
            temp_data = HHSCommands.parse_temperature_response(response['raw_response'])
            
            if temp_data['success'] and temp_data['edge'] is not None:
                return temp_data['edge']
        
        return None
    
    async def start_shaking_async(self, 
                           speed: float = 800,
                           direction: int = 0,
                           acceleration: int = 1000) -> bool:
        """
        Start shaking with specified parameters.
        
        Args:
            speed: Speed in steps/second (20-2000)
            direction: Direction (0=positive, 1=negative)
            acceleration: Acceleration in increments/second (500-10000)
        """
        self._validate_speed(speed)
        self._validate_acceleration(acceleration)
        
        if direction not in [0, 1]:
            raise ValueError("Direction must be 0 or 1")
        
        # Format parameters according to Hamilton protocol
        speed_str = HHSCommands.format_speed(int(speed))
        accel_str = HHSCommands.format_acceleration(acceleration)
        
        # First ensure plate is locked
        if not await self.lock_plate():
            self.logger.error("Failed to lock plate - cannot start shaking")
            return False
        
        # Start shaking with Hamilton SB command
        response = await self._send_hhs_command(
            HHSCommands.START_SHAKING,
            st=direction,      # direction
            sv=speed_str,      # speed (4-digit)
            sr=accel_str       # acceleration (5-digit)
        )
        
        if response['success']:
            # Verify shaking started
            if await self.get_is_shaking():
                self.is_shaking = True
                self.current_speed = speed
                self.logger.info(f"Shaking started: {speed} steps/sec, direction {direction}")
                return True
            else:
                self.logger.error("Shaking command sent but device not shaking")
                return False
        
        self.logger.error(f"Failed to start shaking: {response.get('error', 'Unknown error')}")
        return False
    
    async def stop_shaking_async(self) -> bool:
        """Stop shaking."""
        # Send stop command
        response = await self._send_hhs_command(HHSCommands.STOP_SHAKING)
        
        if response['success']:
            # Wait for stop to complete
            await self._send_hhs_command(HHSCommands.WAIT_FOR_STOP)
            
            self.is_shaking = False
            self.logger.info("Shaking stopped")
            return True
        
        self.logger.error(f"Failed to stop shaking: {response.get('error', 'Unknown error')}")
        return False
    
    async def get_is_shaking(self) -> bool:
        """Check if device is currently shaking."""
        response = await self._send_hhs_command(HHSCommands.GET_SHAKING_STATUS)
        
        if response['success']:
            return HHSCommands.parse_shaking_response(response['raw_response'])
        
        return False
    
    async def lock_plate(self) -> bool:
        """Lock the plate for shaking."""
        response = await self._send_hhs_command(
            HHSCommands.LOCK_PLATE,
            lp=1  # 1 = locked
        )
        
        if response['success']:
            self.logger.info("Plate locked")
            return True
        
        self.logger.error("Failed to lock plate")
        return False
    
    async def unlock_plate(self) -> bool:
        """Unlock the plate."""
        response = await self._send_hhs_command(
            HHSCommands.LOCK_PLATE,
            lp=0  # 0 = unlocked
        )
        
        if response['success']:
            self.logger.info("Plate unlocked")
            return True
        
        self.logger.error("Failed to unlock plate")
        return False
    
    async def wait_for_temperature_async(self) -> bool:
        """Wait until target temperature is reached (TW command)."""
        response = await self._send_hhs_command("TW")
        
        if response['success']:
            self.logger.info("Temperature target reached")
            return True
        else:
            # Handle specific temperature errors from manual
            error_code = response.get('error_code', 'Unknown')
            if error_code == "61":
                self.logger.error("Temperature timeout - target not reached in time")
            elif error_code == "62":
                self.logger.error("Temperature out of supervision range")
            elif error_code == "63":
                self.logger.error("Temperature out of security range - heating disabled")
            elif error_code == "64":
                self.logger.error("Temperature sensor error - no connection to sensors or sensor difference too big")
            else:
                self.logger.error(f"Temperature wait failed: {response.get('error', 'Unknown error')}")
            return False
    
    async def start_temperature_with_wait(self, temperature: float, **kwargs) -> bool:
        """Start temperature controller and wait for target (TB command)."""
        self._validate_temperature(temperature)
        
        temp_str = HHSCommands.format_temperature(temperature)
        
        response = await self._send_hhs_command(
            "TB",  # Start temperature with wait
            ta=temp_str,
            **kwargs
        )
        
        if response['success']:
            self.logger.info(f"Temperature reached {temperature}°C")
            return True
        else:
            error_code = response.get('error_code', 'Unknown')
            if error_code == "61":
                self.logger.error("Temperature timeout during startup")
            elif error_code == "62":
                self.logger.error("Temperature supervision failed")
            elif error_code == "63":
                self.logger.error("Temperature security range violation")
            elif error_code == "64":
                self.logger.error("Temperature sensor error - no connection to sensors or sensor difference too big")
            else:
                self.logger.error(f"Temperature control failed: {response.get('error', 'Unknown error')}")
            return False
    
    async def get_temperature_controller_state_async(self) -> Optional[dict]:
        """Get temperature controller state (QC command)."""
        response = await self._send_hhs_command("QC")
        
        if response['success']:
            try:
                # Parse QC response: look for 'qc' token followed by three values
                # Format: ...er00qc1 128 0 (control_state pwm_value supervision_state)
                if 'qc' in response['raw_response']:
                    qc_part = response['raw_response'].split('qc')[1]
                    parts = qc_part.split()
                    
                    if len(parts) >= 3:
                        return {
                            'control_active': parts[0] == '1',
                            'pwm_value': int(parts[1]),
                            'supervision_state': int(parts[2])
                        }
            except Exception as e:
                self.logger.error(f"Error parsing controller state: {e}")
        
        return None
    
    async def get_temperature_error_async(self) -> Optional[str]:
        """Get last temperature error code (QE command)."""
        response = await self._send_hhs_command("QE")
        
        if response['success']:
            # Extract error code from qe## response
            if 'qe' in response['raw_response']:
                error_code = response['raw_response'].split('qe')[1][:2]
                return error_code
        
        return None
    
    async def get_heating_state_async(self) -> Optional[bool]:
        """Get heating up state (QD command)."""
        response = await self._send_hhs_command("QD")
        
        if response['success']:
            return 'qd1' in response['raw_response']
        
        return None
    
    async def deactivate_heating(self) -> bool:
        """Turn off heating (Hamilton TO command)."""
        response = await self._send_hhs_command(HHSCommands.DEACTIVATE_HEATING)
        
        if response['success']:
            self.logger.info("Heating deactivated")
            return True
        
        return False
    
    async def _initialize_lock(self) -> bool:
        """Initialize the lock system (Hamilton LI command)."""
        response = await self._send_hhs_command(HHSCommands.INITIALIZE_LOCK)
        return response['success']
    
    async def _initialize_shaker(self) -> bool:
        """Initialize the shaker drive (Hamilton SI command)."""
        response = await self._send_hhs_command(HHSCommands.INITIALIZE_SHAKER)
        return response['success']
    
    async def _wait_for_temperature(self, 
                                   target_temp: float, 
                                   tolerance: float = 1.0, 
                                   timeout: float = 300.0) -> bool:
        """Wait for temperature to stabilize."""
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            current_temp = await self.get_temperature_async()
            
            if current_temp is not None:
                if abs(current_temp - target_temp) <= tolerance:
                    self.logger.info(f"Temperature stabilized at {current_temp:.1f}°C")
                    return True
                
                self.logger.debug(f"Current: {current_temp:.1f}°C, Target: {target_temp:.1f}°C")
            
            await asyncio.sleep(5)
        
        return False
    
    def _validate_temperature(self, temperature: float):
        """Validate temperature parameter."""
        if not (self.min_temperature <= temperature <= self.max_temperature):
            raise ValueError(f"Temperature must be between {self.min_temperature}°C and {self.max_temperature}°C")
    
    def _validate_speed(self, speed: float):
        """Validate speed parameter (increments/second per Hamilton manual)."""
        if not (self.min_speed <= speed <= self.max_speed):
            raise ValueError(f"Speed must be between {self.min_speed} and {self.max_speed} increments/second")
    
    def _validate_acceleration(self, acceleration: int):
        """Validate acceleration parameter."""
        if not (self.min_acceleration <= acceleration <= self.max_acceleration):
            raise ValueError(f"Acceleration must be between {self.min_acceleration} and {self.max_acceleration}")
    
    async def shutdown_async(self) -> bool:
        """Safely shutdown the device."""
        if not self.is_initialized:
            return True
        
        try:
            # Stop shaking
            await self.stop_shaking_async()
            
            # Turn off heating (Hamilton command)
            await self.deactivate_heating()
            
            # Unlock plate
            await self.unlock_plate()
            
            # Disconnect
            if self.comm_interface:
                await self.comm_interface.disconnect()
            
            self.is_initialized = False
            self.logger.info("Device shutdown complete")
            return True
            
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            return False
    
    @staticmethod
    def list_available_ports():
        """List available serial ports."""
        if not SERIAL_AVAILABLE:
            print("pyserial not available")
            return []
        
        ports = serial.tools.list_ports.comports()
        available_ports = []
        
        for port in ports:
            available_ports.append({
                'device': port.device,
                'description': port.description,
                'manufacturer': port.manufacturer or 'Unknown'
            })
        
        return available_ports
    
    def __repr__(self) -> str:
        """String representation."""
        status = "initialized" if self.is_initialized else "not initialized"
        return f"HeaterShaker(name='{self.name}', interface='{self.interface.value}', port='{self.port}', status={status})"


# Test functions for debugging
def test_commands():
    """Test command building and parsing."""
    print("Testing Hamilton HHS command protocol...")
    
    # Test command building
    cmd1 = HHSCommands.build_command(1, "TA", 123, ta="0370")
    print(f"Set temperature command: {cmd1}")
    
    cmd2 = HHSCommands.build_command(1, "SB", 124, st=0, sv="0800", sr="01000")
    print(f"Start shaking command: {cmd2}")
    
    # Test temperature formatting
    temp_str = HHSCommands.format_temperature(37.5)
    print(f"Temperature 37.5°C formatted: {temp_str}")
    
    # Test response parsing
    test_response = "T1RTid0001rt+0370 +0365"
    temp_data = HHSCommands.parse_temperature_response(test_response)
    print(f"Parsed temperature: {temp_data}")


if __name__ == "__main__":
    print("Hamilton Heater Shaker Controller")
    print("=" * 40)
    
    # Check dependencies
    print("Dependencies:")
    print(f"  pyserial: {'✓ Available' if SERIAL_AVAILABLE else '✗ Not installed'}")
    print(f"  pyusb: {'✓ Available' if USB_AVAILABLE else '✗ Not installed'}")
    
    # Test command protocol
    print("\nCommand Protocol Test:")
    test_commands()
    
    print("\nTo use this module:")
    print("1. Import: from heater_shaker import HeaterShaker")
    print("2. Create: hs = HeaterShaker(port='COM3')")
    print("3. Use: hs.initialize(temp=25.0)")
    print("4. Run: hs.heat_shake(300, 37.0, 800)")
    print("5. Stop: hs.shutdown()")
    
    print("\nReal Hamilton HHS command examples:")
    print("- Set 37°C: T1TAid0001ta0370")
    print("- Start shake: T1SBid0002st0sv0800sr01000")
    print("- Get temp: T1RTid0003")
    print("- Stop shake: T1SCid0004")
    print("- Lock plate: T1LPid0005lp1")
    
    print(f"\nUSB Device ID: {HHSCommands.USB_VENDOR_ID:04X}:{HHSCommands.USB_PRODUCT_ID:04X}")