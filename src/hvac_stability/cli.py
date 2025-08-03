import base64
import json
import logging
import os
from pathlib import Path
from typing import Annotated

import environ
import pykumo
import typer
from attrs import define
from click import secho
from environ import config, var
from pykumo import KumoCloudAccount, PyKumo
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer()
console = Console()


def configure_pykumo_logging(enable: bool = False):
    """Configure pykumo logging based on the global flag."""
    pykumo_logger = logging.getLogger("pykumo")

    if enable:
        # Enable pykumo logging with INFO level
        pykumo_logger.setLevel(logging.INFO)
        if not pykumo_logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(name)s: %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            pykumo_logger.addHandler(handler)
    else:
        # Disable pykumo logging by setting to WARNING or higher
        pykumo_logger.setLevel(logging.ERROR)


@config(prefix="KUMO")
class Config:
    auth_username: str = var(default=None)
    auth_password: str = var(default=None)

    data_path: Path = var(default="~/.local/var/hvac_stability/")

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


@app.callback()
def main(
    pykumo_logging: Annotated[
        bool, typer.Option("--pykumo-logging", help="Enable pykumo library logging")
    ] = False,
):
    """HVAC Stability Management Tool for Kumo Cloud API."""
    configure_pykumo_logging(pykumo_logging)


app_config = environ.to_config(Config)


@define
class HVACManager:
    config: Config
    connection: KumoCloudAccount

    devices: list[PyKumo] = []
    local_device_config: dict[str, dict] = {}

    @classmethod
    def create_with_auth(cls, config: Config) -> "HVACManager":
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
        account = KumoCloudAccount.Factory(username, password)
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
                except:
                    temp_str = "N/A"

                # Get mode and fan speed
                try:
                    mode = device.get_mode() or "N/A"
                    fan_speed = device.get_fan_speed() or "N/A"
                    status = device.get_runstate() or "N/A"
                    wifi_rssi = device.get_wifi_rssi()
                    wifi_str = f"{wifi_rssi}dBm" if wifi_rssi is not None else "N/A"
                except:
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
            except Exception as e:
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
def check_device_settings(name_or_serial: str): ...


@app.command()
def reset_device_to_scheduled(name_or_serial: str): ...


if __name__ == "__main__":
    app()
