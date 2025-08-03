import json
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

app = typer.Typer()


@config(prefix="KUMO")
class Config:
    auth_username: str = var(default=None)
    auth_password: str = var(default=None)

    data_path: Path = var(default="~/.local/var/hvac_stability/")

    @property
    def devices_file(self) -> Path:
        return Path(self.data_path).expanduser() / "devices.json"


app_config = environ.to_config(Config)


@define
class HVACManager:
    config: Config
    connection: KumoCloudAccount

    devices: list[PyKumo] = []
    local_device_config: dict[str, dict] = {}

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
):
    """Login to the Kumo API."""
    username = username or app_config.auth_username
    password = app_config.auth_password
    pykumo.KumoCloudAccount.Factory(username, password)


@app.command()
def list():
    """List all devices."""
    if not username or not password:
        secho("Username and password are required.", fg="red")
        raise typer.Exit(1)

    try:
        account = KumoCloudAccount.Factory(username, password)
        devices = account.get_indoor_units()
        print(devices)
    except Exception as e:
        secho(f"Error: {e}", fg="red")
        raise typer.Exit(1)


@app.command()
def store_device_ip(): ...


@app.command()
def check_device_settings(name_or_serial: str): ...


@app.command()
def reset_device_to_scheduled(name_or_serial: str): ...


if __name__ == "__main__":
    app()
