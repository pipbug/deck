#!/usr/bin/env python3
"""
Dual LED controller for Raspberry Pi
- Caps Lock LED: Monitors USB keyboard and toggles on Caps Lock state
- Battery LED: Monitors battery level and illuminates when ≤15%
Optimized for minimal CPU usage with async event handling
"""

import asyncio
import evdev
import RPi.GPIO as GPIO
import json
import time
import os
from contextlib import asynccontextmanager
from typing import Optional

# Configuration
CAPS_LED_PIN = 5
BATTERY_LED_PIN = 6
BATTERY_CHECK_INTERVAL = 30  # seconds
BATTERY_LOW_THRESHOLD = 15.0  # percent
BATTERY_STATUS_FILE = '/tmp/battery_status.json'
MAX_DATA_AGE = 300  # seconds (5 minutes)
DEBUG = False

class DualLEDController:
    def __init__(self, caps_pin: int, battery_pin: int):
        self.caps_pin = caps_pin
        self.battery_pin = battery_pin
        self.caps_state = False
        self.battery_low = False
        self.keyboard: Optional[evdev.InputDevice] = None
        
        # GPIO setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.caps_pin, GPIO.OUT)
        GPIO.setup(self.battery_pin, GPIO.OUT)
        GPIO.output(self.caps_pin, GPIO.LOW)
        GPIO.output(self.battery_pin, GPIO.LOW)
    
    def find_keyboard(self) -> Optional[evdev.InputDevice]:
        """Find USB keyboard device with Caps Lock capability"""
        if self.keyboard and not self.keyboard.closed:
            return self.keyboard
            
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        
        for device in devices:
            caps = device.capabilities()
            if (evdev.ecodes.EV_KEY in caps and 
                evdev.ecodes.KEY_CAPSLOCK in caps[evdev.ecodes.EV_KEY]):
                if DEBUG:
                    print(f"Found keyboard: {device.name}")
                self.keyboard = device
                return device
        
        return None
    
    def toggle_caps_led(self) -> None:
        """Toggle Caps Lock LED state"""
        self.caps_state = not self.caps_state
        GPIO.output(self.caps_pin, GPIO.HIGH if self.caps_state else GPIO.LOW)
        
        if DEBUG:
            print(f"Caps Lock {'ON' if self.caps_state else 'OFF'}")
    
    def read_battery_level(self) -> Optional[float]:
        """Read battery level from status JSON file"""
        try:
            if not os.path.exists(BATTERY_STATUS_FILE):
                if DEBUG:
                    print(f"Battery status file not found: {BATTERY_STATUS_FILE}")
                return None
            
            with open(BATTERY_STATUS_FILE, 'r') as f:
                data = json.load(f)
            
            # Check if data is recent enough
            current_time = int(time.time())
            data_timestamp = data.get('battery', {}).get('timestamp', 0)
            
            if current_time - data_timestamp > MAX_DATA_AGE:
                if DEBUG:
                    print(f"Battery data is stale (age: {current_time - data_timestamp}s)")
                return None
            
            # Extract user percentage
            battery_info = data.get('battery', {})
            user_percent = battery_info.get('percent_user')
            
            if user_percent is None:
                if DEBUG:
                    print("No percent_user field in battery data")
                return None
                
            return float(user_percent)
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            if DEBUG:
                print(f"Battery data parsing error: {e}")
            return None
        except Exception as e:
            if DEBUG:
                print(f"Battery file read error: {e}")
            return None
    
    def update_battery_led(self, battery_level: Optional[float]) -> None:
        """Update battery LED based on level"""
        if battery_level is None:
            return
            
        new_low_state = battery_level <= BATTERY_LOW_THRESHOLD
        
        if new_low_state != self.battery_low:
            self.battery_low = new_low_state
            GPIO.output(self.battery_pin, GPIO.HIGH if self.battery_low else GPIO.LOW)
            
            if DEBUG:
                print(f"Battery: {battery_level:.1f}% - LED {'ON' if self.battery_low else 'OFF'}")
    
    async def battery_monitor_task(self) -> None:
        """Periodic battery level monitoring task"""
        while True:
            battery_level = self.read_battery_level()
            self.update_battery_led(battery_level)
            await asyncio.sleep(BATTERY_CHECK_INTERVAL)
    
    async def caps_lock_monitor_task(self) -> None:
        """Caps Lock key monitoring task"""
        keyboard = self.find_keyboard()
        if not keyboard:
            raise RuntimeError("No compatible keyboard found")
        
        try:
            async for event in keyboard.async_read_loop():
                if (event.type == evdev.ecodes.EV_KEY and 
                    event.code == evdev.ecodes.KEY_CAPSLOCK and 
                    event.value == 1):
                    self.toggle_caps_led()
                    
        except (OSError, IOError) as e:
            if DEBUG:
                print(f"Keyboard device error: {e}")
            self.keyboard = None
            await asyncio.sleep(1)
            return await self.caps_lock_monitor_task()
    
    async def run(self) -> None:
        """Run both monitoring tasks concurrently"""
        await asyncio.gather(
            self.caps_lock_monitor_task(),
            self.battery_monitor_task()
        )
    
    def cleanup(self) -> None:
        """Clean up all resources"""
        GPIO.output(self.caps_pin, GPIO.LOW)
        GPIO.output(self.battery_pin, GPIO.LOW)
        GPIO.cleanup()
        
        if self.keyboard:
            self.keyboard.close()

@asynccontextmanager
async def led_controller(caps_pin: int, battery_pin: int):
    """Context manager for proper resource cleanup"""
    controller = DualLEDController(caps_pin, battery_pin)
    try:
        yield controller
    finally:
        controller.cleanup()

async def main():
    """Main entry point"""
    try:
        async with led_controller(CAPS_LED_PIN, BATTERY_LED_PIN) as controller:
            if DEBUG:
                print("Starting dual LED controller...")
            await controller.run()
    except KeyboardInterrupt:
        if DEBUG:
            print("Shutting down...")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
