#!/usr/bin/env python3
"""
Battery System Tray Widget
Displays battery status in system tray with icon and menu
"""

import json
import subprocess
import sys
import datetime
import time
import gi
import RPi.GPIO as GPIO

gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')

from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator3

# GPIO pin for charge detection
CHARGE_DETECT_PIN = 4

class BatterySystemTray:
    def __init__(self):
        # Hide tooltip completely by setting empty application name
        GLib.set_application_name("")
        GLib.set_prgname("")
        
        self.status_file = '/tmp/battery_status.json'
        self.update_interval = 5000  # 5 seconds
        self.current_battery = None
        self.was_charging = False  # Track previous charging state
        
        # Setup GPIO for charge detection
        self.setup_charge_detection()
        
        # Setup AppIndicator
        self.setup_app_indicator()
        
        # Start update timer
        GLib.timeout_add(self.update_interval, self.update_battery)
        
        # Initial update
        self.update_battery()
    
    def setup_charge_detection(self):
        """Setup GPIO for charge detection"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(CHARGE_DETECT_PIN, GPIO.IN)
        print(f"Charge detection setup on GPIO {CHARGE_DETECT_PIN}")
    
    def is_charging(self):
        """Check if battery is currently charging"""
        return GPIO.input(CHARGE_DETECT_PIN) == GPIO.HIGH
    
    def setup_app_indicator(self):
        """Setup AppIndicator"""
        self.indicator = AppIndicator3.Indicator.new(
            "battery-widget",
            "battery-good-symbolic",
            AppIndicator3.IndicatorCategory.HARDWARE
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.create_menu()
    
    def create_menu(self):
        """Create right-click context menu"""
        menu = Gtk.Menu()
        
        # Battery status item
        self.status_item = Gtk.MenuItem()
        self.status_item.set_label("Battery: ---%")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        
        # Runtime estimate item
        self.runtime_item = Gtk.MenuItem()
        self.runtime_item.set_label("Runtime: Calculating...")
        self.runtime_item.set_sensitive(False)
        menu.append(self.runtime_item)
        
        # Separator
        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)
        
        # Show details
        details_item = Gtk.MenuItem()
        details_item.set_label("Show Details")
        details_item.connect("activate", self.show_details)
        menu.append(details_item)
        
        # Show all menu items
        for item in menu.get_children():
            item.show()
        menu.show()
        
        self.indicator.set_menu(menu)
    
    def read_battery_status(self):
        """Read battery status from JSON file"""
        try:
            with open(self.status_file, 'r') as f:
                data = json.load(f)
            return data['battery']
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None
    
    def get_battery_icon_name(self, percentage, is_charging=False):
        """Get battery level icon based on percentage and charging status"""
        # Round to nearest 10 for battery-level icons
        level = int(round(percentage / 10) * 10)
        
        # Clamp to valid range (0-100)
        level = max(0, min(100, level))
        
        if is_charging:
            return f"battery-level-{level}-charging-symbolic"
        else:
            return f"battery-level-{level}-symbolic"
    
    def detect_fresh_boot(self):
        """Check if system recently booted"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime = float(f.read().split()[0])
            return uptime < 300  # Less than 5 minutes uptime
        except:
            return False
    
    def calculate_runtime_estimate(self):
        """Calculate estimated runtime based on discharge rate"""
        battery = self.current_battery
        if not battery:
            return getattr(self, 'last_runtime_estimate', "Unknown")
        
        current_percent = battery['percent_user']
        current_time = battery.get('timestamp', time.time())
        
        # Initialize history and last estimate tracking
        if not hasattr(self, 'battery_history'):
            self.battery_history = []
        if not hasattr(self, 'last_runtime_estimate'):
            self.last_runtime_estimate = "Calculating..."
        if not hasattr(self, 'last_timestamp'):
            self.last_timestamp = 0
        
        # Clear old data if system recently booted
        if self.detect_fresh_boot() and not hasattr(self, 'boot_detected'):
            self.battery_history = []  # Clear old data from before shutdown
            self.boot_detected = True
        
        # Only process if we have new data
        if current_time <= self.last_timestamp:
            return self.last_runtime_estimate
        
        # Add current reading to history
        self.battery_history.append({
            'percent': current_percent,
            'time': current_time
        })
        self.last_timestamp = current_time
        
        # Keep only last 10 readings
        self.battery_history = self.battery_history[-10:]
        
        # Show "Calculating..." only during initial 2 readings
        if len(self.battery_history) < 2:
            return self.last_runtime_estimate  # Keep showing "Calculating..." from initialization
        
        # Calculate discharge rate
        time_span = current_time - self.battery_history[0]['time']
        percent_change = self.battery_history[0]['percent'] - current_percent
        
        if time_span < 60:  # Less than 1 minute of data
            return self.last_runtime_estimate  # Keep previous estimate
        
        if percent_change <= 0:  # Battery not discharging
            return self.last_runtime_estimate  # Keep previous estimate
        
        # Calculate and update estimate
        discharge_rate_per_hour = (percent_change / time_span) * 3600
        
        # Wait for proper discharge rate (suggests system was off or minimal usage)
        if discharge_rate_per_hour < 0.5:  # Less than 0.5%/hour
            return "Calculating..."  # Wait for meaningful discharge data
        
        hours_remaining = current_percent / discharge_rate_per_hour
        
        if hours_remaining > 24:
            self.last_runtime_estimate = ">24 hours"
        elif hours_remaining > 1:
            self.last_runtime_estimate = f"{hours_remaining:.1f} hours"
        else:
            minutes_remaining = hours_remaining * 60
            self.last_runtime_estimate = f"{minutes_remaining:.0f} minutes"
        
        return self.last_runtime_estimate
    
    def update_battery(self):
        """Update battery display in tray"""
        battery = self.read_battery_status()
        
        if not battery:
            self.indicator.set_icon("battery-missing-symbolic")
            self.status_item.set_label("Battery: Error")
            self.runtime_item.set_label("Runtime: Unknown")
            return True
        
        self.current_battery = battery
        percentage = battery['percent_user']
        voltage = battery['voltage']
        charging = self.is_charging()
        
        # Detect charger unplugged (charging -> not charging)
        if self.was_charging and not charging:
            # Clear battery history for fresh runtime estimates
            if hasattr(self, 'battery_history'):
                self.battery_history = []
            if hasattr(self, 'last_runtime_estimate'):
                self.last_runtime_estimate = "Calculating..."
        
        self.was_charging = charging  # Update previous state
        
        # Update icon with charging status
        icon_name = self.get_battery_icon_name(percentage, charging)
        self.indicator.set_icon(icon_name)
        
        # Update menu status with charging indicator
        status_text = f"Battery: {percentage:.0f}% ({voltage:.2f}V)"
        if charging:
            status_text += " - Charging"
        self.status_item.set_label(status_text)
        
        # Update runtime estimate (show "---" while charging)
        if charging:
            self.runtime_item.set_label("Runtime: ---")
        else:
            runtime = self.calculate_runtime_estimate()
            self.runtime_item.set_label(f"Runtime: {runtime}")
        
        return True  # Continue timer
    
    def show_details(self, widget):
        """Show detailed battery information"""
        if not self.current_battery:
            self.show_notification('Battery Error', 'No battery data available')
            return
        
        battery = self.current_battery
        
        # Format timestamp from epoch to human readable
        timestamp_str = "Unknown"
        if 'timestamp' in battery:
            try:
                timestamp = int(battery['timestamp'])
                dt = datetime.datetime.fromtimestamp(timestamp)
                timestamp_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                timestamp_str = str(battery.get('timestamp', 'Unknown'))
        
        details = (
            f"Charge: {battery['percent_user']:.1f}%\n"
            f"Voltage: {battery['voltage']:.3f}V\n"
            f"Raw: {battery['percent_raw']:.1f}%\n"
            f"Updated: {timestamp_str}"
        )
        
        self.show_notification('Battery Details', details)
    
    def show_notification(self, title, message):
        """Show notification using zenity"""
        try:
            subprocess.run(['zenity', '--info', '--title', title, '--text', message], 
                          timeout=30)
        except:
            print(f"{title}: {message}")

def main():
    try:
        battery_tray = BatterySystemTray()
        print("Battery widget started. Check your system tray.")
        Gtk.main()
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error starting battery widget: {e}")
    finally:
        # Clean up GPIO on exit
        try:
            GPIO.cleanup()
        except:
            pass

if __name__ == "__main__":
    main()
