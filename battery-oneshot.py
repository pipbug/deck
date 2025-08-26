#!/usr/bin/env python3
"""
Battery Oneshot with MAX Chip Reset
Detects bad readings and resets MAX17048 fuel gauge when needed
"""

import time
import smbus
import json
import os
from pathlib import Path

# Configuration
RESET_COOLDOWN = 300  # 5 minutes between resets
BAD_READING_THRESHOLD = 1  # Reset after 1 bad reading
STATE_FILE = '/tmp/battery_reset_state.json'

class BatteryMonitor:
    def __init__(self):
        self.bus = smbus.SMBus(3)
        self.chip_address = 0x36
        self.load_state()
        
        # Note: No longer configuring alert threshold since we use layered monitoring
    
    def check_current_alert_threshold(self):
        """Check current alert threshold setting with detailed debugging"""
        try:
            # Read config register (0x0C)
            config_raw = self.bus.read_word_data(self.chip_address, 0x0C)
            config_swapped = (config_raw & 0xFF) << 8 | (config_raw >> 8)
            
            # Alert threshold is in upper 8 bits
            alert_threshold_reg = config_swapped >> 8
            
            # Convert register value to percentage using MAX17048 formula
            # Formula: percentage = 32 - (register_value / 2)
            raw_percentage = 32 - (alert_threshold_reg / 2)
            user_percentage = (raw_percentage - 15) * 1.176470588
            
            print(f"=== ALERT THRESHOLD DEBUG ===")
            print(f"Raw register read: 0x{config_raw:04X}")
            print(f"Swapped config: 0x{config_swapped:04X}")
            print(f"Alert threshold register: 0x{alert_threshold_reg:02X} ({alert_threshold_reg})")
            print(f"Calculated raw percentage: {raw_percentage}%")
            print(f"Calculated user percentage: {user_percentage:.1f}%")
            print(f"=============================")
            
            return {
                'register_value': alert_threshold_reg,
                'raw_percentage': raw_percentage,
                'user_percentage': user_percentage,
                'config_raw': config_raw,
                'config_swapped': config_swapped
            }
            
        except Exception as e:
            print(f"Failed to read alert threshold: {e}")
            return None
    
    def configure_alert_threshold(self):
        """Configure MAX17048 alert threshold to 15% (0% user)"""
        try:
            # Check current setting first
            current = self.check_current_alert_threshold()
            if current and abs(current['raw_percentage'] - 15.0) < 0.1:
                print("Alert threshold already correctly set")
                return True
            
            # Alert threshold register is 0x0D
            # Alert threshold = (32 - alert_percent) * 2
            # For 15% raw (0% user): (32 - 15) * 2 = 34 = 0x22
            alert_threshold = 0x22  # 15% raw percentage
            
            # Read current config register (0x0C) to preserve other settings
            config_raw = self.bus.read_word_data(self.chip_address, 0x0C)
            config_swapped = (config_raw & 0xFF) << 8 | (config_raw >> 8)
            
            # Set alert threshold in upper 8 bits, preserve lower 8 bits
            new_config = (alert_threshold << 8) | (config_swapped & 0xFF)
            
            # Swap bytes for write (SMBus is little endian)
            config_to_write = ((new_config & 0xFF) << 8) | (new_config >> 8)
            
            # Write new configuration
            self.bus.write_word_data(self.chip_address, 0x0C, config_to_write)
            
            # Verify the setting
            time.sleep(0.1)
            verify_raw = self.bus.read_word_data(self.chip_address, 0x0C)
            verify_swapped = (verify_raw & 0xFF) << 8 | (verify_raw >> 8)
            set_threshold = verify_swapped >> 8
            
            if set_threshold == alert_threshold:
                print(f"Alert threshold configured successfully: 15% raw (0% user)")
                return True
            else:
                print(f"Alert threshold verification failed: got 0x{set_threshold:02X}, expected 0x{alert_threshold:02X}")
                return False
                
        except Exception as e:
            print(f"Failed to configure alert threshold: {e}")
            return False
    
    def load_state(self):
        """Load persistent state"""
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            self.last_reset = state.get('last_reset', 0)
            self.bad_reading_count = state.get('bad_reading_count', 0)
            self.last_voltage = state.get('last_voltage', 0)
        except (FileNotFoundError, json.JSONDecodeError):
            self.last_reset = 0
            self.bad_reading_count = 0
            self.last_voltage = 0
    
    def save_state(self):
        """Save persistent state"""
        state = {
            'last_reset': self.last_reset,
            'bad_reading_count': self.bad_reading_count,
            'last_voltage': self.last_voltage
        }
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            os.chmod(STATE_FILE, 0o644)
        except:
            pass
    
    def read_raw_data(self):
        """Read raw data from MAX chip"""
        vcell_raw = self.bus.read_word_data(self.chip_address, 0x02)
        soc_raw = self.bus.read_word_data(self.chip_address, 0x04)
        
        voltage = ((vcell_raw & 0xFF) << 8 | (vcell_raw >> 8)) * 0.000078125
        soc_swapped = (soc_raw & 0xFF) << 8 | (soc_raw >> 8)
        raw_percent = (soc_swapped >> 8) + (soc_swapped & 0xFF) / 256.0
        user_percent = min(100.0, max(0.0, (raw_percent - 15) * 1.176470588))
        
        return voltage, raw_percent, user_percent
    
    def is_bad_reading(self, voltage, raw_percent, user_percent):
        """Detect obviously incorrect readings from MAX chip with voltage validation"""
        current_time = time.time()
        
        # Enhanced voltage validation for layered approach
        if voltage > 4.3 or voltage < 2.5:
            return True, f"Impossible voltage: {voltage:.3f}V"
        
        # Check for impossible voltage/percentage combinations
        if voltage > 4.0 and user_percent < 30:
            return True, "High voltage, low percentage"
        
        if voltage < 3.5 and user_percent > 70:
            return True, "Low voltage, high percentage"
        
        # Additional voltage-percentage correlation checks
        if voltage > 3.8 and user_percent < 10:
            return True, f"Voltage too high for percentage: {voltage:.3f}V at {user_percent:.1f}%"
        
        if voltage < 3.3 and user_percent > 50:
            return True, f"Voltage too low for percentage: {voltage:.3f}V at {user_percent:.1f}%"
        
        # Check for sudden voltage jumps (> 0.5V change)
        if self.last_voltage > 0:
            voltage_change = abs(voltage - self.last_voltage)
            if voltage_change > 0.5:
                return True, f"Sudden voltage change: {voltage_change:.3f}V"
        
        # Check for percentage out of reasonable bounds
        if raw_percent < 0 or raw_percent > 110:
            return True, f"Raw percentage out of bounds: {raw_percent:.1f}%"
        
        # Check for impossible user percentage (should be 0-100)
        if user_percent < -5 or user_percent > 105:
            return True, f"User percentage out of bounds: {user_percent:.1f}%"
        
        return False, ""
    
    def reset_max_chip(self):
        """Reset MAX17048 fuel gauge chip"""
        current_time = time.time()
        
        # Check cooldown period
        if current_time - self.last_reset < RESET_COOLDOWN:
            return False, "Reset cooldown active"
        
        try:
            # MAX17048 reset command: write 0x5400 to register 0x0C
            # Note: word data is sent LSB first, so 0x5400 becomes 0x0054
            self.bus.write_word_data(self.chip_address, 0x0C, 0x0054)
            
            # Wait for reset to complete
            time.sleep(0.5)
            
            # Update state
            self.last_reset = current_time
            self.bad_reading_count = 0
            self.save_state()
            
            return True, "MAX chip reset successful"
        
        except Exception as e:
            return False, f"Reset failed: {e}"
    
    def read_battery_with_validation(self):
        """Read battery with bad reading detection and reset capability"""
        try:
            voltage, raw_percent, user_percent = self.read_raw_data()
            
            # Check if reading is bad
            is_bad, reason = self.is_bad_reading(voltage, raw_percent, user_percent)
            
            if is_bad:
                self.bad_reading_count += 1
                
                # Reset chip if we've had too many bad readings
                if self.bad_reading_count >= BAD_READING_THRESHOLD:
                    reset_success, reset_msg = self.reset_max_chip()
                    
                    if reset_success:
                        # Try reading again after reset
                        time.sleep(1)
                        voltage, raw_percent, user_percent = self.read_raw_data()
                        
                        # Create status with reset info
                        status = self.create_status(voltage, raw_percent, user_percent)
                        status['reset_info'] = {
                            'reset_performed': True,
                            'reason': reason,
                            'message': reset_msg,
                            'timestamp': int(time.time())
                        }
                    else:
                        # Reset failed, create status with error
                        status = self.create_status(voltage, raw_percent, user_percent)
                        status['reset_info'] = {
                            'reset_attempted': True,
                            'reset_failed': True,
                            'reason': reason,
                            'error': reset_msg,
                            'timestamp': int(time.time())
                        }
                else:
                    # Bad reading but not ready to reset yet
                    status = self.create_status(voltage, raw_percent, user_percent)
                    status['validation'] = {
                        'bad_reading': True,
                        'reason': reason,
                        'bad_count': self.bad_reading_count,
                        'threshold': BAD_READING_THRESHOLD
                    }
            else:
                # Good reading - reset bad count
                if self.bad_reading_count > 0:
                    self.bad_reading_count = 0
                    self.save_state()
                
                status = self.create_status(voltage, raw_percent, user_percent)
                status['validation'] = {'reading_ok': True}
            
            # Update last voltage for next comparison
            self.last_voltage = voltage
            self.save_state()
            
            return status
            
        except Exception as e:
            # I2C communication error
            return self.create_error_status(f"I2C error: {e}")
    
    def create_status(self, voltage, raw_percent, user_percent):
        """Create standard battery status with layered monitoring support"""
        status = {
            'battery': {
                'voltage': round(voltage, 3),
                'percent_user': round(user_percent, 1),
                'percent_raw': round(raw_percent, 1),
                'timestamp': int(time.time())
            },
            'last_updated': time.strftime('%H:%M:%S')
        }
        
        # Add voltage-based condition flags for layered monitoring
        if voltage <= 3.2 and voltage > 0:
            status['battery']['voltage_critical'] = True
        elif voltage <= 3.5 and voltage > 0:
            status['battery']['voltage_warning'] = True
        
        # Add percentage-based condition flags
        if user_percent <= 2:
            status['battery']['percentage_critical'] = True
        elif user_percent <= 5:
            status['battery']['percentage_warning'] = True
        
        return status
    
    def create_error_status(self, error_msg):
        """Create error status"""
        return {
            'battery': {
                'voltage': 0,
                'percent_user': 0,
                'percent_raw': 0,
                'timestamp': int(time.time()),
                'error': error_msg
            },
            'last_updated': time.strftime('%H:%M:%S')
        }
    
    def write_status(self, status):
        """Write status to file"""
        try:
            with open('/tmp/battery_status.json', 'w') as f:
                json.dump(status, f, separators=(',', ':'))
            os.chmod('/tmp/battery_status.json', 0o644)
            return True
        except Exception as e:
            return False


