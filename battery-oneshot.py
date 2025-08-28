#!/usr/bin/env python3
"""
MAX17043 Battery Monitor Script
Handles charging transitions, bad reading detection, and quick-start management
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import RPi.GPIO as GPIO

try:
    import smbus2 as smbus
except ImportError:
    import smbus

# Configuration constants
MAX17043_ADDRESS = 0x36
VCELL_REGISTER = 0x02
SOC_REGISTER = 0x04
MODE_REGISTER = 0x06
QUICK_START_COMMAND = 0x4000

CHARGE_DETECT_PIN = 4
STATUS_FILE = '/tmp/battery_status.json'
STATE_FILE = '/tmp/battery_monitor_state.json'

CHARGING_WINDOW_DURATION = 300  # 5 minutes in seconds
QUICK_START_COOLDOWN = 300  # 5 minutes in seconds
BAD_READING_THRESHOLD = 0.20  # 20% deviation threshold

# Standard Li-ion voltage curve (voltage -> expected SOC%)
LIION_VOLTAGE_CURVE = [
    (4.20, 100), (4.15, 95), (4.10, 90), (4.05, 85), (4.00, 80),
    (3.95, 75), (3.90, 70), (3.85, 65), (3.80, 60), (3.75, 55),
    (3.70, 50), (3.65, 45), (3.60, 40), (3.55, 35), (3.50, 30),
    (3.45, 25), (3.40, 20), (3.35, 15), (3.30, 10), (3.25, 5), (3.00, 0)
]


class BatteryMonitor:
    def __init__(self):
        self.logger = self._setup_logging()
        self.bus = None
        self.gpio_ready = False
        self.state = self._load_state()
        
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('/tmp/battery_monitor.log')
            ]
        )
        return logging.getLogger('BatteryMonitor')
    
    def _load_state(self) -> Dict[str, Any]:
        """Load persistent state from file"""
        try:
            if Path(STATE_FILE).exists():
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Could not load state file: {e}")
        
        return {
            'last_quick_start': 0,
            'charging_window_start': 0,
            'last_charger_state': False
        }
    
    def _save_state(self):
        """Save persistent state to file"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
        except OSError as e:
            self.logger.error(f"Could not save state file: {e}")
    
    def _init_i2c(self) -> bool:
        """Initialize I2C bus"""
        try:
            self.bus = smbus.SMBus(3)
            # Test communication by reading version register
            self.bus.read_word_data(MAX17043_ADDRESS, 0x08)
            return True
        except Exception as e:
            self.logger.error(f"I2C initialization failed: {e}")
            return False
    
    def _init_gpio(self) -> bool:
        """Initialize GPIO for charge detection"""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(CHARGE_DETECT_PIN, GPIO.IN)
            self.gpio_ready = True
            return True
        except Exception as e:
            self.logger.error(f"GPIO initialization failed: {e}")
            return False
    
    def _read_register16(self, register: int) -> Optional[int]:
        """Read 16-bit register from MAX17043"""
        try:
            if self.bus is None:
                return None
            data = self.bus.read_word_data(MAX17043_ADDRESS, register)
            # Swap bytes (SMBus returns little-endian, MAX17043 uses big-endian)
            return ((data & 0xFF) << 8) | ((data >> 8) & 0xFF)
        except Exception as e:
            self.logger.error(f"Failed to read register 0x{register:02X}: {e}")
            return None
    
    def _write_register16(self, register: int, value: int) -> bool:
        """Write 16-bit register to MAX17043"""
        try:
            if self.bus is None:
                return False
            # Swap bytes for big-endian format
            swapped = ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)
            self.bus.write_word_data(MAX17043_ADDRESS, register, swapped)
            return True
        except Exception as e:
            self.logger.error(f"Failed to write register 0x{register:02X}: {e}")
            return False
    
    def _is_charging(self) -> bool:
        """Check if charger is currently connected"""
        if not self.gpio_ready:
            return False
        try:
            return GPIO.input(CHARGE_DETECT_PIN) == GPIO.HIGH
        except Exception as e:
            self.logger.error(f"Failed to read charge detection pin: {e}")
            return False
    
    def _is_in_charging_window(self) -> bool:
        """Check if we're in a charging window period"""
        current_time = time.time()
        window_start = self.state.get('charging_window_start', 0)
        
        # If currently charging, we're always in window
        if self._is_charging():
            return True
            
        # If not charging, check if we're within window period after disconnect
        if window_start > 0:
            elapsed = current_time - window_start
            return elapsed < CHARGING_WINDOW_DURATION
            
        return False
    
    def _update_charging_state(self):
        """Update charging window state"""
        current_charging = self._is_charging()
        last_charging = self.state.get('last_charger_state', False)
        current_time = time.time()
        
        # Charger was disconnected
        if last_charging and not current_charging:
            self.state['charging_window_start'] = current_time
            self.logger.info("Charger disconnected - starting window period")
        
        # Charger was connected
        elif not last_charging and current_charging:
            self.state['charging_window_start'] = current_time
            self.logger.info("Charger connected - in charging window")
        
        # Reset window if charger reconnected
        elif current_charging:
            self.state['charging_window_start'] = current_time
        
        self.state['last_charger_state'] = current_charging
    
    def _get_expected_soc_from_voltage(self, voltage: float) -> float:
        """Get expected SOC percentage from voltage using li-ion curve"""
        if voltage >= LIION_VOLTAGE_CURVE[0][0]:
            return LIION_VOLTAGE_CURVE[0][1]
        if voltage <= LIION_VOLTAGE_CURVE[-1][0]:
            return LIION_VOLTAGE_CURVE[-1][1]
        
        # Linear interpolation between curve points
        for i in range(len(LIION_VOLTAGE_CURVE) - 1):
            v1, soc1 = LIION_VOLTAGE_CURVE[i]
            v2, soc2 = LIION_VOLTAGE_CURVE[i + 1]
            
            if v2 <= voltage <= v1:
                # Linear interpolation
                ratio = (voltage - v2) / (v1 - v2)
                return soc2 + ratio * (soc1 - soc2)
        
        return 50.0  # Default fallback
    
    def _is_bad_reading(self, voltage: float, soc: float) -> bool:
        """Check if reading deviates significantly from expected li-ion curve"""
        expected_soc = self._get_expected_soc_from_voltage(voltage)
        deviation = abs(soc - expected_soc) / expected_soc if expected_soc > 0 else 0
        
        is_bad = deviation > BAD_READING_THRESHOLD
        if is_bad:
            self.logger.warning(f"Bad reading detected: {soc:.1f}% vs expected {expected_soc:.1f}% "
                              f"(deviation: {deviation:.1%})")
        
        return is_bad
    
    def _can_quick_start(self) -> bool:
        """Check if quick-start is allowed (not in cooldown or charging window)"""
        current_time = time.time()
        last_quick_start = self.state.get('last_quick_start', 0)
        
        # Check cooldown period
        if current_time - last_quick_start < QUICK_START_COOLDOWN:
            return False
        
        # Never quick-start during charging window
        if self._is_in_charging_window():
            return False
        
        return True
    
    def _send_quick_start(self) -> bool:
        """Send quick-start command to MAX17043"""
        if not self._write_register16(MODE_REGISTER, QUICK_START_COMMAND):
            return False
        
        self.state['last_quick_start'] = time.time()
        self.logger.info("Quick-start command sent successfully")
        return True
    
    def _read_battery_data(self) -> Optional[Dict[str, Any]]:
        """Read battery data from MAX17043"""
        vcell_raw = self._read_register16(VCELL_REGISTER)
        soc_raw = self._read_register16(SOC_REGISTER)
        
        if vcell_raw is None or soc_raw is None:
            return None
        
        # Convert raw values according to datasheet
        voltage = (vcell_raw >> 4) * 1.25 / 1000.0  # Convert to volts
        soc_percent = (soc_raw >> 8) + (soc_raw & 0xFF) / 256.0  # High byte + fractional
        user_percent = min(100.0, max(0.0, (soc_percent - 10.0) * (100.0 / 90.0)))
        return {
            'voltage': voltage,
            'percent_user': user_percent,
            'percent_raw': soc_percent,
            'timestamp': time.time(),
            'charging': self._is_charging(),
            'in_window': self._is_in_charging_window()
        }
    
    def _write_status_file(self, battery_data: Dict[str, Any]):
        """Write battery data to status file for widget consumption"""
        try:
            status = {
                'battery': battery_data,
                'timestamp': time.time()
            }
            
            with open(STATUS_FILE, 'w') as f:
                json.dump(status, f, indent=2)
                
        except OSError as e:
            self.logger.error(f"Failed to write status file: {e}")
    
    def run(self) -> bool:
        """Run single monitoring cycle"""
        self.logger.info("Starting battery monitor")
        
        if not self._init_i2c():
            self.logger.error("Failed to initialize I2C")
            return False
        
        if not self._init_gpio():
            self.logger.error("Failed to initialize GPIO")
        
        # Update charging state
        self._update_charging_state()
        
        # Read battery data
        battery_data = self._read_battery_data()
        if battery_data is None:
            self.logger.error("Failed to read battery data")
            self._write_status_file({'error': 'Failed to read battery data'})
            self._save_state()
            return False
        
        # Check for bad readings
        is_bad = self._is_bad_reading(battery_data['voltage'], battery_data['percent_raw'])
        in_window = self._is_in_charging_window()
        
        self.logger.info(f"Battery: {battery_data['percent_raw']:.1f}%, "
                        f"{battery_data['voltage']:.3f}V, charging: {battery_data['charging']}, "
                        f"window: {in_window}, bad_reading: {is_bad}")
        
        # Handle bad readings
        if is_bad and not in_window:
            if self._can_quick_start():
                self.logger.warning("Bad reading detected outside window - sending quick-start")
                self._send_quick_start()
                # Wait for stabilization and re-read
                time.sleep(2)
                new_data = self._read_battery_data()
                if new_data:
                    battery_data = new_data
            else:
                self.logger.warning("Bad reading detected but quick-start not allowed")
        elif is_bad and in_window:
            self.logger.info("Bad reading detected during charging window - ignoring")
        
        # Write status file
        self._write_status_file(battery_data)
        self._save_state()
        
        return True
    
    def cleanup(self):
        """Cleanup resources"""
        if self.gpio_ready:
            try:
                GPIO.cleanup()
            except:
                pass


def main():
    monitor = BatteryMonitor()
    
    try:
        success = monitor.run()
        return 0 if success else 1
        
    except KeyboardInterrupt:
        monitor.logger.info("Interrupted by user")
        return 0
    except Exception as e:
        monitor.logger.error(f"Unexpected error: {e}")
        return 1
    finally:
        monitor.cleanup()


if __name__ == '__main__':
    exit(main())
