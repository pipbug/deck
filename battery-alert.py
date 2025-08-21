#!/usr/bin/env python3
"""
Enhanced Battery Alert Monitor with GUI Support
Shows countdown in CLI and desktop environments
"""

import time
import subprocess
import RPi.GPIO as GPIO
from signal import signal, SIGTERM, SIGINT
import sys
import logging
import os

ALERT_PIN = 25
SHUTDOWN_DELAY = 15  # Increased to 15 seconds for better user experience
POLL_INTERVAL = 30

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class EnhancedAlertMonitor:
    def __init__(self):
        self.shutdown_initiated = False
        self.running = True
        
    def setup_gpio(self):
        """Simple GPIO setup"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(ALERT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logging.info(f"Enhanced battery alert monitor started on GPIO {ALERT_PIN}")
        
    def cleanup_gpio(self):
        """GPIO cleanup"""
        GPIO.cleanup()
        logging.info("GPIO cleanup complete")
        
    def signal_handler(self, signum, frame):
        """Clean shutdown"""
        logging.info(f"Received signal {signum}, shutting down gracefully")
        self.running = False
        self.cleanup_gpio()
        sys.exit(0)
        
    def get_active_user_info(self):
        """Get the currently logged-in user and their display info"""
        try:
            # Find the active user session
            result = subprocess.run(['who'], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout:
                # Get first logged-in user (usually the desktop user)
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if ':0' in line or 'tty7' in line:  # Desktop session indicators
                        username = line.split()[0]
                        return username
            
            # Fallback: check for common user
            result = subprocess.run(['ls', '/home'], capture_output=True, text=True)
            if result.returncode == 0:
                users = result.stdout.strip().split('\n')
                for user in users:
                    if user and user != 'lost+found':
                        return user
            
            return 'pi'  # Final fallback
        except:
            return 'pi'
    
    def run_as_user(self, username, command):
        """Run a command as a specific user with their environment"""
        try:
            # Get user's UID
            uid_result = subprocess.run(['id', '-u', username], capture_output=True, text=True)
            if uid_result.returncode != 0:
                return
            uid = uid_result.stdout.strip()
            
            # Use sudo -u instead of su to avoid command parsing issues
            sudo_command = ['sudo', '-u', username] + command
            
            # Set up environment
            env = os.environ.copy()
            env.update({
                'DISPLAY': ':0',
                'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'
            })
            
            subprocess.Popen(sudo_command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        except Exception as e:
            logging.error(f"Failed to run command as user {username}: {e}")
            # Fallback - try direct execution
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except:
                pass
    
    def get_user_id(self, username):
        """Get user ID for a username"""
        try:
            result = subprocess.run(['id', '-u', username], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else '1000'
        except:
            return '1000'
        
    def show_desktop_warning(self, username, title, message, timeout=5, use_dialog=False):
        """Show desktop notification as the specified user"""
        try:
            if use_dialog:
                # Show prominent ERROR dialog (more attention-grabbing than warning)
                zenity_cmd = ['zenity', '--error', '--title', title, '--text', message, 
                             f'--timeout={timeout}']
                self.run_as_user(username, zenity_cmd)
            else:
                # Use info dialog for less critical messages
                zenity_cmd = ['zenity', '--info', '--title', title, '--text', message, 
                             f'--timeout={timeout}']
                self.run_as_user(username, zenity_cmd)
            
        except Exception as e:
            logging.error(f"Failed to show desktop warning: {e}")
        
    def broadcast_message(self, message, show_dialog=False):
        """Send message to all terminals and optionally show desktop dialog"""
        try:
            # CLI users - wall command
            subprocess.run(['wall', message], capture_output=True, timeout=2)
        except:
            pass
            
        # Desktop users - only show dialog if requested
        if show_dialog:
            try:
                username = self.get_active_user_info()
                self.show_desktop_warning(username, "CRITICAL BATTERY ALERT", message, 15, use_dialog=True)
            except:
                pass
        
    def show_shutdown_countdown(self):
        """Simple shutdown with initial and final warnings only"""
        try:
            # Get the active user for desktop notifications
            username = self.get_active_user_info()
            
            # Initial alert - main dialog with better formatting (no emojis)
            initial_dialog_text = "Battery critically low!\n\nSystem will shutdown in 15 seconds\nto protect the battery.\n\nSave your work!"
            initial_msg = f"Battery critically low! System will shutdown in {SHUTDOWN_DELAY} seconds to protect battery. Save your work!"
            
            # Show dialog and CLI message
            try:
                subprocess.run(['wall', initial_msg], capture_output=True, timeout=2)
            except:
                pass
            
            try:
                self.show_desktop_warning(username, "CRITICAL BATTERY ALERT", initial_dialog_text, 15, use_dialog=True)
            except:
                pass
                
            logging.critical(f"Battery critically low! System will shutdown in {SHUTDOWN_DELAY} seconds!")
            
            # Simple countdown with CLI warnings only (no desktop spam)
            for remaining in range(SHUTDOWN_DELAY, 0, -1):
                if remaining <= 5 or remaining % 5 == 0:
                    if remaining <= 5:
                        message = f"SHUTTING DOWN IN {remaining} SECONDS!"
                    else:
                        message = f"Battery critically low! Shutdown in {remaining} seconds - SAVE YOUR WORK!"
                    
                    # CLI broadcast only
                    try:
                        subprocess.run(['wall', message], capture_output=True, timeout=1)
                    except:
                        pass
                    logging.critical(message)
                
                time.sleep(1)
            
            # Final warning - desktop and CLI
            final_dialog_text = "System is shutting down now to protect the battery. Please plug in your device."
            final_msg = "BATTERY CRITICALLY LOW"
            
            try:
                subprocess.run(['wall', final_msg], capture_output=True, timeout=1)
                self.show_desktop_warning(username, "SHUTTING DOWN NOW", final_dialog_text, 3, use_dialog=True)
            except:
                pass
            logging.critical(final_msg)
            
            # Brief pause then shutdown
            time.sleep(2)
            subprocess.run(['sudo', 'shutdown', '-h', 'now'])
            
        except Exception as e:
            logging.error(f"Error during shutdown countdown: {e}")
            # Emergency shutdown
            subprocess.run(['sudo', 'shutdown', '-h', 'now'])
            
    def run(self):
        """Main monitoring loop"""
        try:
            self.setup_gpio()
            
            while self.running:
                time.sleep(POLL_INTERVAL)
                
                if GPIO.input(ALERT_PIN) == GPIO.LOW:
                    if not self.shutdown_initiated:
                        self.shutdown_initiated = True
                        logging.critical("Battery alert detected on GPIO 25")
                        self.show_shutdown_countdown()
                        break
                
        except Exception as e:
            logging.error(f"Error in battery alert monitor: {e}")
        finally:
            self.cleanup_gpio()

if __name__ == "__main__":
    monitor = EnhancedAlertMonitor()
    signal(SIGTERM, monitor.signal_handler)
    signal(SIGINT, monitor.signal_handler)
    monitor.run()
