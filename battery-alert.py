#!/usr/bin/env python3
"""
Layered Battery Alert Monitor
Uses voltage + percentage + GPIO for maximum reliability
"""

import time
import subprocess
import json
import os
from signal import signal, SIGTERM, SIGINT
import sys
import logging

# Layered shutdown thresholds
VOLTAGE_CRITICAL = 3.2      # Immediate shutdown voltage
VOLTAGE_WARNING = 3.3       # Warning voltage
PERCENTAGE_CRITICAL = 1     # Immediate shutdown percentage (user)
PERCENTAGE_WARNING = 5      # Warning percentage (user)

SHUTDOWN_DELAY = 15
POLL_INTERVAL = 10          # Check every 10 seconds for faster response
BATTERY_STATUS_FILE = '/tmp/battery_status.json'

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class LayeredAlertMonitor:
    def __init__(self):
        self.shutdown_initiated = False
        self.running = True
        self.last_warning_time = 0
        self.warning_shown = False
        
    def signal_handler(self, signum, frame):
        """Clean shutdown"""
        logging.info(f"Received signal {signum}, shutting down gracefully")
        self.running = False
        sys.exit(0)
        
    def get_battery_data(self):
        """Read battery data from JSON file"""
        try:
            with open(BATTERY_STATUS_FILE, 'r') as f:
                data = json.load(f)
            
            battery = data.get('battery', {})
            return {
                'voltage': battery.get('voltage', 0),
                'percent_user': battery.get('percent_user', 0),
                'percent_raw': battery.get('percent_raw', 0),
                'timestamp': battery.get('timestamp', 0),
                'error': battery.get('error', None)
            }
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None
    
    def check_shutdown_conditions(self):
        """Layered battery condition checking"""
        battery = self.get_battery_data()
        
        if not battery:
            logging.warning("Cannot read battery data - continuing monitoring")
            return "OK"
        
        if battery.get('error'):
            logging.warning(f"Battery error: {battery['error']}")
            return "OK"  # Don't shutdown on read errors
        
        voltage = battery['voltage']
        percentage = battery['percent_user']
        
        # Layer 1: Critical voltage protection (most reliable)
        if voltage <= VOLTAGE_CRITICAL and voltage > 0:
            logging.critical(f"CRITICAL VOLTAGE: {voltage:.3f}V <= {VOLTAGE_CRITICAL}V")
            return "CRITICAL_VOLTAGE"
        
        # Layer 2: Critical percentage protection
        if percentage <= PERCENTAGE_CRITICAL:
            logging.critical(f"CRITICAL PERCENTAGE: {percentage:.1f}% <= {PERCENTAGE_CRITICAL}%")
            return "CRITICAL_PERCENTAGE"
        
        # Layer 3: Combined warning thresholds
        if voltage <= VOLTAGE_WARNING and voltage > 0:
            logging.warning(f"LOW VOLTAGE WARNING: {voltage:.3f}V <= {VOLTAGE_WARNING}V")
            return "WARNING_VOLTAGE"
        
        if percentage <= PERCENTAGE_WARNING:
            logging.warning(f"LOW PERCENTAGE WARNING: {percentage:.1f}% <= {PERCENTAGE_WARNING}%")
            return "WARNING_PERCENTAGE"
        
        return "OK"
    
    def get_active_user_info(self):
        """Get the currently logged-in user and their display info"""
        try:
            # Find the active user session
            result = subprocess.run(['who'], capture_output=True, text=True)
            logging.info(f"WHO output: {result.stdout.strip()}")
            
            if result.returncode == 0 and result.stdout:
                # Get first logged-in user (usually the desktop user)
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    logging.info(f"Checking line: {line}")
                    if ':0' in line or 'tty7' in line:  # Desktop session indicators
                        username = line.split()[0]
                        logging.info(f"Found desktop user: {username}")
                        return username
            
            # Fallback: check for common user
            result = subprocess.run(['ls', '/home'], capture_output=True, text=True)
            if result.returncode == 0:
                users = result.stdout.strip().split('\n')
                for user in users:
                    if user and user != 'lost+found':
                        logging.info(f"Fallback user: {user}")
                        return user
            
            logging.info("Using final fallback: pi")
            return 'pi'  # Final fallback
        except Exception as e:
            logging.error(f"Error getting user info: {e}")
            return 'pi'
    
    def run_as_user(self, username, command):
        """Run a command as a specific user with their environment"""
        try:
            # Get user's UID
            uid_result = subprocess.run(['id', '-u', username], capture_output=True, text=True)
            if uid_result.returncode != 0:
                logging.error(f"Could not get UID for user {username}")
                return
            uid = uid_result.stdout.strip()
            logging.info(f"Running command as user {username} (UID: {uid})")
            
            # Use sudo -u instead of su to avoid command parsing issues
            sudo_command = ['sudo', '-u', username] + command
            
            # Set up environment - use simple approach that works
            env = os.environ.copy()
            env.update({
                'DISPLAY': ':0',
                'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'
            })
            
            logging.info(f"Executing: {' '.join(sudo_command)}")
            
            # Use Popen approach like the working old script (fire and forget)
            subprocess.Popen(sudo_command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logging.info(f"Command launched successfully")
            
        except Exception as e:
            logging.error(f"Failed to run command as user {username}: {e}")
            # Fallback - try direct execution
            try:
                logging.info("Trying direct execution fallback")
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e2:
                logging.error(f"Direct execution fallback also failed: {e2}")
    
    def show_desktop_warning(self, username, title, message, timeout=5, use_dialog=False):
        """Show desktop notification as the specified user"""        
        try:
            if use_dialog:
                # Show prominent ERROR dialog (more attention-grabbing than warning)
                zenity_cmd = ['zenity', '--error', '--title', title, '--text', message, 
                             f'--timeout={timeout}']
                logging.info(f"Showing ERROR dialog: {title}")
            else:
                # Use info dialog for less critical messages
                zenity_cmd = ['zenity', '--info', '--title', title, '--text', message, 
                             f'--timeout={timeout}']
                logging.info(f"Showing INFO dialog: {title}")
            
            self.run_as_user(username, zenity_cmd)
            
        except Exception as e:
            logging.error(f"Failed to show desktop warning: {e}")
    
    def show_low_battery_warning(self, condition, voltage, percentage):
        """Show low battery warning (non-critical)"""
        current_time = time.time()
        
        # Don't spam warnings - show max once every 5 minutes
        if current_time - self.last_warning_time < 300:
            return
        
        self.last_warning_time = current_time
        username = self.get_active_user_info()
        
        if condition == "WARNING_VOLTAGE":
            title = "Low Battery Voltage Warning"
            message = f"Battery voltage is low: {voltage:.2f}V\n\nPlease connect charger soon.\n\nCritical shutdown at {VOLTAGE_CRITICAL}V"
        else:  # WARNING_PERCENTAGE
            title = "Low Battery Warning"
            message = f"Battery level is low: {percentage:.1f}%\n\nPlease connect charger soon.\n\nCritical shutdown at {PERCENTAGE_CRITICAL}%"
        
        wall_message = f"LOW BATTERY WARNING: {voltage:.2f}V, {percentage:.1f}% - Connect charger!"
        
        try:
            subprocess.run(['wall', wall_message], capture_output=True, timeout=2)
        except:
            pass
        
        try:
            self.show_desktop_warning(username, title, message, 10, use_dialog=False)
        except:
            pass
        
        logging.warning(wall_message)
    
    def show_critical_shutdown_countdown(self, condition, voltage, percentage):
        """Critical shutdown with countdown"""
        try:
            username = self.get_active_user_info()
            
            # Determine shutdown reason
            if condition == "CRITICAL_VOLTAGE":
                reason = f"voltage critically low ({voltage:.2f}V)"
                dialog_reason = f"Battery voltage critically low!\n\nVoltage: {voltage:.2f}V (critical: {VOLTAGE_CRITICAL}V)"
            else:  # CRITICAL_PERCENTAGE
                reason = f"battery critically low ({percentage:.1f}%)"
                dialog_reason = f"Battery critically low!\n\nLevel: {percentage:.1f}% (critical: {PERCENTAGE_CRITICAL}%)"
            
            # Initial alert
            initial_dialog_text = f"{dialog_reason}\n\nSystem will shutdown in {SHUTDOWN_DELAY} seconds\nto protect the battery.\n\nSave your work NOW!"
            initial_msg = f"CRITICAL: Battery {reason}! System shutdown in {SHUTDOWN_DELAY} seconds - SAVE YOUR WORK!"
            
            # Show dialog and CLI message
            try:
                subprocess.run(['wall', initial_msg], capture_output=True, timeout=2)
            except:
                pass
            
            try:
                self.show_desktop_warning(username, "CRITICAL BATTERY ALERT", initial_dialog_text, SHUTDOWN_DELAY, use_dialog=True)
            except:
                pass
                
            logging.critical(f"CRITICAL: Battery {reason}! System shutdown in {SHUTDOWN_DELAY} seconds!")
            
            # Countdown with regular warnings
            for remaining in range(SHUTDOWN_DELAY, 0, -1):
                if remaining <= 5 or remaining % 5 == 0:
                    if remaining <= 5:
                        message = f"CRITICAL SHUTDOWN IN {remaining} SECONDS!"
                    else:
                        message = f"CRITICAL: Battery {reason}! Shutdown in {remaining} seconds - SAVE YOUR WORK!"
                    
                    # CLI broadcast
                    try:
                        subprocess.run(['wall', message], capture_output=True, timeout=1)
                    except:
                        pass
                    logging.critical(message)
                
                time.sleep(1)
            
            # Final warning
            final_dialog_text = f"System shutting down now to protect battery.\n\nReason: {reason.capitalize()}\n\nPlease connect charger before restarting."
            final_msg = f"EMERGENCY SHUTDOWN: Battery {reason}"
            
            try:
                subprocess.run(['wall', final_msg], capture_output=True, timeout=1)
                self.show_desktop_warning(username, "EMERGENCY SHUTDOWN", final_dialog_text, 3, use_dialog=True)
            except:
                pass
            logging.critical(final_msg)
            
            # Emergency shutdown
            time.sleep(2)
            subprocess.run(['sudo', 'shutdown', '-h', 'now'])
            
        except Exception as e:
            logging.error(f"Error during shutdown countdown: {e}")
            # Emergency shutdown
            subprocess.run(['sudo', 'shutdown', '-h', 'now'])
    
    def log_battery_status(self):
        """Log current battery status for monitoring"""
        battery = self.get_battery_data()
        if battery:
            voltage = battery['voltage']
            percentage = battery['percent_user']
            logging.info(f"Battery status: {voltage:.3f}V, {percentage:.1f}% user")
        else:
            logging.warning("Could not read battery status")
    
    def run(self):
        """Main monitoring loop with layered protection"""
        try:
            logging.info("Layered battery alert monitor started")
            logging.info(f"Thresholds - Critical: {VOLTAGE_CRITICAL}V/{PERCENTAGE_CRITICAL}%, Warning: {VOLTAGE_WARNING}V/{PERCENTAGE_WARNING}%")
            
            # Log initial status
            self.log_battery_status()
            
            while self.running:
                time.sleep(POLL_INTERVAL)
                
                condition = self.check_shutdown_conditions()
                
                if condition.startswith("CRITICAL") and not self.shutdown_initiated:
                    self.shutdown_initiated = True
                    battery = self.get_battery_data()
                    voltage = battery['voltage'] if battery else 0
                    percentage = battery['percent_user'] if battery else 0
                    
                    logging.critical(f"CRITICAL BATTERY CONDITION: {condition}")
                    self.show_critical_shutdown_countdown(condition, voltage, percentage)
                    break
                
                elif condition.startswith("WARNING"):
                    battery = self.get_battery_data()
                    voltage = battery['voltage'] if battery else 0
                    percentage = battery['percent_user'] if battery else 0
                    self.show_low_battery_warning(condition, voltage, percentage)
                
                elif condition == "OK":
                    # Reset warning state when battery is good
                    if self.warning_shown:
                        self.warning_shown = False
                        logging.info("Battery level returned to normal")
                
        except Exception as e:
            logging.error(f"Error in layered battery monitor: {e}")
        finally:
            logging.info("Battery monitor shutdown")

if __name__ == "__main__":
    import sys
    
    # Handle test command
    if len(sys.argv) > 1 and sys.argv[1] == 'test-notifications':
        # Test notification system
        monitor = LayeredAlertMonitor()
        username = monitor.get_active_user_info()
        print(f"Detected user: {username}")
        
        print("Testing INFO dialog...")
        monitor.show_desktop_warning(username, "Test Info", "This is a test info dialog", 5, use_dialog=False)
        
        time.sleep(2)
        
        print("Testing ERROR dialog...")
        monitor.show_desktop_warning(username, "Test Error", "This is a test error dialog", 5, use_dialog=True)
        
        sys.exit(0)
    
    # Normal operation
    monitor = LayeredAlertMonitor()
    signal(SIGTERM, monitor.signal_handler)
    signal(SIGINT, monitor.signal_handler)
    monitor.run()
