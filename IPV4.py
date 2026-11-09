"""IPv4 Switcher - quickly switch a Windows network adapter between DHCP and static IPv4.

Reads adapter state with PowerShell (falls back to netsh), applies settings with netsh.
Profiles and settings are stored in %APPDATA%\\IPv4Switcher\\settings.json.
"""

import base64
import ctypes
import ipaddress
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

APP_NAME = "IPv4 Switcher"
VERSION = "2.1.0"

# Default values for the static fields (still editable).
DEFAULTS = {
    "ip": "",
    "mask": "255.255.255.0",
    "gateway": "192.168.1.1",
    "dns1": "1.1.1.1",
    "dns2": "",
}

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- #
# System helpers
# --------------------------------------------------------------------------- #

def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Re-run this program elevated via UAC. Returns True if the new process started."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False


def enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def system_prefers_dark():
    if not IS_WINDOWS:
        return True
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return True


def style_titlebar(window, dark):
    """Ask Windows 10/11 to draw a dark (or light) title bar."""
    if not IS_WINDOWS:
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def resource_path(name):
    """Path to a bundled file, both from source and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def set_window_icon(window):
    if not IS_WINDOWS:
        return
    try:
        # Own taskbar identity, so Windows shows our icon instead of python.exe's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RootJunky.IPv4Switcher")
    except Exception:
        pass
    icon = resource_path("ipv4.ico")
    if os.path.exists(icon):
        try:
            window.iconbitmap(default=icon)  # default= also applies to dialogs
        except tk.TclError:
            pass


def oem_encoding():
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"


def run_split(cmd, timeout=60, encoding=None):
    """Run a command without a shell or console window. Returns (returncode, stdout, stderr)."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout, **kwargs)
    enc = encoding or oem_encoding()
    return (p.returncode, p.stdout.decode(enc, errors="replace").strip(),
            p.stderr.decode(enc, errors="replace").strip())


def run(cmd, timeout=60, encoding=None):
    """Like run_split, but stdout and stderr combined. Returns (returncode, output)."""
    rc, out, err = run_split(cmd, timeout, encoding)
    return rc, "\n".join(x for x in (out, err) if x)


# --------------------------------------------------------------------------- #
# Adapter queries
# --------------------------------------------------------------------------- #

