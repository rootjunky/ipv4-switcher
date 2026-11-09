<img src="ipv4.png" width="96" alt="IPv4 Switcher icon">

# IPv4 Switcher

Windows GUI for switching a network adapter between DHCP and a static IPv4 address.

## Features
- Dark/light UI that follows the Windows theme (dark title bar, DPI aware)
- Shows the selected adapter's live status, mode, IP, mask, gateway, DNS, MAC, link speed
- DHCP / Static toggle. Static fields default to mask `255.255.255.0`, gateway `192.168.1.1`, DNS `1.1.1.1`, and you can still edit them
- CIDR input (`192.168.1.50/24` fills in the mask)
- Field validation (bad IPs/masks are outlined red, plus a warning if the gateway is outside the subnet)
- Saved profiles in `%APPDATA%\IPv4Switcher\settings.json`
- Copy current settings to form, Renew DHCP, Ping gateway
- Real error reporting from netsh. Runs in the background, so the window doesn't freeze or flash consoles

## Run / build (Windows, Python 3.8+, no extra packages)
    python IPV4.py                 # prompts for UAC; add --no-elevate to skip
    pip install pyinstaller
    pyinstaller --clean IPV4.spec  # -> dist\IPV4Switcher.exe (requests admin on launch)

`IPV4_original.py` is the v1 script, kept for reference.

## Download
Grab `IPV4Switcher.exe` from the [Releases](../../releases) page.

## Changelog
- **2.1.0** New app icon (exe, title bar, taskbar). `make_icon.py` regenerates `ipv4.ico` (needs `pip install pillow`).
- **2.0.1** Fix: adapter details (IP, mask, gateway, DNS, MAC, speed) showed "—" because PowerShell's progress output on stderr broke the JSON parse. The status bar now explains it if the detailed query fails.
- **2.0.0** Rewrite: themed UI, profiles, validation, background tasks, UAC elevation.