def manual_reset():
    """Manual reset command for testing"""
    try:
        monitor = BatteryMonitor()
        success, message = monitor.reset_max_chip()
        print(f"Manual reset: {message}")
        return success
    except Exception as e:
        print(f"Manual reset failed: {e}")
        return False


def dump_all_registers():
    """Dump all MAX17048 registers for debugging"""
    try:
        monitor = BatteryMonitor()
        print("=== MAX17048 REGISTER DUMP ===")
        
        registers = {
            0x02: "VCELL",
            0x04: "SOC", 
            0x06: "MODE",
            0x08: "VERSION",
            0x0A: "HIBRT",
            0x0C: "CONFIG",
            0x0E: "VALRT",
            0x10: "CRATE",
            0x12: "VRESET",
            0x14: "STATUS",
            0x16: "TABLE"
        }
        
        for addr, name in registers.items():
            try:
                raw_value = monitor.bus.read_word_data(monitor.chip_address, addr)
                swapped = (raw_value & 0xFF) << 8 | (raw_value >> 8)
                print(f"0x{addr:02X} {name:8}: raw=0x{raw_value:04X}, swapped=0x{swapped:04X}")
            except Exception as e:
                print(f"0x{addr:02X} {name:8}: ERROR - {e}")
        
        print("==============================")
        return True
        
    except Exception as e:
        print(f"Failed to dump registers: {e}")
        return False


