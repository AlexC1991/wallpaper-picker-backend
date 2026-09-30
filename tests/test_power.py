"""Tests for power-source detection (pause-on-battery)."""

from __future__ import annotations

from pathlib import Path

from wallpaper_picker import power


def _supply(root: Path, name: str, **fields: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    for k, v in fields.items():
        (d / k).write_text(v)
    return d


def test_parse_capacity():
    assert power.parse_capacity(" 87\n") == 87
    assert power.parse_capacity("0") == 0
    assert power.parse_capacity("100") == 100
    assert power.parse_capacity("150") is None
    assert power.parse_capacity("nope") is None
    assert power.parse_capacity("") is None


def test_classify():
    assert power.classify("Discharging") == (True, False)
    assert power.classify("Charging") == (False, True)
    assert power.classify("Full") == (False, True)
    assert power.classify("Not charging") == (False, True)
    assert power.classify("Unknown") == (False, False)
    assert power.classify("") == (False, False)


def test_read_power_discharging(tmp_path):
    _supply(tmp_path, "BAT0", type="Battery\n", capacity="55\n", status="Discharging\n")
    st = power.read_power(tmp_path)
    assert st.has_battery and st.percent == 55
    assert st.on_battery is True
    assert st.to_dict()["on_battery"] is True


def test_read_power_charging_on_mains(tmp_path):
    _supply(tmp_path, "BAT0", type="Battery\n", capacity="55\n", status="Charging\n")
    _supply(tmp_path, "AC", type="Mains\n", online="1\n")
    st = power.read_power(tmp_path)
    assert st.on_battery is False and st.plugged is True


def test_read_power_no_battery_desktop(tmp_path):
    _supply(tmp_path, "AC", type="Mains\n", online="1\n")
    st = power.read_power(tmp_path)
    assert st.has_battery is False
    assert st.on_battery is False


def test_read_power_empty_and_missing_root(tmp_path):
    assert power.read_power(tmp_path).has_battery is False
    assert power.read_power(tmp_path / "nope").has_battery is False


def test_read_power_ignores_unreadable_fields(tmp_path):
    """A real battery whose status/capacity cannot be read: present, but never 'on battery'."""
    _supply(tmp_path, "BAT0", type="Battery\n", scope="System\n")
    st = power.read_power(tmp_path)
    assert st.has_battery is True and st.percent is None and st.on_battery is False
    assert power.should_pause(st, pause_on_battery=True, threshold=0) is False


# --------------------------------------------------------------------------- decision

def test_should_pause_off_by_default():
    st = power.PowerState(has_battery=True, discharging=True, percent=50)
    assert power.should_pause(st, pause_on_battery=False) is False


def test_should_pause_when_discharging():
    st = power.PowerState(has_battery=True, discharging=True, percent=90)
    assert power.should_pause(st, pause_on_battery=True) is True


def test_should_not_pause_when_plugged():
    st = power.PowerState(has_battery=True, plugged=True, percent=10)
    assert power.should_pause(st, pause_on_battery=True) is False


def test_threshold_pauses_when_status_unknown():
    st = power.PowerState(has_battery=True, percent=15)
    assert power.should_pause(st, pause_on_battery=True, threshold=20) is True
    assert power.should_pause(st, pause_on_battery=True, threshold=10) is False


def test_no_battery_never_pauses():
    assert power.should_pause(power.PowerState(), pause_on_battery=True) is False

# ------------------------------------------------------- peripheral batteries (real bug)

def _supply_with_scope(root, name, scope, **fields):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (d / k).write_text(v)
    if scope is not None:
        (d / "scope").write_text(scope)
    return d


def test_wireless_mouse_is_not_the_system_battery(tmp_path):
    """A Logitech mouse (hidpp) reports type=Battery, status=Discharging. Treating that as
    the machine's power source would pause wallpapers on a wall-powered desktop."""
    _supply_with_scope(tmp_path, "hidpp_battery_0", "Device",
                       type="Battery\n", status="Discharging\n", capacity="55\n")
    st = power.read_power(tmp_path)
    assert st.has_battery is False
    assert st.on_battery is False
    assert power.should_pause(st, pause_on_battery=True) is False


def test_peripheral_scope_variants_are_ignored(tmp_path):
    for i, scope in enumerate(["device", "peripheral", "DEVICE"]):
        root = tmp_path / f"case{i}"
        _supply_with_scope(root, "periph", scope, type="Battery\n", status="Discharging\n",
                           capacity="30\n")
        assert power.read_power(root).has_battery is False


def test_scope_less_battery_without_capacity_is_a_peripheral(tmp_path):
    _supply_with_scope(tmp_path, "headset", None, type="Battery\n", status="Discharging\n")
    assert power.read_power(tmp_path).has_battery is False


def test_real_battery_alongside_a_mouse_still_counts(tmp_path):
    _supply_with_scope(tmp_path, "hidpp_battery_0", "Device",
                       type="Battery\n", status="Discharging\n", capacity="5\n")
    _supply_with_scope(tmp_path, "BAT0", "System",
                       type="Battery\n", status="Discharging\n", capacity="42\n")
    st = power.read_power(tmp_path)
    assert st.has_battery is True
    assert st.percent == 42          # the mouse's 5% must not win
    assert st.on_battery is True


def test_this_machines_layout_is_a_desktop(tmp_path):
    """Reproduces the actual sysfs layout found here: only a hidpp mouse."""
    _supply_with_scope(tmp_path, "hidpp_battery_0", None,
                       type="Battery\n", status="Discharging\n", online="1\n")
    st = power.read_power(tmp_path)
    assert st.has_battery is False and st.on_battery is False