PS_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$out = @(foreach ($a in (Get-NetAdapter | Sort-Object Name)) {
  $ip  = Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 |
         Sort-Object { $_.IPAddress -like '169.254.*' } | Select-Object -First 1
  $ipi = Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4
  $gw  = Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' |
         Sort-Object RouteMetric | Select-Object -First 1
  $dns = Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4
  [pscustomobject]@{
    Name        = [string]$a.Name
    Description = [string]$a.InterfaceDescription
    Status      = [string]$a.Status
    Mac         = [string]$a.MacAddress
    Speed       = [string]$a.LinkSpeed
    IP          = [string]$ip.IPAddress
    Prefix      = [int]$ip.PrefixLength
    Dhcp        = [string]$ipi.Dhcp
    Gateway     = [string]$gw.NextHop
    Dns         = @($dns.ServerAddresses)
  }
})
ConvertTo-Json -InputObject $out -Compress -Depth 3
"""


def query_adapters_powershell():
    encoded = base64.b64encode(PS_QUERY.encode("utf-16-le")).decode("ascii")
    # Only stdout is parsed: PowerShell writes progress/CLIXML noise to stderr when redirected.
    rc, out, err = run_split(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                              "Bypass", "-EncodedCommand", encoded], timeout=45, encoding="utf-8")
    out = out.lstrip("\ufeff")
    start = out.find("[")
    if start < 0:
        start = out.find("{")
    if start < 0:
        raise RuntimeError(err or out or f"PowerShell query failed (exit code {rc})")
    data, _end = json.JSONDecoder().raw_decode(out[start:])
    if isinstance(data, dict):
        data = [data]
    adapters = []
    for a in data:
        dns = a.get("Dns") or []
        if isinstance(dns, str):
            dns = [dns]
        adapters.append({
            "name": a.get("Name") or "",
            "description": a.get("Description") or "",
            "status": a.get("Status") or "",
            "mac": a.get("Mac") or "",
            "speed": a.get("Speed") or "",
            "ip": a.get("IP") or "",
            "prefix": a.get("Prefix") or 0,
            "dhcp": a.get("Dhcp") or "",
            "gateway": a.get("Gateway") or "",
            "dns": [d for d in dns if d],
        })
    return [a for a in adapters if a["name"]]


def query_adapters_netsh():
    """Fallback: names and state only. Parses by column layout, not by (localized) words."""
    rc, out = run(["netsh", "interface", "show", "interface"], timeout=20)
    if rc != 0:
        raise RuntimeError(out or "netsh failed")
    adapters, past_header = [], False
    for line in out.splitlines():
        if set(line.strip()) == {"-"} or line.strip().startswith("---"):
            past_header = True
            continue
        if not past_header or not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) == 4:
            adapters.append({"name": parts[3].strip(), "description": "", "status": parts[1],
                             "mac": "", "speed": "", "ip": "", "prefix": 0, "dhcp": "",
                             "gateway": "", "dns": []})
    return adapters


def query_adapters():
    """Returns (adapters, warning). warning is set when only the limited fallback worked."""
    try:
        return query_adapters_powershell(), None
    except Exception as exc:
        reason = str(exc).strip().splitlines()
        adapters = query_adapters_netsh()
        return adapters, ("PowerShell adapter query failed, so details are limited"
                          + (f": {reason[0][:150]}" if reason else "."))


# --------------------------------------------------------------------------- #
# Validation and commands
# --------------------------------------------------------------------------- #

def parse_ipv4(text):
    return str(ipaddress.IPv4Address(text.strip()))


def parse_ip_field(text):
    """Accepts '192.168.1.50' or '192.168.1.50/24'. Returns (ip, mask or None)."""
    text = text.strip()
    if "/" in text:
        iface = ipaddress.IPv4Interface(text)
        return str(iface.ip), str(iface.netmask)
    return parse_ipv4(text), None


def valid_mask(text):
    try:
        net = ipaddress.IPv4Network(f"0.0.0.0/{text.strip()}")
        # ipaddress also accepts host masks (0.0.0.255); require a real netmask.
        return str(net.netmask) == parse_ipv4(text)
    except ValueError:
        return False


def prefix_to_mask(prefix):
    try:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{int(prefix)}").netmask)
    except (ValueError, TypeError):
        return ""


def validate_static(form):
    """Returns (config, errors, warnings)."""
    errors, warnings = [], []
    cfg = {"mode": "static"}

    ip_text = form.get("ip", "").strip()
    mask = form.get("mask", "").strip()
    ip = None
    if not ip_text:
        errors.append("IP address is required.")
    else:
        try:
            ip, cidr_mask = parse_ip_field(ip_text)
            if cidr_mask:
                mask = cidr_mask
        except ValueError:
            errors.append(f"'{ip_text}' is not a valid IP address.")

    if not valid_mask(mask):
        errors.append(f"'{mask}' is not a valid subnet mask.")
        mask = None

    net = None
    if ip and mask:
        addr = ipaddress.IPv4Address(ip)
        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
        if addr.is_loopback or addr.is_multicast or addr.is_unspecified or ip == "255.255.255.255":
            errors.append(f"{ip} can't be used as an adapter address.")
        elif net.prefixlen <= 30 and addr in (net.network_address, net.broadcast_address):
            errors.append(f"{ip} is the network or broadcast address of {net}.")
    cfg["ip"], cfg["mask"] = ip or "", mask or ""

    gateway = form.get("gateway", "").strip()
    if gateway:
        try:
            gateway = parse_ipv4(gateway)
            if gateway == ip:
                errors.append("Gateway can't be the same as the IP address.")
            elif net and ipaddress.IPv4Address(gateway) not in net:
                warnings.append(f"Gateway {gateway} is outside the {net} subnet.")
        except ValueError:
            errors.append(f"'{gateway}' is not a valid gateway.")
    cfg["gateway"] = gateway

    for key, label in (("dns1", "Primary DNS"), ("dns2", "Secondary DNS")):
        value = form.get(key, "").strip()
        if value:
            try:
                value = parse_ipv4(value)
            except ValueError:
                errors.append(f"{label} '{value}' is not a valid address.")
        cfg[key] = value
    if cfg["dns2"] and not cfg["dns1"]:
        cfg["dns1"], cfg["dns2"] = cfg["dns2"], ""

    return cfg, errors, warnings


def build_commands(adapter, cfg, current=None):
    """netsh command lines for a config. Run without a shell, so no cmd.exe injection."""
    if '"' in adapter:
        raise ValueError("Adapter names containing quotes are not supported.")
    n = f'name="{adapter}"'
    base = "netsh interface ipv4"
    if cfg["mode"] == "dhcp":
        cmds = []
        if not current or current.get("dhcp") != "Enabled":
            cmds.append(f"{base} set address {n} source=dhcp")
        cmds.append(f"{base} set dnsservers {n} source=dhcp")
        return cmds

    gw = f"gateway={cfg['gateway']} gwmetric=1" if cfg["gateway"] else "gateway=none"
    cmds = [f"{base} set address {n} source=static address={cfg['ip']} mask={cfg['mask']} {gw}"]
    if cfg["dns1"]:
        cmds.append(f"{base} set dnsservers {n} source=static address={cfg['dns1']} "
                    f"register=primary validate=no")
        if cfg["dns2"]:
            cmds.append(f"{base} add dnsservers {n} address={cfg['dns2']} index=2 validate=no")
    else:
        cmds.append(f"{base} set dnsservers {n} source=static address=none validate=no")
    return cmds


def run_commands(cmds):
    log = []
    for cmd in cmds:
        rc, out = run(cmd, timeout=60)
        log.append(f"> {cmd}\n{out}".strip())
        if rc != 0 and "already" not in out.lower():
            raise RuntimeError(f"{out or 'Command failed (exit code %d).' % rc}\n\nCommand:\n{cmd}")
    return "\n\n".join(log)


# --------------------------------------------------------------------------- #
# Settings / profiles
# --------------------------------------------------------------------------- #

def settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "IPv4Switcher", "settings.json")


def load_settings():
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("profiles", [])
            return data
    except Exception:
        pass
    return {"profiles": [], "last_adapter": ""}


def save_settings(data):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

PALETTES = {
    "dark": {
        "bg": "#1b1c1f", "card": "#26282c", "card2": "#33363b", "hover": "#3e4148",
        "border": "#383b41", "field": "#1e1f23", "fg": "#e8e9eb", "muted": "#9aa0a8",
        "accent": "#3b82f6", "accent_hover": "#2f6fdc", "ok": "#22c55e", "warn": "#f59e0b",
        "err": "#ef4444", "on_accent": "#ffffff",
    },
    "light": {
        "bg": "#f2f3f5", "card": "#ffffff", "card2": "#e9ebee", "hover": "#dde0e4",
        "border": "#d9dce1", "field": "#ffffff", "fg": "#1f2328", "muted": "#5f6670",
        "accent": "#2563eb", "accent_hover": "#1d4ed8", "ok": "#16a34a", "warn": "#d97706",
        "err": "#dc2626", "on_accent": "#ffffff",
    },
}

FONT = "Segoe UI" if IS_WINDOWS else "TkDefaultFont"
FIELDS = [
    ("ip", "IP address"),
    ("mask", "Subnet mask"),
    ("gateway", "Gateway"),
    ("dns1", "Primary DNS"),
    ("dns2", "Secondary DNS"),
]


class NameDialog(tk.Toplevel):
    """Small themed prompt for a profile name."""

    def __init__(self, app, title, prompt, initial=""):
        super().__init__(app.root)
        self.result = None
        self.withdraw()
        self.title(title)
        self.transient(app.root)
        self.resizable(False, False)
        self.configure(bg=app.c["bg"])

        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=prompt).pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(frm, textvariable=self.var, width=34, font=(FONT, 10))
        entry.pack(fill="x", pady=(6, 14), ipady=2)
        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        style_titlebar(self, app.dark)
        entry.focus_set()
        entry.select_range(0, "end")
        self.grab_set()
        self.wait_window()

    def _ok(self):
        value = self.var.get().strip()
        if value:
            self.result = value
            self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.dark = system_prefers_dark()
        self.c = PALETTES["dark" if self.dark else "light"]
        self.settings = load_settings()
        self.adapters = {}          # name -> info dict
        self.display_to_name = {}   # combobox label -> name
        self.busy = False
        self.tasks = queue.Queue()
        self.admin = is_admin()

        root.title(APP_NAME)
        set_window_icon(root)
        root.configure(bg=self.c["bg"])
        root.resizable(False, False)
        self._setup_style()
        self._build()
        self._set_mode("dhcp")
        style_titlebar(root, self.dark)

        root.bind("<F5>", lambda e: self.refresh())
        root.after(100, self._poll_tasks)
        self.refresh()

    # ---- styling ---------------------------------------------------------- #

    def _setup_style(self):
        c = self.c
        s = ttk.Style(self.root)
        s.theme_use("clam")
        base = (FONT, 10)

        s.configure(".", background=c["bg"], foreground=c["fg"], font=base,
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    troughcolor=c["card"], focuscolor=c["accent"], fieldbackground=c["field"],
                    selectbackground=c["accent"], selectforeground=c["on_accent"],
                    insertcolor=c["fg"])
        s.configure("TFrame", background=c["bg"])
        s.configure("Card.TFrame", background=c["card"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("Header.TLabel", font=(FONT, 17, "bold"))
        s.configure("Sub.TLabel", foreground=c["muted"], font=(FONT, 9))
        s.configure("Card.TLabel", background=c["card"])
        s.configure("Section.Card.TLabel", background=c["card"], font=(FONT, 11, "bold"))
        s.configure("Key.Card.TLabel", background=c["card"], foreground=c["muted"], font=(FONT, 9))
        s.configure("Value.Card.TLabel", background=c["card"], font=(FONT, 10))
        s.configure("Hint.Card.TLabel", background=c["card"], foreground=c["muted"], font=(FONT, 8))
        s.configure("Status.TLabel", foreground=c["muted"], font=(FONT, 9))

        s.configure("TButton", background=c["card2"], foreground=c["fg"], borderwidth=0,
                    focusthickness=0, padding=(14, 7), relief="flat")
        s.map("TButton",
              background=[("disabled", c["card"]), ("pressed", c["border"]), ("active", c["hover"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Accent.TButton", background=c["accent"], foreground=c["on_accent"],
                    font=(FONT, 10, "bold"), padding=(16, 9))
        s.map("Accent.TButton",
              background=[("disabled", c["card2"]), ("pressed", c["accent_hover"]),
                          ("active", c["accent_hover"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Icon.TButton", padding=(9, 5))

        s.configure("Seg.Toolbutton", background=c["card2"], foreground=c["fg"], borderwidth=0,
                    padding=(22, 6), anchor="center", font=(FONT, 10))
        s.map("Seg.Toolbutton",
              background=[("selected", c["accent"]), ("active", c["hover"])],
              foreground=[("selected", c["on_accent"])])

        s.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"], padding=(8, 5),
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        s.map("TEntry",
              fieldbackground=[("disabled", c["card"])],
              foreground=[("disabled", c["muted"])],
              bordercolor=[("invalid", c["err"]), ("focus", c["accent"])],
              lightcolor=[("invalid", c["err"]), ("focus", c["accent"])],
              darkcolor=[("invalid", c["err"]), ("focus", c["accent"])])

        s.configure("TCombobox", fieldbackground=c["field"], background=c["card2"],
                    foreground=c["fg"], arrowcolor=c["fg"], padding=(8, 5),
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        s.map("TCombobox",
              fieldbackground=[("readonly", c["field"]), ("disabled", c["card"])],
              foreground=[("disabled", c["muted"]), ("readonly", c["fg"])],
              selectbackground=[("readonly", c["field"])],
              selectforeground=[("readonly", c["fg"])],
              background=[("active", c["hover"])],
              bordercolor=[("focus", c["accent"])],
              lightcolor=[("focus", c["accent"])], darkcolor=[("focus", c["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", c["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
        self.root.option_add("*TCombobox*Listbox.font", base)
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        s.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["card"],
                    bordercolor=c["bg"], lightcolor=c["accent"], darkcolor=c["accent"], thickness=4)

    def _card(self, parent, title):
        outer = tk.Frame(parent, bg=self.c["card"], highlightthickness=1,
                         highlightbackground=self.c["border"], highlightcolor=self.c["border"])
        outer.pack(fill="x", pady=(0, 12))
        inner = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12, 16, 14))
        inner.pack(fill="both", expand=True)
        ttk.Label(inner, text=title, style="Section.Card.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        return inner

    # ---- layout ----------------------------------------------------------- #

    def _build(self):
        c = self.c
        main = ttk.Frame(self.root, padding=(18, 16, 18, 10))
        main.pack(fill="both", expand=True)

        # Header
        head = ttk.Frame(main)
        head.pack(fill="x", pady=(0, 14))
        ttk.Label(head, text=APP_NAME, style="Header.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{VERSION}", style="Sub.TLabel").pack(side="left", padx=(8, 0), pady=(8, 0))
        badge_color = c["ok"] if self.admin else c["warn"]
        tk.Label(head, text=" Administrator " if self.admin else " Not elevated ",
                 bg=badge_color, fg="#ffffff", font=(FONT, 8, "bold"),
                 padx=6, pady=2).pack(side="right", pady=(6, 0))

        # Adapter card
        card = self._card(main, "Network adapter")
        card.columnconfigure(0, weight=1)
        self.adapter_var = tk.StringVar()
        self.adapter_combo = ttk.Combobox(card, textvariable=self.adapter_var, state="readonly",
                                          font=(FONT, 10), width=38)
        self.adapter_combo.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.adapter_combo.bind("<<ComboboxSelected>>", lambda e: self._on_adapter_selected())
        self.refresh_btn = ttk.Button(card, text="⟳", style="Icon.TButton", width=3,
                                      command=self.refresh)
        self.refresh_btn.grid(row=1, column=3, sticky="e", padx=(8, 0))

        info = ttk.Frame(card, style="Card.TFrame")
        info.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        info.columnconfigure(1, weight=1)
        info.columnconfigure(3, weight=1)
        self.info_vars = {}
        rows = [("status", "Status", "mode", "Mode"),
                ("ip", "IP address", "gateway", "Gateway"),
                ("mask", "Subnet mask", "dns", "DNS"),
                ("mac", "MAC", "speed", "Link speed")]
        for r, (k1, l1, k2, l2) in enumerate(rows):
            for col, (key, label) in enumerate(((k1, l1), (k2, l2))):
                ttk.Label(info, text=label, style="Key.Card.TLabel").grid(
                    row=r * 2, column=col * 2, columnspan=2, sticky="w", padx=(0, 12))
                var = tk.StringVar(value="—")
                lbl = ttk.Label(info, textvariable=var, style="Value.Card.TLabel")
                lbl.grid(row=r * 2 + 1, column=col * 2, columnspan=2, sticky="w",
                         padx=(0, 12), pady=(0, 6))
                self.info_vars[key] = var
                if key == "status":
                    self.status_value_label = lbl
        self.use_current_btn = ttk.Button(card, text="Copy current settings to form",
                                          command=self.use_current)
        self.use_current_btn.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # Configuration card
        card = self._card(main, "Configuration")
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Profile", style="Key.Card.TLabel").grid(row=1, column=0, sticky="w")
        prof_row = ttk.Frame(card, style="Card.TFrame")
        prof_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(2, 12))
        prof_row.columnconfigure(0, weight=1)
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(prof_row, textvariable=self.profile_var,
                                          state="readonly", font=(FONT, 10))
        self.profile_combo.grid(row=0, column=0, sticky="ew")
        self.profile_combo.bind("<<ComboboxSelected>>", lambda e: self._on_profile_selected())
        ttk.Button(prof_row, text="Save", command=self.save_profile).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(prof_row, text="Delete", command=self.delete_profile).grid(row=0, column=2, padx=(6, 0))
        self._reload_profiles()

        seg = ttk.Frame(card, style="Card.TFrame")
        seg.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        self.mode_var = tk.StringVar(value="dhcp")
        ttk.Radiobutton(seg, text="DHCP (automatic)", value="dhcp", variable=self.mode_var,
                        style="Seg.Toolbutton", command=lambda: self._set_mode("dhcp")).pack(side="left")
        ttk.Radiobutton(seg, text="Static IP", value="static", variable=self.mode_var,
                        style="Seg.Toolbutton", command=lambda: self._set_mode("static")).pack(side="left")
        ttk.Button(seg, text="Reset defaults", command=self.reset_defaults).pack(side="right")

        self.field_vars, self.entries = {}, {}
        for i, (key, label) in enumerate(FIELDS):
            row = 4 + i
            ttk.Label(card, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=4)
            var = tk.StringVar(value=DEFAULTS[key])
            entry = ttk.Entry(card, textvariable=var, font=(FONT, 10))
            entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
            var.trace_add("write", lambda *_a, k=key: self._validate_field(k))
            self.field_vars[key], self.entries[key] = var, entry
        self.entries["ip"].bind("<FocusOut>", lambda e: self._expand_cidr())
        ttk.Label(card, text="Tip: enter 192.168.1.50/24 to fill the subnet mask automatically.",
                  style="Hint.Card.TLabel").grid(row=4 + len(FIELDS), column=0, columnspan=4,
                                                 sticky="w", pady=(4, 0))

        # Actions
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(2, 8))
        self.apply_btn = ttk.Button(actions, text="Apply", style="Accent.TButton", command=self.apply)
        self.apply_btn.pack(side="left", fill="x", expand=True)
        self.renew_btn = ttk.Button(actions, text="Renew DHCP", command=self.renew)
        self.renew_btn.pack(side="left", padx=(8, 0))
        self.ping_btn = ttk.Button(actions, text="Ping gateway", command=self.ping_gateway)
        self.ping_btn.pack(side="left", padx=(8, 0))

        # Status bar
        status = ttk.Frame(main)
        status.pack(fill="x")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=120)
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(status, textvariable=self.status_var, style="Status.TLabel",
                                      wraplength=440, justify="left")
        self.status_label.pack(side="left", fill="x", expand=True)

        self.action_buttons = [self.apply_btn, self.renew_btn, self.ping_btn,
                               self.refresh_btn, self.use_current_btn]

    # ---- background tasks ------------------------------------------------- #

    def _run_task(self, message, fn, done):
        if self.busy:
            return
        self._set_busy(True, message)

        def worker():
            try:
                result = (True, fn())
            except Exception as exc:  # reported in the UI
                result = (False, exc)
            self.tasks.put((done, result))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_tasks(self):
        try:
            while True:
                done, (ok, value) = self.tasks.get_nowait()
                self._set_busy(False)
                done(ok, value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_tasks)

    def _set_busy(self, busy, message=None):
        self.busy = busy
        for b in self.action_buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.progress.pack(side="right")
            self.progress.start(12)
            self._status(message or "Working…", "muted")
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _status(self, text, kind="muted"):
        self.status_var.set(text)
        self.status_label.configure(foreground=self.c.get(kind, self.c["muted"]))

    # ---- adapters --------------------------------------------------------- #

    def refresh(self, then=None):
        selected = self.display_to_name.get(self.adapter_var.get()) or self.settings.get("last_adapter", "")

        def done(ok, result):
            if not ok:
                self._status(f"Couldn't read adapters: {result}", "err")
                return
            value, warning = result
            self.adapters = {a["name"]: a for a in value}
            self.display_to_name = {}
            labels = []
            for a in value:
                label = f"{a['name']}   ·   {self._status_text(a['status'])}"
                self.display_to_name[label] = a["name"]
                labels.append(label)
            self.adapter_combo["values"] = labels
            name = selected if selected in self.adapters else None
            if not name:
                up = [a["name"] for a in value if a["status"].lower() == "up"]
                name = up[0] if up else (value[0]["name"] if value else None)
            if name:
                self.adapter_var.set(next(l for l, n in self.display_to_name.items() if n == name))
                self._on_adapter_selected(save=False)
            else:
                self.adapter_var.set("")
            if warning:
                self._status(warning, "warn")
            elif then:
                then()
            else:
                self._status(f"Found {len(value)} adapter(s).", "muted")

        self._run_task("Reading adapters…", query_adapters, done)

    @staticmethod
    def _status_text(status):
        return {"up": "Connected", "disconnected": "Disconnected",
                "disabled": "Disabled"}.get(status.lower(), status or "Unknown")

    def current_adapter(self):
        return self.adapters.get(self.display_to_name.get(self.adapter_var.get(), ""))

    def _on_adapter_selected(self, save=True):
        a = self.current_adapter()
        if not a:
            return
        dash = "—"
        status = a["status"].lower()
        self.info_vars["status"].set(self._status_text(a["status"]))
        self.status_value_label.configure(foreground=self.c["ok"] if status == "up" else
                                          self.c["warn"] if status == "disconnected" else self.c["muted"])
        mode = {"Enabled": "DHCP", "Disabled": "Static"}.get(a["dhcp"], dash)
        self.info_vars["mode"].set(mode)
        self.info_vars["ip"].set(a["ip"] or dash)
        self.info_vars["mask"].set(prefix_to_mask(a["prefix"]) if a["ip"] else dash)
        self.info_vars["gateway"].set(a["gateway"] or dash)
        self.info_vars["dns"].set(", ".join(a["dns"][:2]) or dash)
        self.info_vars["mac"].set(a["mac"] or dash)
        self.info_vars["speed"].set(a["speed"] if status == "up" and a["speed"] else dash)
        if save:
            self.settings["last_adapter"] = a["name"]
            self._save_settings()

    def use_current(self):
        a = self.current_adapter()
        if not a or not a["ip"]:
            self._status("The selected adapter has no IPv4 address to copy.", "warn")
            return
        self.mode_var.set("static")
        self._set_mode("static")
        self.field_vars["ip"].set(a["ip"])
        self.field_vars["mask"].set(prefix_to_mask(a["prefix"]))
        self.field_vars["gateway"].set(a["gateway"])
        self.field_vars["dns1"].set(a["dns"][0] if a["dns"] else "")
        self.field_vars["dns2"].set(a["dns"][1] if len(a["dns"]) > 1 else "")
        self._status("Copied current settings into the form.", "muted")

    # ---- form ------------------------------------------------------------- #

    def _set_mode(self, mode):
        self.mode_var.set(mode)
        for entry in self.entries.values():
            entry.state(["!disabled"] if mode == "static" else ["disabled"])
        self.apply_btn.configure(text="Apply DHCP" if mode == "dhcp" else "Apply static IP")

    def _validate_field(self, key):
        value = self.field_vars[key].get().strip()
        ok = True
        if value:
            try:
                if key == "ip":
                    parse_ip_field(value)
                elif key == "mask":
                    ok = valid_mask(value)
                else:
                    parse_ipv4(value)
            except ValueError:
                ok = False
        self.entries[key].state(["!invalid"] if ok else ["invalid"])

    def _expand_cidr(self):
        text = self.field_vars["ip"].get().strip()
        if "/" in text:
            try:
                ip, mask = parse_ip_field(text)
                self.field_vars["ip"].set(ip)
                self.field_vars["mask"].set(mask)
            except ValueError:
                pass

    def reset_defaults(self):
        for key, value in DEFAULTS.items():
            self.field_vars[key].set(value)
        self.profile_var.set("")
        self._status("Fields reset to defaults.", "muted")

    def _form(self):
        return {k: v.get() for k, v in self.field_vars.items()}

    # ---- profiles --------------------------------------------------------- #

    def _reload_profiles(self):
        names = [p["name"] for p in self.settings["profiles"]]
        self.profile_combo["values"] = names
        if self.profile_var.get() not in names:
            self.profile_var.set("")

    def _save_settings(self):
        try:
            save_settings(self.settings)
        except OSError as exc:
            self._status(f"Couldn't save settings: {exc}", "err")

    def _on_profile_selected(self):
        name = self.profile_var.get()
        prof = next((p for p in self.settings["profiles"] if p["name"] == name), None)
        if not prof:
            return
        mode = prof.get("mode", "static")
        self._set_mode("static")  # enable entries so they can be filled
        for key in self.field_vars:
            self.field_vars[key].set(prof.get(key, DEFAULTS[key]))
        self._set_mode(mode)
        self._status(f"Loaded profile “{name}”. Click Apply to use it.", "muted")

    def save_profile(self):
        mode = self.mode_var.get()
        data = {"mode": mode, **{k: v.strip() for k, v in self._form().items()}}
        if mode == "static":
            self._expand_cidr()
            cfg, errors, _ = validate_static(self._form())
            if errors:
                messagebox.showerror(APP_NAME, "Fix these before saving:\n\n" + "\n".join(errors))
                return
            data.update({k: cfg[k] for k in ("ip", "mask", "gateway", "dns1", "dns2")})
        name = NameDialog(self, "Save profile", "Profile name", self.profile_var.get()).result
        if not name:
            return
        profiles = self.settings["profiles"]
        existing = next((p for p in profiles if p["name"] == name), None)
        if existing and not messagebox.askyesno(APP_NAME, f"Overwrite profile “{name}”?"):
            return
        if existing:
            existing.clear()
            existing.update({"name": name, **data})
        else:
            profiles.append({"name": name, **data})
            profiles.sort(key=lambda p: p["name"].lower())
        self._save_settings()
        self._reload_profiles()
        self.profile_var.set(name)
        self._status(f"Saved profile “{name}”.", "ok")

    def delete_profile(self):
        name = self.profile_var.get()
        if not name:
            self._status("Select a profile to delete.", "warn")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete profile “{name}”?"):
            return
        self.settings["profiles"] = [p for p in self.settings["profiles"] if p["name"] != name]
        self._save_settings()
        self.profile_var.set("")
        self._reload_profiles()
        self._status(f"Deleted profile “{name}”.", "muted")

    # ---- actions ---------------------------------------------------------- #

    def _require_admin(self):
        if self.admin or not IS_WINDOWS:
            return True
        if messagebox.askyesno(APP_NAME, "Changing network settings requires administrator rights.\n\n"
                                         "Restart IPv4 Switcher as administrator?"):
            if relaunch_as_admin():
                self.root.destroy()
        return False

    def apply(self):
        adapter = self.current_adapter()
        if not adapter:
            self._status("Select an adapter first.", "warn")
            return
        if not self._require_admin():
            return

        if self.mode_var.get() == "dhcp":
            cfg = {"mode": "dhcp"}
            summary = "DHCP"
        else:
            self._expand_cidr()
            cfg, errors, warnings = validate_static(self._form())
            if errors:
                messagebox.showerror(APP_NAME, "\n".join(errors))
                return
            if warnings and not messagebox.askyesno(APP_NAME, "\n".join(warnings) + "\n\nApply anyway?"):
                return
            summary = f"{cfg['ip']} / {cfg['mask']}"

        try:
            cmds = build_commands(adapter["name"], cfg, adapter)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        name = adapter["name"]

        def done(ok, value):
            if not ok:
                self._status(f"Failed to apply settings to {name}.", "err")
                messagebox.showerror(APP_NAME, f"Couldn't apply settings to “{name}”.\n\n{value}")
                return
            msg = f"Applied {summary} to {name}."
            # DHCP needs a moment to get a lease before the refresh is meaningful.
            delay = 2500 if cfg["mode"] == "dhcp" else 800
            self._status(msg + " Refreshing…", "ok")
            self.root.after(delay, lambda: self.refresh(then=lambda: self._status(msg, "ok")))

        self._run_task(f"Applying {summary} to {name}…", lambda: run_commands(cmds), done)

    def renew(self):
        adapter = self.current_adapter()
        if not adapter:
            self._status("Select an adapter first.", "warn")
            return
        if adapter["dhcp"] == "Disabled":
            self._status("This adapter uses a static IP. Switch it to DHCP first.", "warn")
            return
        if not self._require_admin():
            return
        name = adapter["name"]

        def work():
            run(f'ipconfig /release "{name}"', timeout=30)
            rc, out = run(f'ipconfig /renew "{name}"', timeout=90)
            if rc != 0:
                raise RuntimeError(out or "ipconfig /renew failed")

        def done(ok, value):
            if not ok:
                self._status(f"DHCP renew failed: {str(value).splitlines()[-1] if str(value) else ''}", "err")
                messagebox.showerror(APP_NAME, f"DHCP renew failed for “{name}”.\n\n{value}")
                return
            self.refresh(then=lambda: self._status(f"Renewed DHCP lease on {name}.", "ok"))

        self._run_task(f"Renewing DHCP lease on {name}…", work, done)

    def ping_gateway(self):
        adapter = self.current_adapter()
        target = (adapter or {}).get("gateway") or self.field_vars["gateway"].get().strip()
        try:
            target = parse_ipv4(target)
        except ValueError:
            self._status("No gateway to ping.", "warn")
            return

        def work():
            _rc, out = run(["ping", "-n", "3", "-w", "1000", target], timeout=20)
            return out

        def done(ok, value):
            if not ok:
                self._status(f"Ping failed: {value}", "err")
            elif "ttl=" in str(value).lower():
                self._status(f"{target} is reachable.", "ok")
            else:
                self._status(f"No reply from {target}.", "err")

        self._run_task(f"Pinging {target}…", work, done)


def main():
    if IS_WINDOWS and not is_admin() and "--no-elevate" not in sys.argv:
        if relaunch_as_admin():
            sys.exit(0)
    enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
