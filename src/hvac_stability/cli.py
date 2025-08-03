import base64
import json
import logging
import os
import datetime
from pathlib import Path
from typing import Annotated

import typer
from attrs import define
from environ import config, var
from pykumo import KumoCloudAccount, PyKumo
from pykumo.schedule import UnitSchedule
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()

# Configure logging to suppress debug/info messages
logging.basicConfig(level=logging.ERROR)


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9 / 5) + 32


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (fahrenheit - 32) * 5 / 9


def format_temperature(temp: float | None, unit: str) -> str:
    """Format temperature with proper unit and conversion."""
    if temp is None:
        return "N/A"

    if unit.upper() == "F":
        # PyKumo returns Celsius, convert to Fahrenheit for display
        temp_display = celsius_to_fahrenheit(temp)
        return f"{temp_display:.1f}°F"
    else:
        # Display in Celsius as returned by PyKumo
        return f"{temp:.1f}°C"


@config(prefix="KUMO")
class Config:
    auth_username: str = var(default=None)
    auth_password: str = var(default=None)

    data_path: Path = var(default="~/.local/var/hvac_stability/")
    temperature_unit: str = var(default="F")  # "F" for Fahrenheit, "C" for Celsius

    @property
    def devices_file(self) -> Path:
        return Path(self.data_path).expanduser() / "devices.json"

    @property
    def credentials_file(self) -> Path:
        return Path(self.data_path).expanduser() / ".credentials"

    def load_stored_credentials(self) -> tuple[str | None, str | None]:
        """Load stored credentials from the credentials file."""
        creds_file = self.credentials_file
        if not creds_file.exists():
            return None, None

        try:
            with open(creds_file, "r") as f:
                encoded_data = f.read().strip()

            decoded_data = base64.b64decode(encoded_data).decode("utf-8")
            creds = json.loads(decoded_data)
            return creds.get("username"), creds.get("password")
        except (json.JSONDecodeError, Exception):
            return None, None

    def store_credentials(self, username: str, password: str) -> None:
        """Store credentials securely in the credentials file."""
        creds_file = self.credentials_file
        creds_file.parent.mkdir(parents=True, exist_ok=True)

        creds_data = {"username": username, "password": password}
        encoded_data = base64.b64encode(json.dumps(creds_data).encode("utf-8")).decode(
            "utf-8"
        )

        with open(creds_file, "w") as f:
            f.write(encoded_data)

        # Set restrictive permissions (owner read/write only)
        os.chmod(creds_file, 0o600)

    def get_auth_credentials(self) -> tuple[str | None, str | None]:
        """Get auth credentials from environment variables or stored file."""
        # First try environment variables
        if self.auth_username and self.auth_password:
            return self.auth_username, self.auth_password

        # Fall back to stored credentials
        return self.load_stored_credentials()


# Initialize configuration
app_config = Config()


@define
class DeviceSettings:
    """Current device settings."""

    mode: str | None = None
    heat_setpoint: float | None = None
    cool_setpoint: float | None = None
    fan_speed: str | None = None
    vane_direction: str | None = None

    @classmethod
    def from_device(cls, device: PyKumo) -> "DeviceSettings":
        """Create DeviceSettings from a PyKumo device."""
        device.update_status()
        return cls(
            mode=device.get_mode(),
            heat_setpoint=device.get_heat_setpoint(),
            cool_setpoint=device.get_cool_setpoint(),
            fan_speed=device.get_fan_speed(),
            vane_direction=device.get_vane_direction(),
        )

    @classmethod
    def from_schedule_settings(cls, schedule_settings) -> "DeviceSettings":
        """Create DeviceSettings from a schedule ScheduleSettings object."""
        return cls(
            mode=schedule_settings.mode,
            heat_setpoint=schedule_settings.set_point_heat,
            cool_setpoint=schedule_settings.set_point_cool,
            fan_speed=schedule_settings.fan_speed,
            vane_direction=schedule_settings.vane_dir,
        )

    def compare_to(
        self, other: "DeviceSettings", config: "Config" = None
    ) -> dict[str, tuple[str, str]]:
        """Compare this settings object to another, returning differences with proper formatting."""
        differences = {}

        for field in [
            "mode",
            "heat_setpoint",
            "cool_setpoint",
            "fan_speed",
            "vane_direction",
        ]:
            self_val = getattr(self, field)
            other_val = getattr(other, field)

            # Special handling for mode - ignore auto/autoCool/autoHeat variations
            if field == "mode":
                # Normalize auto modes for comparison
                self_normalized = self._normalize_auto_mode(self_val)
                other_normalized = self._normalize_auto_mode(other_val)

                if self_normalized != other_normalized:
                    differences[field] = (
                        str(self_val) if self_val is not None else "N/A",
                        str(other_val) if other_val is not None else "N/A",
                    )
            elif field in ["heat_setpoint", "cool_setpoint"]:
                # Format temperature values with proper units
                if config:
                    self_str = format_temperature(self_val, config.temperature_unit)
                    other_str = format_temperature(other_val, config.temperature_unit)
                else:
                    # Fallback to raw values if no config provided
                    self_str = str(self_val) if self_val is not None else "N/A"
                    other_str = str(other_val) if other_val is not None else "N/A"

                if self_val != other_val:
                    differences[field] = (self_str, other_str)
            else:
                # Handle None values and format for display
                self_str = str(self_val) if self_val is not None else "N/A"
                other_str = str(other_val) if other_val is not None else "N/A"

                if self_val != other_val:
                    differences[field] = (self_str, other_str)

        return differences

    def _normalize_auto_mode(self, mode: str | None) -> str | None:
        """Normalize auto mode variations (auto, autoCool, autoHeat) to 'auto' for comparison."""
        if mode is None:
            return None
        mode_lower = mode.lower()
        if mode_lower in ["auto", "autocool", "autoheat"]:
            return "auto"
        return mode


