"""Windows toolkit: parsers are tested on real command output (English and Spanish Windows)."""

import pytest

from orion.tools import windows as w
from orion.tools.platform import IS_WIN
from orion.tools.registry import ToolError

IFACES_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 1b2c3d4e-0000-0000-0000-000000000000
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : connected
    SSID                   : Casa Lluc 5G
    BSSID                  : 11:22:33:44:55:66
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Signal                 : 87%
    Profile                : Casa Lluc 5G
"""

IFACES_ES = """
Hay 1 interfaz en el sistema:

    Nombre                 : Wi-Fi
    Descripción            : Realtek 8822CE Wireless LAN
    Estado                 : desconectado
    Tipo de radio          : 802.11ac
"""

NETWORKS_ES = """
Nombre de interfaz : Wi-Fi
Actualmente hay 3 redes visibles.

SSID 1 : Casa Lluc 5G
    Tipo de red             : Infraestructura
    Autenticación           : WPA2-Personal
    Cifrado                 : CCMP
    BSSID 1                 : 11:22:33:44:55:66
         Señal              : 87%
         Tipo de radio      : 802.11ax
    BSSID 2                 : 11:22:33:44:55:67
         Señal              : 91%

SSID 2 : Cafe Gratis
    Tipo de red             : Infraestructura
    Autenticación           : Abierta
    Cifrado                 : Ninguno
    BSSID 1                 : 22:33:44:55:66:77
         Señal              : 40%

SSID 3 :
    Autenticación           : WPA3-Personal
    Cifrado                 : CCMP
    BSSID 1                 : 33:44:55:66:77:88
         Señal              : 10%
"""


def test_wifi_parsers_any_language():
    en = w.parse_wlan_interfaces(IFACES_EN)
    assert en == [{"name": "Wi-Fi", "ssid": "Casa Lluc 5G", "signal": 87, "connected": True}]
    es = w.parse_wlan_interfaces(IFACES_ES)
    assert es == [{"name": "Wi-Fi", "ssid": "", "signal": None, "connected": False}]
    nets = w.parse_wlan_networks(NETWORKS_ES)
    assert [n["ssid"] for n in nets] == ["Casa Lluc 5G", "Cafe Gratis", "(hidden network)"]
    assert nets[0] == {"ssid": "Casa Lluc 5G", "signal": 91, "security": "WPA2-Personal"}
    assert nets[1]["security"] == "Abierta" and nets[2]["security"] == "WPA3-Personal"


def test_wifi_profile_xml():
    xml = w._wifi_profile_xml("Casa & Co", "p<ss", "WPA2-Personal")
    assert "<authentication>WPA2PSK</authentication>" in xml and "Casa &amp; Co" in xml and "p&lt;ss" in xml
    assert "<authentication>WPA3SAE</authentication>" in w._wifi_profile_xml("x", "y", "WPA3-Personal")
    assert "<authentication>open</authentication>" in w._wifi_profile_xml("x", "", "Abierta")


POWERCFG = """
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Equilibrado) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (Alto rendimiento)
"""


def test_powercfg_parser():
    plans = w.parse_powercfg_list(POWERCFG)
    assert plans == [{"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "name": "Equilibrado", "active": True},
                     {"guid": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "name": "Alto rendimiento", "active": False}]


WINGET_SEARCH = """\r   - \r   \\ \r
Name             Id                     Version   Match         Source
-----------------------------------------------------------------------
VLC media player VideoLAN.VLC           3.0.21    Moniker: vlc  winget
VLC UWP          9NBLGGH4VVNH           Unknown                 msstore
"""

WINGET_UPGRADE = """
Nombre                 Id                       Versión     Disponible  Origen
-------------------------------------------------------------------------------
Google Chrome          Google.Chrome            129.0.1     130.0.2     winget
Microsoft Edge         Microsoft.Edge           129.0       130.0       winget
2 actualizaciones disponibles.
"""


def test_winget_parser():
    rows = w.parse_winget_table(WINGET_SEARCH)
    assert rows[0] == {"name": "VLC media player", "id": "VideoLAN.VLC", "version": "3.0.21", "match": "Moniker: vlc",
                       "source": "winget"}
    assert rows[1]["id"] == "9NBLGGH4VVNH"
    up = w.parse_winget_table(WINGET_UPGRADE, "upgrade")
    assert [(r["id"], r["available"]) for r in up] == [("Google.Chrome", "130.0.2"), ("Microsoft.Edge", "130.0")]


def test_ping_parser():
    en = "Reply from 1.1.1.1: bytes=32 time=12ms TTL=57\nReply from 1.1.1.1: bytes=32 time<1ms TTL=57\nRequest timed out."
    assert w.parse_ping(en, 3) == {"replies": 2, "sent": 3, "loss_percent": 33, "avg_ms": 6, "min_ms": 1, "max_ms": 12}
    es = "Respuesta desde 8.8.8.8: bytes=32 tiempo=20ms TTL=117\nRespuesta desde 8.8.8.8: bytes=32 tiempo=22ms TTL=117"
    assert w.parse_ping(es, 2)["avg_ms"] == 21


BATTERY = """<?xml version="1.0" encoding="utf-8"?>
<BatteryReport xmlns="http://schemas.microsoft.com/battery/2012">
  <Batteries><Battery><Id>DELL</Id><DesignCapacity>56000</DesignCapacity>
  <FullChargeCapacity>44800</FullChargeCapacity><CycleCount>312</CycleCount></Battery></Batteries>
</BatteryReport>"""


def test_battery_report_parser():
    assert w.parse_battery_report(BATTERY) == {"design_capacity_mWh": 56000, "full_charge_mWh": 44800,
                                               "cycle_count": 312, "health_percent": 80}
    with pytest.raises(ToolError):
        w.parse_battery_report('<BatteryReport xmlns="http://schemas.microsoft.com/battery/2012"><Batteries/></BatteryReport>')


@pytest.mark.skipif(IS_WIN, reason="checks the non-Windows guard")
def test_windows_only_tools_say_so():
    for fn in (lambda: w.set_theme("dark"), lambda: w.wifi(), lambda: w.power_plan(), lambda: w.startup_apps()):
        with pytest.raises(ToolError, match="Windows"):
            fn()


def test_risk_levels_and_kit():
    from orion.tools.registry import decorated_tools

    tools = {t.name: t for t in decorated_tools() if t.kit == "windows"}
    assert {"wifi", "bluetooth", "set_theme", "set_wallpaper", "install_app", "uninstall_app", "startup_apps",
            "internet_speed_test", "arrange_window", "check_windows_updates", "battery_health"} <= set(tools)
    assert tools["uninstall_app"].risk == "danger" and tools["empty_recycle_bin"].risk == "danger"
    assert tools["install_app"].risk == "confirm" and tools["network_info"].risk == "safe"
