#!/bin/bash
# Install everything for cyberdeck

set -e

echo "Installing dependencies..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Update package list
echo "Updating package list..."
apt update

# Install required packages
echo "Installing dependencies..."
apt install -y \
    python3-gi \
    gir1.2-gtk-3.0 \
    libayatana-appindicator3-1 \
    gir1.2-ayatanaappindicator3-0.1 \
    python3-rpi.gpio \
    python3-smbus \
    python3-dbus \
    zenity \
    jq

echo ""
echo "Dependencies installed."
echo ""
echo "Downloading battery system files..."

# Base URL for raw GitHub files
BASE_URL="https://raw.githubusercontent.com/pipbug/deck/main"

# Download battery scripts
wget -O /usr/local/bin/battery "$BASE_URL/battery"
wget -O /usr/local/bin/battery-alert.py "$BASE_URL/battery-alert.py"
wget -O /usr/local/bin/battery-oneshot.py "$BASE_URL/battery-oneshot.py"
wget -O /usr/local/bin/battery-widget.py "$BASE_URL/battery-widget.py"

# Download systemd service files
wget -O /etc/systemd/system/battery-alert.service "$BASE_URL/battery-alert.service"
wget -O /etc/systemd/system/battery-oneshot.service "$BASE_URL/battery-oneshot.service"
wget -O /etc/systemd/system/battery-oneshot.timer "$BASE_URL/battery-oneshot.timer"

# Set executable permissions
chmod +x /usr/local/bin/battery
chmod +x /usr/local/bin/battery-alert.py
chmod +x /usr/local/bin/battery-oneshot.py
chmod +x /usr/local/bin/battery-widget.py

echo ""
echo "Installing battery widget desktop app..."

# Create application directory
mkdir -p /opt/battery-widget
cp /usr/local/bin/battery-widget.py /opt/battery-widget/battery-widget
chmod +x /opt/battery-widget/battery-widget.py

# Download desktop files
wget -O /etc/xdg/autostart/battery-widget.desktop "$BASE_URL/battery-widget.desktop"
wget -O /usr/share/applications/battery-widget.desktop "$BASE_URL/usrshareapps-battery-widget.desktop"

echo ""
echo "Setting up systemd services..."
systemctl daemon-reload
systemctl enable battery-alert.service
systemctl enable battery-oneshot.timer
systemctl start battery-alert.service
systemctl start battery-oneshot.timer

echo ""
echo "Downloading display overlay..."
wget -O /boot/firmware/overlays/rpi-dsi-generic-pwm.dtbo "$BASE_URL/rpi-dsi-generic-pwm.dtbo"

echo ""
echo "Updating boot configuration..."

# Backup existing config
cp /boot/firmware/config.txt /boot/firmware/config.txt.backup

# Append cyberdeck configuration
cat >> /boot/firmware/config.txt << 'EOF'

# Cyberdeck Configuration
# GPIO pin assignments:
# GPIO 23 - I2C SDA (to MAX17043 SDA pin)
# GPIO 24 - I2C SCL (to MAX17043 SCL pin)  
# GPIO 25 - Battery Alert (to MAX17043 ALRT pin - triggers shutdown when low)
# GPIO 27 - Fan PWM
# GPIO 4 - Charge port detection
# GPIO 3 - Shutdown button
# GPIO 18 - Backlight PWM

dtparam=audio=off
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24
dtoverlay=gpio-shutdown
dtoverlay=pwm-gpio-fan,fan_gpio=27,fan_temp0=50000,fan_temp1=57500,fan_temp2=65000,fan_temp3=72500
dtoverlay=vc4-kms-v3d
dtoverlay=rpi-dsi-generic-pwm
max_framebuffers=2
disable_fw_kms_setup=1
disable_overscan=1
arm_64bit=1
arm_boost=1
EOF

echo ""
echo "Installation complete!"
echo ""
echo "Services installed and started:"
echo "  - battery-alert.service (critical battery shutdown)"
echo "  - battery-oneshot.timer (battery data collection)"
echo "  - battery-widget (desktop app - will start on next login)"
echo ""
echo "Configuration backed up to: /boot/firmware/config.txt.backup"
echo ""
echo "Reboot required to activate hardware configuration."
read -p "Reboot now? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    reboot
else
    echo "Please reboot manually when ready."
fi