@define
class ScheduleAnalyzer:
    """Analyzes device schedules to determine expected settings."""

    @staticmethod
    def get_expected_settings(
        unit_schedule: UnitSchedule, target_time: datetime.datetime = None
    ) -> DeviceSettings | None:
        """Determine what settings should be active based on schedule and time."""
        if target_time is None:
            target_time = datetime.datetime.now()

        current_day = target_time.weekday()  # Monday = 0
        current_time = target_time.time()

        # Get active events and find applicable ones
        applicable_events = []

        for slot in unit_schedule:
            event = unit_schedule[slot]
            if event.active and event.in_use:
                if current_day in event.scheduled_days:
                    # This event applies to today
                    if event.scheduled_time <= current_time:
                        # This event has already triggered today
                        applicable_events.append((event.scheduled_time, slot, event))

        # If no events today, check yesterday for late events that might still apply
        if not applicable_events:
            yesterday = (target_time - datetime.timedelta(days=1)).weekday()
            for slot in unit_schedule:
                event = unit_schedule[slot]
                if event.active and event.in_use:
                    if yesterday in event.scheduled_days:
                        # Event from yesterday might still be active
                        applicable_events.append((event.scheduled_time, slot, event))

        if applicable_events:
            # Get the most recent event that should be active
            latest_time, latest_slot, latest_event = max(
                applicable_events, key=lambda x: x[0]
            )
            return DeviceSettings.from_schedule_settings(latest_event.settings)

        return None


@define
class HVACManager:
    config: "Config"
    connection: KumoCloudAccount

    devices: list[PyKumo] = []
    local_device_config: dict[str, dict] = {}

    @classmethod
    def create_with_auth(cls, config: "Config") -> "HVACManager":
        """Create HVACManager with authentication from stored credentials."""
        username, password = config.get_auth_credentials()

        if not username or not password:
            console.print(
                "✗ No credentials found. Please run 'hvac-stability login' first.",
                style="bold red",
            )
            raise typer.Exit(1)

        try:
            connection = KumoCloudAccount.Factory(username, password)
            return cls(config=config, connection=connection)
        except Exception as e:
            console.print(f"✗ Authentication failed: {e}", style="bold red")
            raise typer.Exit(1)

    def load_devices(self):
        """Load devices from cloud and merge with local configuration."""
        for _, device in self.connection.make_pykumos().items():
            self.devices.append(device)

        self._load_local_config()
        self._merge_device_config()

    def enable_scheduling_for_device(self, device: PyKumo) -> PyKumo:
        """Create a new PyKumo instance with scheduling enabled for the given device."""
        import base64

        # Create new PyKumo instance with scheduling enabled
        schedule_device = PyKumo(
            name=device.get_name(),
            addr=device._address,
            cfg_json={
                "password": base64.b64encode(device._security["password"]).decode(
                    "utf-8"
                ),
                "crypto_serial": device._security["crypto_serial"].hex(),
            },
            use_schedule=True,
        )
        return schedule_device

    def _load_local_config(self):
        """Load local device configuration from file."""
        data_file = self.config.devices_file

        if data_file.exists():
            with open(data_file, "r") as f:
                local_device_config = f.read()

            data = json.loads(local_device_config)
            self.local_device_config = data.get("devices", {})

    def _merge_device_config(self):
        """Merge local configuration with cloud devices."""
        for device in self.devices:
            serial = device.get_serial()
            if serial in self.local_device_config:
                local_config = self.local_device_config[serial]

                # If we have a stored IP address, update the device
                if "ip_address" in local_config and hasattr(device, "_address"):
                    device._address = local_config["ip_address"]
                    console.print(
                        f"✓ Updated device {serial} with IP: {local_config['ip_address']}",
                        style="dim green",
                    )

    def store_device_ip(self, device_serial: str, ip_address: str):
        """Store IP address for a specific device."""
        # Ensure data directory exists
        self.config.devices_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new one
        if self.config.devices_file.exists():
            with open(self.config.devices_file, "r") as f:
                data = json.loads(f.read())
        else:
            data = {"devices": {}}

        # Update device IP address
        if device_serial not in data["devices"]:
            data["devices"][device_serial] = {}

        data["devices"][device_serial]["ip_address"] = ip_address

        # Save back to file
        with open(self.config.devices_file, "w") as f:
            json.dump(data, f, indent=2)

        console.print(
            f"✓ Stored IP address {ip_address} for device {device_serial}",
            style="bold green",
        )

    def get_device_by_serial(self, serial: str) -> PyKumo | None:
        """Get a device by its serial number."""
        for device in self.devices:
            if device.get_serial() == serial:
                return device
        return None

    def get_device_by_name(self, name: str) -> PyKumo | None:
        """Get a device by its name (case-insensitive)."""
        for device in self.devices:
            if device.get_name().lower() == name.lower():
                return device
        return None

    def list_devices_simple(self) -> list[tuple[str, str]]:
        """Return a simple list of (serial, name) tuples."""
        return [(device.get_serial(), device.get_name()) for device in self.devices]


