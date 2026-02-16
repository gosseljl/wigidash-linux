#!/bin/bash
set -euo pipefail

INSTALL_DIR="/opt/wigidash"
UDEV_RULE="/etc/udev/rules.d/99-wigidash.rules"
SERVICE_FILE="/etc/systemd/system/wigidash.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must be run as root."
    exit 1
fi

uninstall() {
    echo "Uninstalling WigiDash..."

    if systemctl is-active --quiet wigidash 2>/dev/null; then
        echo "  Stopping service..."
        systemctl stop wigidash
    fi

    if systemctl is-enabled --quiet wigidash 2>/dev/null; then
        echo "  Disabling service..."
        systemctl disable wigidash
    fi

    if [ -f "$SERVICE_FILE" ]; then
        echo "  Removing $SERVICE_FILE"
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
    fi

    if [ -f "$UDEV_RULE" ]; then
        echo "  Removing $UDEV_RULE"
        rm -f "$UDEV_RULE"
        udevadm control --reload-rules
        udevadm trigger
    fi

    if [ -d "$INSTALL_DIR" ]; then
        echo "  Removing $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
    fi

    echo "Done. WigiDash uninstalled."
}

install() {
    echo "Installing WigiDash..."

    # Copy main script
    echo "  Installing to $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    cp "$SCRIPT_DIR/wigidash.py" "$INSTALL_DIR/wigidash.py"
    chmod +x "$INSTALL_DIR/wigidash.py"

    # udev rule
    echo "  Installing udev rule"
    cat > "$UDEV_RULE" << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0666"
EOF
    udevadm control --reload-rules
    udevadm trigger

    # systemd service
    echo "  Installing systemd service"
    cp "$SCRIPT_DIR/wigidash.service" "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl enable wigidash
    systemctl start wigidash

    echo "Done. WigiDash installed and running."
    echo "  Check status: systemctl status wigidash"
    echo "  View logs:    journalctl -u wigidash -f"
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
else
    install
fi