def test_alert_values():
    """Test different alert threshold values"""
    try:
        monitor = BatteryMonitor()
        
        # Test different threshold values
        test_values = [0x00, 0x10, 0x20, 0x22, 0x30, 0x40]
        
        print("=== TESTING ALERT THRESHOLDS ===")
        
        for test_val in test_values:
            try:
                # Read current config
                config_raw = monitor.bus.read_word_data(monitor.chip_address, 0x0C)
                config_swapped = (config_raw & 0xFF) << 8 | (config_raw >> 8)
                
                # Set new threshold in upper 8 bits
                new_config = (test_val << 8) | (config_swapped & 0xFF)
                config_to_write = ((new_config & 0xFF) << 8) | (new_config >> 8)
                
                # Write and verify
                monitor.bus.write_word_data(monitor.chip_address, 0x0C, config_to_write)
                time.sleep(0.1)
                
                # Read back
                verify_raw = monitor.bus.read_word_data(monitor.chip_address, 0x0C)
                verify_swapped = (verify_raw & 0xFF) << 8 | (verify_raw >> 8)
                actual_threshold = verify_swapped >> 8
                
                # Calculate percentages
                raw_pct = 32 - (actual_threshold / 2)
                user_pct = (raw_pct - 15) * 1.176470588
                
                print(f"Set 0x{test_val:02X} -> Got 0x{actual_threshold:02X} -> {raw_pct}% raw -> {user_pct:.1f}% user")
                
            except Exception as e:
                print(f"Error testing 0x{test_val:02X}: {e}")
        
        print("=================================")
        return True
        
    except Exception as e:
        print(f"Failed to test alert values: {e}")
        return False


def main():
    """Main execution"""
    import sys
    
    # Handle commands
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == 'reset':
            manual_reset()
            return
        elif command == 'dump-registers':
            dump_all_registers()
            return
        elif command == 'test-alert':
            test_alert_values()
            return
        else:
            print("Available commands:")
            print("  reset          - Reset fuel gauge chip")
            print("  dump-registers - Show all MAX17048 registers")
            print("  test-alert     - Test different alert threshold values")
            return
    
    # Normal battery reading
    monitor = BatteryMonitor()
    status = monitor.read_battery_with_validation()
    
    if monitor.write_status(status):
        # Print status for debugging (optional)
        if 'reset_info' in status:
            print(f"Battery monitor: {status['reset_info'].get('message', 'Reset performed')}")
        elif status.get('validation', {}).get('bad_reading'):
            val = status['validation']
            print(f"Bad reading {val['bad_count']}/{val['threshold']}: {val['reason']}")
    else:
        print("Failed to write battery status")


if __name__ == "__main__":
    main()