@app.command()
def login(
    username: Annotated[str, typer.Argument()] = None,
    password: Annotated[
        str, typer.Option("--password", "-p", prompt=True, hide_input=True)
    ] = None,
):
    """Login to the Kumo API and store credentials securely."""
    # Get username from argument, stored creds, or environment
    if not username:
        stored_username, _ = app_config.load_stored_credentials()
        username = username or stored_username or app_config.auth_username

    if not username:
        username = typer.prompt("Username")

    # Get password from option, stored creds, or environment
    if not password:
        _, stored_password = app_config.load_stored_credentials()
        password = password or stored_password or app_config.auth_password

    if not password:
        password = typer.prompt("Password", hide_input=True)

    try:
        # Test the credentials
        KumoCloudAccount.Factory(username, password)
        console.print("✓ Login successful!", style="bold green")

        # Store credentials on successful login
        app_config.store_credentials(username, password)
        console.print("✓ Credentials stored securely.", style="green")

    except Exception as e:
        console.print(f"✗ Login failed: {e}", style="bold red")
        raise typer.Exit(1)


@app.command()
def list(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed device information")
    ] = False,
):
    """List all devices."""
    manager = HVACManager.create_with_auth(app_config)
    manager.load_devices()

    if not manager.devices:
        console.print("[yellow]No devices found.[/yellow]")
        return

    if verbose:
        table = Table(
            title="HVAC Devices - Detailed View",
            show_header=True,
            header_style="bold blue",
        )
        table.add_column("Name", style="green", min_width=12)
        table.add_column("Serial", style="cyan", no_wrap=True)
        table.add_column("Temperature", style="red", justify="center")
        table.add_column("Mode", style="yellow", justify="center")
        table.add_column("Fan Speed", style="blue", justify="center")
        table.add_column("Status", style="magenta", justify="center")
        table.add_column("WiFi", style="dim", justify="center")
        table.add_column("IP Address", style="orange_red1", justify="center")

        for device in manager.devices:
            try:
                # Get actual device status information
                device_name = device.get_name()
                device_serial = device.get_serial()

                # Get current temperature (may need to update status first)
                try:
                    device.update_status()
                    temp = device.get_current_temperature()
                    temp_str = f"{temp}°F" if temp is not None else "N/A"
                except Exception:
                    temp_str = "N/A"

                # Get mode and fan speed
                try:
                    mode = device.get_mode() or "N/A"
                    fan_speed = device.get_fan_speed() or "N/A"
                    status = device.get_runstate() or "N/A"
                    wifi_rssi = device.get_wifi_rssi()
                    wifi_str = f"{wifi_rssi}dBm" if wifi_rssi is not None else "N/A"
                except Exception:
                    mode = fan_speed = status = wifi_str = "N/A"

                # Get stored IP address
                ip_address = "N/A"
                if device_serial in manager.local_device_config:
                    ip_address = manager.local_device_config[device_serial].get(
                        "ip_address", "N/A"
                    )

                table.add_row(
                    device_name,
                    device_serial,
                    temp_str,
                    str(mode),
                    str(fan_speed),
                    str(status),
                    wifi_str,
                    ip_address,
                )
            except Exception:
                # Fallback for devices that fail to provide info
                table.add_row(
                    device.get_name() if hasattr(device, "get_name") else "Unknown",
                    device.get_serial() if hasattr(device, "get_serial") else "N/A",
                    "Error",
                    "Error",
                    "Error",
                    "Error",
                    "Error",
                    "Error",
                )

        console.print(table)
    else:
        console.print(f"[green]Found {len(manager.devices)} device(s):[/green]")
        for i, device in enumerate(manager.devices, 1):
            device_name = device.get_name()
            console.print(f"  {i}. {device_name}")


