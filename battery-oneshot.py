#!/usr/bin/env python3
"""
Battery Oneshot - Run once, update status, exit
For use with systemd timer (near-zero continuous CPU)
"""

import time
import smbus
import json
import os

def read_battery_once():
    """Single battery read and status file update"""
    try:
        bus = smbus.SMBus(3)
        
        # Quick read
        vcell_raw = bus.read_word_data(0x36, 0x02)
        soc_raw = bus.read_word_data(0x36, 0x04)
        
        # Calculate
        voltage = ((vcell_raw & 0xFF) << 8 | (vcell_raw >> 8)) * 0.000078125
        soc_swapped = (soc_raw & 0xFF) << 8 | (soc_raw >> 8)
        raw_percent = (soc_swapped >> 8) + (soc_swapped & 0xFF) / 256.0
        user_percent = min(100.0, max(0.0, (raw_percent - 15) * 1.176470588))
        
        # Write status
        status = {
            'battery': {
                'voltage': round(voltage, 3),
                'percent_user': round(user_percent, 1),
                'percent_raw': round(raw_percent, 1),
                'timestamp': int(time.time())
            },
            'last_updated': time.strftime('%H:%M:%S')
        }
        
        with open('/tmp/battery_status.json', 'w') as f:
            json.dump(status, f, separators=(',', ':'))
        os.chmod('/tmp/battery_status.json', 0o644)
        
        return True
    except:
        return False

if __name__ == "__main__":
    read_battery_once()
