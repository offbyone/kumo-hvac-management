import base64
import json
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
from rich.table import Table
from rich.panel import Panel

app = typer.Typer()
console = Console()


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
            console.print("✗ No credentials found. Please run 'hvac-stability login' first.", style="bold red")
            raise typer.Exit(1)
        
        try:
            connection = KumoCloudAccount.Factory(username, password)
            return cls(config=config, connection=connection)
        except Exception as e:
            console.print(f"✗ Authentication failed: {e}", style="bold red")
            raise typer.Exit(1)

        try:
            connection = KumoCloudAccount.Factory(username, password)
            return cls(config=config, connection=connection)
        except Exception as e:
            secho(f"Authentication failed: {e}", fg="red")
            raise typer.Exit(1)

    def load_devices(self):
        for device in self.connection.get_indoor_units():
            self.devices.append(device)

        data_file = self.config.devices_file

        if data_file.exists():
            with open(data_file, "r") as f:
                local_device_config = f.read()

            self.local_device_config = json.loads(local_device_config)["devices"]

        for device in self.devices:
            if device.get_serial() in self.local_device_config:
                # if we don't have an address, update it from config
                ...


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
def list(verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed device information")] = False):
    """List all devices."""
    username, password = app_config.get_auth_credentials()

    if not username or not password:
        console.print("✗ No credentials found. Please run 'hvac-stability login' first.", style="bold red")
        raise typer.Exit(1)

    try:
        account = KumoCloudAccount.Factory(username, password)

        if verbose:
            device_details = account.make_pykumos()
            
            if not device_details:
                console.print("[yellow]No devices found.[/yellow]")
                return
            
            table = Table(title="HVAC Devices", show_header=True, header_style="bold blue")
            table.add_column("Serial", style="cyan", no_wrap=True)
            table.add_column("Label", style="green")
            table.add_column("Address", style="magenta")
            table.add_column("Unit Type", style="yellow")
            table.add_column("MAC", style="dim")
            
            for device_serial, device in device_details.items():
                label = getattr(device, 'label', 'N/A')
                address = getattr(device, 'address', 'N/A')
                unit_type = getattr(device, 'unitType', 'N/A')
                mac = getattr(device, 'mac', 'N/A')
                
                table.add_row(
                    device_serial,
                    label,
                    address,
                    unit_type,
                    mac
                )
            
            console.print(table)
        else:
            devices = account.get_indoor_units()
            
            if not devices:
                console.print("[yellow]No devices found.[/yellow]")
                return
                
            console.print(f"[green]Found {len(devices)} device(s):[/green]")
            for i, device in enumerate(devices, 1):
                device_info = f"{i}. {getattr(device, 'get_name', lambda: 'Unknown Device')()}"
                console.print(f"  {device_info}")
    except Exception as e:
        console.print(f"✗ Error: {e}", style="bold red")
        raise typer.Exit(1)


@app.command()
def store_device_ip(): ...


@app.command()
def check_device_settings(name_or_serial: str): ...


@app.command()
def reset_device_to_scheduled(name_or_serial: str): ...


if __name__ == "__main__":
    app()