@app.command()
def store_device_ip(
    device_identifier: Annotated[
        str, typer.Argument(help="Device serial number or name")
    ] = None,
    ip_address: Annotated[str, typer.Argument(help="IP address to store")] = None,
):
    """Store IP address for a specific device."""
    manager = HVACManager.create_with_auth(app_config)
    manager.load_devices()

    # If no device specified, show available devices
    if not device_identifier:
        console.print("[yellow]Available devices:[/yellow]")
        devices = manager.list_devices_simple()

        if not devices:
            console.print("[red]No devices found.[/red]")
            raise typer.Exit(1)

        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Index", style="cyan", justify="center")
        table.add_column("Serial", style="green")
        table.add_column("Name", style="yellow")

        for i, (serial, name) in enumerate(devices, 1):
            table.add_row(str(i), serial, name)

        console.print(table)

        # Get user selection
        choice = typer.prompt("Select device by index or enter serial/name")

        # Try to parse as index first
        try:
            index = int(choice) - 1
            if 0 <= index < len(devices):
                device_identifier = devices[index][0]  # Use serial
            else:
                console.print("[red]Invalid index.[/red]")
                raise typer.Exit(1)
        except ValueError:
            # Not an index, use as identifier
            device_identifier = choice

    # Find the device
    device = manager.get_device_by_serial(device_identifier)
    if not device:
        device = manager.get_device_by_name(device_identifier)

    if not device:
        console.print(f"[red]Device '{device_identifier}' not found.[/red]")
        raise typer.Exit(1)

    device_serial = device.get_serial()
    device_name = device.get_name()

    # Get IP address if not provided
    if not ip_address:
        ip_address = typer.prompt(
            f"Enter IP address for '{device_name}' ({device_serial})"
        )

    # Validate IP address format (basic validation)
    import re

    ip_pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    if not re.match(ip_pattern, ip_address):
        console.print(f"[red]Invalid IP address format: {ip_address}[/red]")
        raise typer.Exit(1)

    # Store the IP address
    manager.store_device_ip(device_serial, ip_address)
    console.print(
        f"[green]✓ IP address {ip_address} stored for device '{device_name}' ({device_serial})[/green]"
    )


