"""
main.py - Test program for Hamilton Heater Shaker

This program demonstrates how to use the HeaterShaker class for automation.
Modify the parameters below to test different protocols.
"""

from heater_shaker import HeaterShaker
import time


def main():
    """Main test function for heater shaker operations."""
    
    # Protocol parameters - modify these for your test
    initial_temp = 25.0    # Initial temperature in Celsius
    time_seconds = 300     # Protocol time in seconds (5 minutes)
    temperature = 37.0     # Target temperature in Celsius
    speed = 800           # Shaking speed in steps/second
    
    # Connection parameters - adjust for your setup
    port = "COM3"         # Windows: COM3, COM4, etc. | Linux: /dev/ttyUSB0, etc.
    interface = "rs232"   # "rs232" for STAR connection or "usb" for heater shaker box
    
    print("=" * 60)
    print("Hamilton Heater Shaker Test Program")
    print("=" * 60)
    print(f"Initial temperature: {initial_temp}°C")
    print(f"Protocol: {temperature}°C for {time_seconds} seconds at {speed} steps/sec")
    print(f"Connection: {interface.upper()} via {port}")
    print("=" * 60)
    
    # Create heater shaker instance
    print("Creating HeaterShaker instance...")
    try:
        heater_shaker = HeaterShaker(
            port=port,
            interface=interface,
            device_index=1,
            name="Test_HHS"
        )
    except Exception as e:
        print(f"ERROR: Failed to create HeaterShaker instance: {e}")
        print("Check that pyserial and/or pyusb are installed:")
        print("  pip install pyserial")
        print("  pip install pyusb")
        return False
    
    try:
        # Initialize the heater shaker
        print(f"\nInitializing heater shaker at {initial_temp}°C...")
        success = heater_shaker.initialize(temp=initial_temp)
        
        if not success:
            print("ERROR: Failed to initialize heater shaker")
            print("Check:")
            print("  - Device is connected and powered on")
            print("  - Correct port specified")
            print("  - No other programs using the device")
            return False
        
        print("✓ Heater shaker initialized successfully")
        
        # Get initial readings
        print("\nGetting initial device status...")
        current_temp = heater_shaker.get_temperature()
        if current_temp is not None:
            print(f"  Current temperature: {current_temp:.1f}°C")
        else:
            print("  Warning: Could not read temperature")
        
        # Run the heat-shake protocol
        print(f"\nStarting heat-shake protocol...")
        print(f"  Target: {temperature}°C")
        print(f"  Speed: {speed} steps/second")
        print(f"  Duration: {time_seconds} seconds ({time_seconds/60:.1f} minutes)")
        
        start_time = time.time()
        success = heater_shaker.heat_shake(time_seconds, temperature, speed)
        end_time = time.time()
        
        if success:
            actual_time = end_time - start_time
            print(f"✓ Heat-shake protocol completed successfully")
            print(f"  Actual duration: {actual_time:.1f} seconds")
        else:
            print("ERROR: Heat-shake protocol failed")
            return False
        
        # Get final readings
        print("\nGetting final device status...")
        final_temp = heater_shaker.get_temperature()
        if final_temp is not None:
            print(f"  Final temperature: {final_temp:.1f}°C")
        
        # Test individual controls
        print("\nTesting individual controls...")
        
        # Test temperature control
        test_temp = 30.0
        print(f"  Setting temperature to {test_temp}°C...")
        if heater_shaker.set_temperature(test_temp):
            print("  ✓ Temperature set successfully")
        else:
            print("  ✗ Failed to set temperature")
        
        # Test shaking control
        test_speed = 500
        print(f"  Starting shaking at {test_speed} steps/sec...")
        if heater_shaker.start_shaking(speed=test_speed):
            print("  ✓ Shaking started")
            time.sleep(5)  # Shake for 5 seconds
            
            if heater_shaker.stop_shaking():
                print("  ✓ Shaking stopped")
            else:
                print("  ✗ Failed to stop shaking")
        else:
            print("  ✗ Failed to start shaking")
        
        print("\n" + "=" * 60)
        print("Test completed successfully!")
        print("=" * 60)
        return True
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user (Ctrl+C)")
        return False
        
    except Exception as e:
        print(f"\nERROR: Unexpected error during test: {e}")
        print("Check device connection and try again.")
        return False
        
    finally:
        # Always shutdown properly
        print("\nShutting down heater shaker...")
        try:
            heater_shaker.shutdown()
            print("✓ Heater shaker shutdown complete")
        except Exception as e:
            print(f"Warning: Error during shutdown: {e}")


def test_connection_only():
    """Quick test to verify device connection without running protocol."""
    
    print("Testing device connection...")
    
    # List available ports
    print("\nAvailable serial ports:")
    try:
        ports = HeaterShaker.list_available_ports()
        if ports:
            for port in ports:
                print(f"  {port['device']}: {port['description']}")
        else:
            print("  No serial ports found")
    except Exception as e:
        print(f"  Error listing ports: {e}")
    
    # Try to connect
    print(f"\nTesting connection to heater shaker...")
    heater_shaker = HeaterShaker()
    
    try:
        success = heater_shaker.initialize(temp=25.0)
        if success:
            print("✓ Connection successful!")
            temp = heater_shaker.get_temperature()
            if temp is not None:
                print(f"  Current temperature: {temp:.1f}°C")
        else:
            print("✗ Connection failed")
    except Exception as e:
        print(f"✗ Connection error: {e}")
    finally:
        heater_shaker.shutdown()


if __name__ == "__main__":
    print("Hamilton Heater Shaker Test")
    print("1. Run full test protocol")
    print("2. Test connection only")
    print("3. Exit")
    
    choice = input("Enter choice (1-3): ").strip()
    
    if choice == "1":
        main()
    elif choice == "2":
        test_connection_only()
    elif choice == "3":
        print("Exiting...")
    else:
        print("Invalid choice. Running full test...")
        main()