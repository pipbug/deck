#!/usr/bin/env python3
"""
Efficient Battery System Tray Widget
Optimized version with minimal overhead and smart caching
"""

import json
import subprocess
import datetime
import time
import os
from collections import deque
from typing import Optional, Dict, Tuple
import gi
import RPi.GPIO as GPIO

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator3

CHARGE_DETECT_PIN = 4


class IconManager:
    """Efficient icon management with comprehensive caching"""

    __slots__ = ("_icon_cache", "_theme", "_theme_ready", "_level_cache")

    # Static icon definitions - no repeated string formatting
    _CHARGING_TEMPLATES = (
        "battery-level-{}-charging-symbolic",
        "battery-{}-charging-symbolic",
        "battery-charging-symbolic",
    )

    _NORMAL_TEMPLATES = (
        "battery-level-{}-symbolic",
        "battery-{}-symbolic",
        "battery-symbolic",
    )

    _FULL_CHARGING = (
        "battery-full-charging-symbolic",
        "battery-level-100-charging-symbolic",
    )

    _FULL_NORMAL = ("battery-level-100-symbolic",)

    _FALLBACKS = ("battery-good-symbolic",)

    def __init__(self):
        self._icon_cache: Dict[str, bool] = {}
        self._theme = None
        self._theme_ready = False
        self._level_cache = {}  # Cache percentage->level conversions

    def _get_theme(self) -> Optional[Gtk.IconTheme]:
        """Get theme instance with caching"""
        if self._theme is None:
            self._theme = Gtk.IconTheme.get_default()
        return self._theme

    def check_theme_ready(self) -> bool:
        """Efficient theme readiness check"""
        if self._theme_ready:
            return True

        theme = self._get_theme()
        if theme and theme.has_icon("battery-symbolic"):
            self._theme_ready = True
        return self._theme_ready

    def _test_icon(self, icon_name: str) -> bool:
        """Test icon existence with caching"""
        if icon_name in self._icon_cache:
            return self._icon_cache[icon_name]

        theme = self._get_theme()
        exists = theme and theme.has_icon(icon_name)
        self._icon_cache[icon_name] = exists
        return exists

    def _get_level(self, percentage: float) -> int:
        """Get battery level with caching"""
        if percentage in self._level_cache:
            return self._level_cache[percentage]

        level = max(0, min(100, int(round(percentage / 10) * 10)))
        self._level_cache[percentage] = level
        return level

    def _find_icon(self, templates: Tuple[str, ...], level: int) -> Optional[str]:
        """Find first working icon from templates"""
        for template in templates:
            if level == 100 and "{}" not in template:
                icon_name = template
            else:
                icon_name = template.format(level)

            if self._test_icon(icon_name):
                return icon_name
        return None

    def get_battery_icon(self, percentage: float, is_charging: bool = False) -> str:
        """Get optimal battery icon efficiently"""
        if not self._theme_ready:
            return "battery-good-symbolic"

        level = self._get_level(percentage)

        # Handle 100% battery with dedicated icons
        if level == 100:
            icons = self._FULL_CHARGING if is_charging else self._FULL_NORMAL
            icon = self._find_icon(icons, level)
            if icon:
                return icon

        # Standard battery icons
        if is_charging:
            icon = self._find_icon(self._CHARGING_TEMPLATES, level)
            if icon:
                return icon

        # Normal battery icons
        icon = self._find_icon(self._NORMAL_TEMPLATES, level)
        if icon:
            return icon

        # Emergency fallback
        for fallback in self._FALLBACKS:
            if self._test_icon(fallback):
                return fallback

        return "battery-good-symbolic"

    def preload_icons(self):
        """Preload common icons efficiently"""
        if not self._theme_ready:
            return

        # Batch test common levels
        levels = range(0, 101, 10)
        all_templates = self._CHARGING_TEMPLATES + self._NORMAL_TEMPLATES

        for level in levels:
            for template in all_templates:
                self._test_icon(template.format(level))

        # Test special icons
        for icon in self._FULL_CHARGING + self._FULL_NORMAL + self._FALLBACKS:
            self._test_icon(icon)