@app.command()
def show_schedule(
    device_identifier: Annotated[
        str, typer.Argument(help="Device serial number or name")
    ] = None,
):
    """Show the current schedule for a device."""
    manager = HVACManager.create_with_auth(app_config)
    manager.load_devices()

    # If no device specified, show available devices
    if not device_identifier:
        console.print("[yellow]Available devices:[/yellow]")
        devices = manager.list_devices_simple()

        if not devices:
            console.print("[red]No devices found.[/red]")
            raise typer.Exit(1)

        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Index", style="cyan", justify="center")
        table.add_column("Serial", style="green")
        table.add_column("Name", style="yellow")

        for i, (serial, name) in enumerate(devices, 1):
            table.add_row(str(i), serial, name)

        console.print(table)

        # Get user selection
        choice = typer.prompt("Select device by index or enter serial/name")

        # Try to parse as index first
        try:
            index = int(choice) - 1
            if 0 <= index < len(devices):
                device_identifier = devices[index][0]  # Use serial
            else:
                console.print("[red]Invalid index.[/red]")
                raise typer.Exit(1)
        except ValueError:
            # Not an index, use as identifier
            device_identifier = choice

    # Find the device
    device = manager.get_device_by_serial(device_identifier)
    if not device:
        device = manager.get_device_by_name(device_identifier)

    if not device:
        console.print(f"[red]Device '{device_identifier}' not found.[/red]")
        raise typer.Exit(1)

    device_serial = device.get_serial()
    device_name = device.get_name()

    try:
        console.print(
            f"\n[bold green]Schedule for {device_name} ({device_serial})[/bold green]"
        )

        # Create a schedule-enabled version of the device
        schedule_device = manager.enable_scheduling_for_device(device)

        # Get the schedule
        unit_schedule = schedule_device.get_unit_schedule()
        if unit_schedule is None:
            console.print("[red]Schedule not available for this device.[/red]")
            raise typer.Exit(1)

        unit_schedule.fetch()

        # Check if any schedule entries exist
        if len(unit_schedule) == 0:
            console.print("[yellow]No schedule entries found.[/yellow]")
            return

        # Create table for schedule display
        table = Table(
            title=f"Schedule for {device_name}",
            show_header=True,
            header_style="bold blue",
        )
        table.add_column("Slot", style="cyan", justify="center")
        table.add_column("Active", style="green", justify="center")
        table.add_column("Days", style="yellow", min_width=12)
        table.add_column("Time", style="magenta", justify="center")
        table.add_column("Mode", style="blue", justify="center")
        table.add_column("Heat SP", style="red", justify="center")
        table.add_column("Cool SP", style="cyan", justify="center")
        table.add_column("Fan Speed", style="orange1", justify="center")
        table.add_column("Vane Dir", style="dim", justify="center")

        # Map day numbers to names for display
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for slot in sorted(unit_schedule.keys()):
            event = unit_schedule[slot]

            # Format days
            if event.scheduled_days:
                days_str = ", ".join(
                    day_names[day] for day in sorted(event.scheduled_days)
                )
            else:
                days_str = "None"

            # Format active status
            active_str = "✓" if event.active else "✗"

            # Format setpoints
            heat_sp = (
                f"{event.settings.set_point_heat}°F"
                if event.settings.set_point_heat is not None
                else "N/A"
            )
            cool_sp = (
                f"{event.settings.set_point_cool}°F"
                if event.settings.set_point_cool is not None
                else "N/A"
            )

            # Format time
            time_str = (
                event.scheduled_time.strftime("%H:%M")
                if event.scheduled_time
                else "N/A"
            )

            table.add_row(
                slot,
                active_str,
                days_str,
                time_str,
                str(event.settings.mode),
                heat_sp,
                cool_sp,
                str(event.settings.fan_speed),
                str(event.settings.vane_dir),
            )

        console.print(table)

        # Show summary info
        active_events = sum(1 for slot in unit_schedule if unit_schedule[slot].active)
        console.print(
            f"\n[dim]Total slots: {len(unit_schedule)}, Active events: {active_events}[/dim]"
        )

    except Exception as e:
        console.print(f"[red]Error retrieving schedule: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def check_device_settings(
    device_identifier: Annotated[
        str,
        typer.Argument(help="Device serial number or name (or 'all' for all devices)"),
    ] = None,
    exit_code: Annotated[
        bool,
        typer.Option(
            "--exit-code", help="Exit with non-zero code if device is out of sync"
        ),
    ] = False,
):
    """Check if device settings match their scheduled values. Supports checking all devices with 'all'."""
    manager = HVACManager.create_with_auth(app_config)
    manager.load_devices()

    if not manager.devices:
        console.print("[red]No devices found.[/red]")
        raise typer.Exit(1)

    # Handle 'all' devices case
    if device_identifier and device_identifier.lower() == "all":
        devices_to_check = manager.devices
        console.print(f"[blue]Checking all {len(devices_to_check)} device(s)...[/blue]")
    else:
        # If no device specified, show available devices
        if not device_identifier:
            console.print("[yellow]Available devices:[/yellow]")
            devices = manager.list_devices_simple()

            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Index", style="cyan", justify="center")
            table.add_column("Serial", style="green")
            table.add_column("Name", style="yellow")

            for i, (serial, name) in enumerate(devices, 1):
                table.add_row(str(i), serial, name)

            console.print(table)

            # Get user selection
            choice = typer.prompt(
                "Select device by index or enter serial/name (or 'all')"
            )

            # Try to parse as index first
            try:
                index = int(choice) - 1
                if 0 <= index < len(devices):
                    device_identifier = devices[index][0]  # Use serial
                else:
                    console.print("[red]Invalid index.[/red]")
                    raise typer.Exit(1)
            except ValueError:
                # Not an index, use as identifier
                device_identifier = choice

        # Handle single device or 'all' from prompt
        if device_identifier.lower() == "all":
            devices_to_check = manager.devices
            console.print(
                f"[blue]Checking all {len(devices_to_check)} device(s)...[/blue]"
            )
        else:
            # Find single device
            device = manager.get_device_by_serial(device_identifier)
            if not device:
                device = manager.get_device_by_name(device_identifier)

            if not device:
                console.print(f"[red]Device '{device_identifier}' not found.[/red]")
                raise typer.Exit(1)

            devices_to_check = [device]

    # Process each device
    total_devices = len(devices_to_check)
    devices_in_sync = 0
    devices_with_critical_issues = 0
    devices_with_minor_issues = 0
    devices_with_errors = 0
    all_exit_status = 0

    for i, device in enumerate(devices_to_check, 1):
        device_name = device.get_name()
        device_serial = device.get_serial()

        if total_devices > 1:
            console.print(
                f"\n[bold blue]({i}/{total_devices}) Checking {device_name} ({device_serial})[/bold blue]"
            )
        else:
            console.print(
                f"\n[bold blue]Checking Settings for {device_name} ({device_serial})[/bold blue]"
            )

        try:
            # Get current device settings
            current_settings = DeviceSettings.from_device(device)

            # Create a schedule-enabled version of the device
            schedule_device = manager.enable_scheduling_for_device(device)
            unit_schedule = schedule_device.get_unit_schedule()

            if unit_schedule is None:
                console.print(
                    f"[yellow]⚠️ Schedule not available for {device_name}. Skipping.[/yellow]"
                )
                devices_with_errors += 1
                continue

            unit_schedule.fetch()

            # Get expected settings based on schedule
            analyzer = ScheduleAnalyzer()
            expected_settings = analyzer.get_expected_settings(unit_schedule)

            if expected_settings is None:
                console.print(
                    f"[yellow]⚠️ No active schedule found for {device_name}. Skipping.[/yellow]"
                )
                devices_with_errors += 1
                continue

            # Compare settings
            differences = current_settings.compare_to(expected_settings, manager.config)

            # Separate critical differences (setpoints) from minor ones (mode variations)
            critical_differences = {}
            minor_differences = {}

            for field, diff in differences.items():
                if field in ["heat_setpoint", "cool_setpoint"]:
                    critical_differences[field] = diff
                else:
                    minor_differences[field] = diff

            has_critical_issues = len(critical_differences) > 0
            has_minor_issues = len(minor_differences) > 0

            # For single device, show detailed table
            if total_devices == 1:
                # Create comparison table
                table = Table(
                    title="Settings Comparison",
                    show_header=True,
                    header_style="bold blue",
                )
                table.add_column("Setting", style="cyan", min_width=15)
                table.add_column(
                    "Current", style="yellow", justify="center", min_width=12
                )
                table.add_column(
                    "Expected", style="green", justify="center", min_width=12
                )
                table.add_column("Status", style="magenta", justify="center")

                # Prioritize setpoints first, then other settings
                settings_map = {
                    "heat_setpoint": "Heat Setpoint",
                    "cool_setpoint": "Cool Setpoint",
                    "mode": "Mode",
                    "fan_speed": "Fan Speed",
                    "vane_direction": "Vane Direction",
                }

                for field, display_name in settings_map.items():
                    current_val = getattr(current_settings, field)
                    expected_val = getattr(expected_settings, field)

                    # Format values properly based on field type
                    if field in ["heat_setpoint", "cool_setpoint"]:
                        current_str = format_temperature(
                            current_val, manager.config.temperature_unit
                        )
                        expected_str = format_temperature(
                            expected_val, manager.config.temperature_unit
                        )
                    else:
                        current_str = (
                            str(current_val) if current_val is not None else "N/A"
                        )
                        expected_str = (
                            str(expected_val) if expected_val is not None else "N/A"
                        )

                    if field in critical_differences:
                        status = "🔥 CRITICAL"
                        # Highlight critical differences more prominently
                        table.add_row(
                            f"[bold]{display_name}[/bold]",
                            f"[bold red]{current_str}[/bold red]",
                            f"[bold green]{expected_str}[/bold green]",
                            status,
                        )
                    elif field in minor_differences:
                        status = "⚠️ MINOR"
                        table.add_row(
                            display_name,
                            f"[yellow]{current_str}[/yellow]",
                            f"[green]{expected_str}[/green]",
                            status,
                        )
                    else:
                        status = "✅ OK"
                        table.add_row(display_name, current_str, expected_str, status)

                console.print(table)

                # Summary for single device
                now = datetime.datetime.now()
                console.print(
                    f"\n[dim]Checked at: {now.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
                )

            # Track results for summary
            if not has_critical_issues and not has_minor_issues:
                console.print(f"[green]✅ {device_name} is in sync![/green]")
                devices_in_sync += 1
            elif has_critical_issues:
                devices_with_critical_issues += 1
                all_exit_status = 1  # Set overall exit status
                console.print(
                    f"[bold red]🔥 {device_name} has {len(critical_differences)} critical issue(s)![/bold red]"
                )

                # Show issues for multi-device or single device detailed view
                for field, (current, expected) in critical_differences.items():
                    settings_map = {
                        "heat_setpoint": "Heat Setpoint",
                        "cool_setpoint": "Cool Setpoint",
                        "mode": "Mode",
                        "fan_speed": "Fan Speed",
                        "vane_direction": "Vane Direction",
                    }
                    display_name = settings_map[field]
                    console.print(
                        f"  • {display_name}: [bold red]{current}[/bold red] → [bold green]{expected}[/bold green]"
                    )

                if minor_differences:
                    console.print(
                        f"  [dim]+ {len(minor_differences)} minor issue(s)[/dim]"
                    )
            else:
                # Only minor issues
                devices_with_minor_issues += 1
                console.print(
                    f"[yellow]⚠️ {device_name} has {len(minor_differences)} minor issue(s) (no critical problems)[/yellow]"
                )

        except Exception as e:
            console.print(f"[red]✗ Error checking {device_name}: {e}[/red]")
            devices_with_errors += 1
            continue

    # Summary for multiple devices
    if total_devices > 1:
        console.print("\n[bold blue]Summary:[/bold blue]")
        console.print(f"  • Total devices checked: {total_devices}")
        console.print(f"  • Devices in sync: [green]{devices_in_sync}[/green]")

        if devices_with_critical_issues > 0:
            console.print(
                f"  • Devices with critical issues: [red]{devices_with_critical_issues}[/red]"
            )
        if devices_with_minor_issues > 0:
            console.print(
                f"  • Devices with minor issues: [yellow]{devices_with_minor_issues}[/yellow]"
            )
        if devices_with_errors > 0:
            console.print(f"  • Devices with errors: [red]{devices_with_errors}[/red]")

        # Overall status
        if devices_with_critical_issues > 0:
            console.print(
                f"\n[bold red]🔥 {devices_with_critical_issues} device(s) need immediate attention![/bold red]"
            )
        elif devices_with_minor_issues > 0:
            console.print(
                f"\n[yellow]⚠️ {devices_with_minor_issues} device(s) have minor issues.[/yellow]"
            )
        else:
            console.print(
                "\n[green]✅ All devices are in sync with their schedules![/green]"
            )

    # Handle exit codes
    if exit_code and all_exit_status != 0:
        raise typer.Exit(all_exit_status)
    elif devices_with_errors > 0 and total_devices > 1:
        # Only exit with error for multiple devices if we couldn't check some
        raise typer.Exit(1)
    elif all_exit_status != 0:
        raise typer.Exit(all_exit_status)


