import os
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess

def get_adapters():
    result = subprocess.check_output("netsh interface show interface", shell=True).decode()
    adapters = []
    for line in result.splitlines():
        if "Connected" in line or "Disconnected" in line:
            parts = line.split()
            adapters.append(" ".join(parts[3:]))
    return adapters

def apply_settings():
    adapter = adapter_var.get()

    if mode_var.get() == "DHCP":
        os.system(f'netsh interface ipv4 set address name="{adapter}" dhcp')
        os.system(f'netsh interface ipv4 set dns name="{adapter}" dhcp')
        messagebox.showinfo("Success", "Switched to DHCP")
    else:
        ip = ip_entry.get()
        subnet = subnet_entry.get()
        gateway = gateway_entry.get()
        dns = dns_entry.get()

        os.system(f'netsh interface ipv4 set address name="{adapter}" static {ip} {subnet} {gateway}')
        os.system(f'netsh interface ipv4 set dns name="{adapter}" static {dns}')
        messagebox.showinfo("Success", "Static IP applied")

def refresh_adapters():
    adapters = get_adapters()
    adapter_menu['values'] = adapters
    if adapters:
        adapter_var.set(adapters[0])

root = tk.Tk()
root.title("IP Switcher")
root.geometry("320x320")

# Adapter
tk.Label(root, text="Adapter").pack()
adapter_var = tk.StringVar()
adapter_menu = ttk.Combobox(root, textvariable=adapter_var)
adapter_menu.pack()

# Mode
mode_var = tk.StringVar(value="DHCP")
tk.Radiobutton(root, text="DHCP", variable=mode_var, value="DHCP").pack()
tk.Radiobutton(root, text="Static", variable=mode_var, value="Static").pack()

# Fields
tk.Label(root, text="IP Address").pack()
ip_entry = tk.Entry(root)
ip_entry.pack()

tk.Label(root, text="Subnet Mask").pack()
subnet_entry = tk.Entry(root)
subnet_entry.pack()

tk.Label(root, text="Gateway").pack()
gateway_entry = tk.Entry(root)
gateway_entry.pack()

tk.Label(root, text="DNS").pack()
dns_entry = tk.Entry(root)
dns_entry.pack()

# Buttons
tk.Button(root, text="Apply", command=apply_settings).pack(pady=5)
tk.Button(root, text="Refresh Adapters", command=refresh_adapters).pack(pady=5)

refresh_adapters()

root.mainloop()