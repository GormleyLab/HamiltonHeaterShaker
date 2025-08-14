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
    def build_command(index: int, command: str, command_id: int, **kwargs) -> str:
        """
        Build Hamilton HHS command string.
        
        Args:
            index: Device index (1-8 for box, 1-2 for STAR)
            command: Command code (TA, SB, SC, etc.)
            command_id: Unique command ID
            **kwargs: Command arguments
            
        Returns:
            Complete command string
            
        Examples:
            build_command(1, "TA", 123, ta="0370") -> "T1TAid0123ta0370"  # 37.0°C
            build_command(1, "SB", 124, st=0, sv="0800", sr="01000") -> "T1SBid0124st0sv0800sr01000"
        """
        args = "".join([f"{key}{value}" for key, value in kwargs.items()])
        id_str = str(command_id).zfill(4)
        return f"T{index}{command}id{id_str}{args}"
    
    @staticmethod
    def format_temperature(temp_celsius: float) -> str:
        """Format temperature for Hamilton protocol (temp * 10 as 4-digit string)."""
        return f"{round(10 * temp_celsius):04d}"
    
    @staticmethod
    def format_speed(speed_steps_per_sec: int) -> str:
        """Format speed for Hamilton protocol (4-digit zero-padded)."""
        return f"{speed_steps_per_sec:04d}"
    
    @staticmethod
    def format_acceleration(accel: int) -> str:
        """Format acceleration for Hamilton protocol (5-digit zero-padded)."""
        return f"{accel:05d}"
    
    @staticmethod
    def parse_temperature_response(response: str) -> dict:
        """
        Parse Hamilton temperature response.
        
        Response format: "...rt+0370 +0365..." (middle temp, edge temp in tenths of degrees)
        """
        result = {'middle': None, 'edge': None, 'success': False}
        
        try:
            if 'rt' in response:
                temp_part = response.split('rt')[1]
                temps = temp_part.split()
                
                if len(temps) >= 2:
                    middle_temp = float(temps[0].strip('+')) / 10
                    edge_temp = float(temps[1].strip('+')) / 10
                    
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
        """Parse shaking status response (returns True if shaking)."""
        return response.endswith("1")
    
    @staticmethod
    def parse_response(response: str) -> dict:
        """
        Parse general Hamilton HHS response string.
        
        Returns:
            Dictionary with parsed response data
        """
        result = {
            'success': False,
            'command_id': None,
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
            
            # Most Hamilton responses are successful if we get a response
            # Specific error handling would need more protocol analysis
            if response and len(response) > 0:
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
            self.is_connected = True
            logging.info(f"Connected to USB device {self.vendor_id:04X}:{self.product_id:04X}")
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
            # USB endpoint configuration (typical values)
            out_endpoint = 0x02
            in_endpoint = 0x81
            
            # Send command as ASCII string
            command_bytes = (command + '\r\n').encode('ascii')
            self.device.write(out_endpoint, command_bytes)
            logging.debug(f"USB sent: {command}")
            
            # Read response
            response_bytes = self.device.read(in_endpoint, 64, timeout=2000)
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
        
        # Device specifications (from PyLabRobot analysis)
        self.max_temperature = 105.0  # °C
        self.min_temperature = 0.1    # °C (must be > 0)
        self.max_speed = 2000         # steps/second (from PyLabRobot: 20-2000)
        self.min_speed = 20           # steps/second
        self.min_acceleration = 500   # increments/second
        self.max_acceleration = 10000 # increments/second
        
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
        if not self.is_initialized:
            raise RuntimeError("Device not initialized")
        
        # Build command string
        cmd_id = self._generate_command_id()
        cmd_str = HHSCommands.build_command(
            index=self.device_index,
            command=command,
            command_id=cmd_id,
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
        await self.lock_plate()
        
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
        """Validate speed parameter (steps/second)."""
        if not (self.min_speed <= speed <= self.max_speed):
            raise ValueError(f"Speed must be between {self.min_speed} and {self.max_speed} steps/second")
    
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