@app.command()
def fix_device_settings(
    device_identifier: Annotated[
        str,
        typer.Argument(help="Device serial number or name (or 'all' for all devices)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show what would be changed without applying changes",
        ),
    ] = False,
    setpoints_only: Annotated[
        bool,
        typer.Option(
            "--setpoints-only", help="Only fix temperature setpoints (default behavior)"
        ),
    ] = True,
):
    """Fix device settings to match scheduled values. Only adjusts setpoints by default."""
    manager = HVACManager.create_with_auth(app_config)
    manager.load_devices()

    if not manager.devices:
        console.print("[red]No devices found.[/red]")
        raise typer.Exit(1)

    # Handle 'all' devices case
    if device_identifier and device_identifier.lower() == "all":
        devices_to_fix = manager.devices
        console.print(f"[blue]Processing all {len(devices_to_fix)} device(s)...[/blue]")
    else:
        # If no device specified, show available devices
        if not device_identifier:
            console.print("[yellow]Available devices:[/yellow]")
            devices = manager.list_devices_simple()

            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Index", style="cyan", justify="center")
            table.add_column("Serial", style="green")
            table.add_column("Name", style="yellow")

            for i, (serial, name) in enumerate(devices, 1):
                table.add_row(str(i), serial, name)

            console.print(table)

            # Get user selection
            choice = typer.prompt(
                "Select device by index or enter serial/name (or 'all')"
            )

            # Try to parse as index first
            try:
                index = int(choice) - 1
                if 0 <= index < len(devices):
                    device_identifier = devices[index][0]  # Use serial
                else:
                    console.print("[red]Invalid index.[/red]")
                    raise typer.Exit(1)
            except ValueError:
                # Not an index, use as identifier
                device_identifier = choice

        # Handle single device or 'all' from prompt
        if device_identifier.lower() == "all":
            devices_to_fix = manager.devices
            console.print(
                f"[blue]Processing all {len(devices_to_fix)} device(s)...[/blue]"
            )
        else:
            # Find single device
            device = manager.get_device_by_serial(device_identifier)
            if not device:
                device = manager.get_device_by_name(device_identifier)

            if not device:
                console.print(f"[red]Device '{device_identifier}' not found.[/red]")
                raise typer.Exit(1)

            devices_to_fix = [device]

    # Process each device
    total_devices = len(devices_to_fix)
    devices_fixed = 0
    devices_already_synced = 0
    devices_with_errors = 0

    for i, device in enumerate(devices_to_fix, 1):
        device_name = device.get_name()
        device_serial = device.get_serial()

        if total_devices > 1:
            console.print(
                f"\n[bold blue]({i}/{total_devices}) Processing {device_name} ({device_serial})[/bold blue]"
            )
        else:
            console.print(
                f"\n[bold blue]Processing {device_name} ({device_serial})[/bold blue]"
            )

        try:
            # Get current device settings
            current_settings = DeviceSettings.from_device(device)

            # Create a schedule-enabled version of the device
            schedule_device = manager.enable_scheduling_for_device(device)
            unit_schedule = schedule_device.get_unit_schedule()

            if unit_schedule is None:
                console.print(
                    f"[yellow]⚠️ Schedule not available for {device_name}. Skipping.[/yellow]"
                )
                devices_with_errors += 1
                continue

            unit_schedule.fetch()

            # Get expected settings based on schedule
            analyzer = ScheduleAnalyzer()
            expected_settings = analyzer.get_expected_settings(unit_schedule)

            if expected_settings is None:
                console.print(
                    f"[yellow]⚠️ No active schedule found for {device_name}. Skipping.[/yellow]"
                )
                devices_with_errors += 1
                continue

            # Compare settings
            differences = current_settings.compare_to(expected_settings, manager.config)

            # Filter to only setpoints if setpoints_only is True
            if setpoints_only:
                setpoint_differences = {
                    field: diff
                    for field, diff in differences.items()
                    if field in ["heat_setpoint", "cool_setpoint"]
                }
                differences = setpoint_differences

            if not differences:
                console.print(f"[green]✅ {device_name} is already in sync![/green]")
                devices_already_synced += 1
                continue

            # Show what will be changed
            console.print(f"[yellow]Changes needed for {device_name}:[/yellow]")
            for field, (current, expected) in differences.items():
                field_display = {
                    "heat_setpoint": "Heat Setpoint",
                    "cool_setpoint": "Cool Setpoint",
                    "mode": "Mode",
                    "fan_speed": "Fan Speed",
                    "vane_direction": "Vane Direction",
                }[field]
                console.print(
                    f"  • {field_display}: [red]{current}[/red] → [green]{expected}[/green]"
                )

            if dry_run:
                console.print(
                    f"[dim]🔍 DRY RUN: Would apply {len(differences)} change(s) to {device_name}[/dim]"
                )
                devices_fixed += 1
                continue

            # Apply the changes
            console.print(
                f"[blue]Applying {len(differences)} change(s) to {device_name}...[/blue]"
            )

            changes_applied = 0
            for field in differences.keys():
                expected_val = getattr(expected_settings, field)

                try:
                    if field == "heat_setpoint" and expected_val is not None:
                        device.set_heat_setpoint(expected_val)
                        changes_applied += 1
                    elif field == "cool_setpoint" and expected_val is not None:
                        device.set_cool_setpoint(expected_val)
                        changes_applied += 1
                    elif (
                        field == "mode"
                        and expected_val is not None
                        and not setpoints_only
                    ):
                        device.set_mode(expected_val)
                        changes_applied += 1
                    elif (
                        field == "fan_speed"
                        and expected_val is not None
                        and not setpoints_only
                    ):
                        device.set_fan_speed(expected_val)
                        changes_applied += 1
                    elif (
                        field == "vane_direction"
                        and expected_val is not None
                        and not setpoints_only
                    ):
                        device.set_vane_direction(expected_val)
                        changes_applied += 1

                except Exception as e:
                    console.print(f"[red]✗ Failed to set {field}: {e}[/red]")
                    continue

            if changes_applied > 0:
                console.print(
                    f"[green]✅ Applied {changes_applied} change(s) to {device_name}[/green]"
                )
                devices_fixed += 1
            else:
                console.print(
                    f"[red]✗ No changes could be applied to {device_name}[/red]"
                )
                devices_with_errors += 1

        except Exception as e:
            console.print(f"[red]✗ Error processing {device_name}: {e}[/red]")
            devices_with_errors += 1
            continue

    # Summary
    console.print("\n[bold blue]Summary:[/bold blue]")

    if dry_run:
        console.print("[dim]🔍 DRY RUN MODE - No actual changes made[/dim]")

    console.print(f"  • Total devices processed: {total_devices}")
    console.print(
        f"  • Devices {'that would be ' if dry_run else ''}fixed: [green]{devices_fixed}[/green]"
    )
    console.print(
        f"  • Devices already in sync: [green]{devices_already_synced}[/green]"
    )

    if devices_with_errors > 0:
        console.print(f"  • Devices with errors: [red]{devices_with_errors}[/red]")

    # Exit with appropriate code
    if devices_with_errors > 0:
        raise typer.Exit(1)
    elif devices_fixed == 0 and devices_already_synced == 0:
        console.print("[yellow]No devices needed fixing.[/yellow]")
        raise typer.Exit(0)
    else:
        console.print(
            f"[green]✅ Successfully processed {devices_fixed + devices_already_synced} device(s)![/green]"
        )
        raise typer.Exit(0)


if __name__ == "__main__":
    app()
