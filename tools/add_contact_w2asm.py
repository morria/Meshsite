#!/tmp/mtvenv/bin/python
"""Inject W2ASM's contact (with verified public key) into the local radio.

Key = W2ASM's pre-flash pubkey, sha256 fingerprint
b61d b5e0 2dbe 6959 da27 08fa 65cb 4a7d — confirmed by the owner 2026-08-25.
Run while the meshsites server is stopped (needs the serial port).
"""
import base64, hashlib, time
from meshtastic.serial_interface import SerialInterface
from meshtastic.protobuf import admin_pb2

DEV = "/dev/serial/by-id/usb-RAKwireless_WisCore_RAK4631_Board_1A07FD7B07DA1E19-if00"
KEY_B64 = "ZawPXdyfGr+RkUUc9EyGeFDhx+6gW5/133FMStnYUBo="

key = base64.b64decode(KEY_B64)
fp = hashlib.sha256(key).hexdigest()
print("injecting key, fp:", " ".join(fp[i:i+4] for i in range(0, 32, 4)))
iface = SerialInterface(devPath=DEV)
time.sleep(2)
a = admin_pb2.AdminMessage()
sc = a.add_contact
sc.node_num = 0x8AC3C723
sc.user.id = "!8ac3c723"
sc.user.long_name = "W2ASM"
sc.user.short_name = "🦗"
sc.user.public_key = key
iface.localNode._sendAdmin(a)
print("add_contact sent; verifying...")
time.sleep(5)
iface.close()
iface = SerialInterface(devPath=DEV)
time.sleep(3)
u = ((iface.nodesByNum.get(0x8AC3C723) or {}).get("user") or {})
if u.get("publicKey"):
    h = hashlib.sha256(base64.b64decode(u["publicKey"])).hexdigest()
    print("VERIFIED:", u.get("longName"), " ".join(h[i:i+4] for i in range(0, 32, 4)))
else:
    print("NOT PRESENT — injection did not take")
iface.close()