class BatterySystemTray:
    """Efficient battery system tray widget"""

    __slots__ = (
        "status_file",
        "update_interval",
        "current_battery",
        "was_charging",
        "initialization_complete",
        "icon_manager",
        "gpio_ready",
        "indicator",
        "status_item",
        "runtime_item",
        "battery_history",
        "last_runtime_estimate",
        "last_timestamp",
        "file_mtime",
        "cached_battery_data",
    )

    def __init__(self):
        GLib.set_application_name("")
        GLib.set_prgname("")

        # Core attributes
        self.status_file = "/tmp/battery_status.json"
        self.update_interval = 5000
        self.current_battery = None
        self.was_charging = False
        self.initialization_complete = False
        self.gpio_ready = False

        # Caching
        self.file_mtime = 0
        self.cached_battery_data = None

        # Battery history with efficient deque
        self.battery_history = deque(maxlen=10)
        self.last_runtime_estimate = "Calculating..."
        self.last_timestamp = 0

        # Initialize components
        self.icon_manager = IconManager()
        self._setup_gpio()
        self._setup_indicator()

        # Start efficient polling
        GLib.timeout_add(500, self._check_theme_ready)
        GLib.timeout_add(self.update_interval, self._update_battery)

    def _setup_gpio(self):
        """Initialize GPIO efficiently"""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(CHARGE_DETECT_PIN, GPIO.IN)
            self.gpio_ready = True
        except:
            self.gpio_ready = False

    def _is_charging(self) -> bool:
        """Check charging status efficiently"""
        if not self.gpio_ready:
            return False

        try:
            return GPIO.input(CHARGE_DETECT_PIN) == GPIO.HIGH
        except:
            self.gpio_ready = False
            return False

    def _setup_indicator(self):
        """Setup AppIndicator with minimal initialization"""
        self.indicator = AppIndicator3.Indicator.new(
            "battery-widget",
            "battery-good-symbolic",
            AppIndicator3.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._create_menu()

    def _create_menu(self):
        """Create context menu efficiently"""
        menu = Gtk.Menu()

        # Create menu items
        self.status_item = Gtk.MenuItem()
        self.status_item.set_label("Battery: Initializing...")
        self.status_item.set_sensitive(False)

        self.runtime_item = Gtk.MenuItem()
        self.runtime_item.set_label("Runtime: Calculating...")
        self.runtime_item.set_sensitive(False)

        separator = Gtk.SeparatorMenuItem()

        details_item = Gtk.MenuItem()
        details_item.set_label("Show Details")
        details_item.connect("activate", self._show_details)

        # Add items efficiently
        for item in (self.status_item, self.runtime_item, separator, details_item):
            menu.append(item)
            item.show()

        menu.show()
        self.indicator.set_menu(menu)

    def _read_battery_data(self) -> Optional[Dict]:
        """Read battery data with file caching"""
        try:
            # Check if file was modified
            mtime = os.path.getmtime(self.status_file)
            if mtime == self.file_mtime and self.cached_battery_data:
                return self.cached_battery_data

            # Read and cache new data
            with open(self.status_file, "r") as f:
                data = json.load(f)

            self.file_mtime = mtime
            self.cached_battery_data = data.get("battery")
            return self.cached_battery_data

        except (FileNotFoundError, KeyError, json.JSONDecodeError, OSError):
            return None

    def _calculate_runtime(self) -> str:
        """Calculate runtime estimate efficiently"""
        if not self.current_battery:
            return self.last_runtime_estimate

        current_percent = self.current_battery["percent_user"]
        current_time = self.current_battery.get("timestamp", time.time())

        # Skip if no new data
        if current_time <= self.last_timestamp:
            return self.last_runtime_estimate

        # Add to history
        self.battery_history.append((current_percent, current_time))
        self.last_timestamp = current_time

        # Need at least 2 readings
        if len(self.battery_history) < 2:
            return self.last_runtime_estimate

        # Calculate discharge rate using oldest and newest readings
        old_percent, old_time = self.battery_history[0]
        time_span = current_time - old_time
        percent_change = old_percent - current_percent

        # Validate discharge data
        if time_span < 60 or percent_change <= 0:
            return self.last_runtime_estimate

        discharge_rate = (percent_change / time_span) * 3600

        if discharge_rate < 0.5:
            return "Calculating..."

        hours_remaining = current_percent / discharge_rate

        # Format result
        if hours_remaining > 24:
            self.last_runtime_estimate = ">24 hours"
        elif hours_remaining > 1:
            self.last_runtime_estimate = f"{hours_remaining:.1f} hours"
        else:
            minutes = hours_remaining * 60
            self.last_runtime_estimate = f"{minutes:.0f} minutes"

        return self.last_runtime_estimate

    def _check_theme_ready(self) -> bool:
        """Check theme readiness and initialize"""
        if self.icon_manager.check_theme_ready() and not self.initialization_complete:
            # Theme ready - initialize immediately
            self.icon_manager.preload_icons()
            self._update_display()
            self.initialization_complete = True
            return False  # Stop polling

        return True  # Continue polling

    def _update_display(self):
        """Update icon and menu items efficiently"""
        battery = self._read_battery_data()

        if not battery:
            self.status_item.set_label("Battery: Error")
            self.runtime_item.set_label("Runtime: Unknown")
            return

        self.current_battery = battery
        percentage = battery["percent_user"]
        voltage = battery["voltage"]
        charging = self._is_charging()

        # Handle charger state changes
        if self.was_charging and not charging:
            self.battery_history.clear()
            self.last_runtime_estimate = "Calculating..."

        self.was_charging = charging

        # Update icon
        icon_name = self.icon_manager.get_battery_icon(percentage, charging)
        self.indicator.set_icon(icon_name)

        # Update menu items
        status_text = f"Battery: {percentage:.0f}% ({voltage:.2f}V)"
        if charging:
            status_text += " - Charging"
            runtime_text = "Runtime: ---"
        else:
            runtime_text = f"Runtime: {self._calculate_runtime()}"

        self.status_item.set_label(status_text)
        self.runtime_item.set_label(runtime_text)

    def _update_battery(self) -> bool:
        """Main update timer callback"""
        if self.initialization_complete:
            self._update_display()
        return True

    def _show_details(self, widget):
        """Show detailed battery information"""
        if not self.current_battery:
            return

        battery = self.current_battery

        # Format timestamp efficiently
        timestamp_str = "Unknown"
        if "timestamp" in battery:
            try:
                dt = datetime.datetime.fromtimestamp(int(battery["timestamp"]))
                timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        charging = self._is_charging()
        current_icon = self.icon_manager.get_battery_icon(
            battery["percent_user"], charging
        )

        details = (
            f"Charge: {battery['percent_user']:.1f}%\n"
            f"Voltage: {battery['voltage']:.3f}V\n"
            f"Raw: {battery['percent_raw']:.1f}%\n"
            f"Charging: {'Yes' if charging else 'No'}\n"
            f"Icon: {current_icon}\n"
            f"Updated: {timestamp_str}"
        )

        try:
            subprocess.run(
                ["zenity", "--info", "--title", "Battery Details", "--text", details],
                timeout=30,
            )
        except:
            pass


def main():
    """Application entry point"""
    try:
        BatterySystemTray()
        Gtk.main()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            GPIO.cleanup()
        except:
            pass


if __name__ == "__main__":
    main()
