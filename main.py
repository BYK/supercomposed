#!/usr/bin/env python3

from configparser import ConfigParser
import json
import re
import subprocess
import yaml


ENV_VAR_RE = r"""(?x)
    \$([^\{\}:\s]+)\b # simple $ENV_VAR style
    |
    \$\{([^\{:\s\}]+)(?:\:[^\{\:\s\}]+)?\} # ${ENV_VAR:val} style
"""


def convert_env_var_declarations(s):
    return re.sub(ENV_VAR_RE, r"%(ENV_\1\2)s", str(s)) if s else s


def stringify_docker_cmd_list(l):
    return (
        " ".join(json.dumps(c) if " " in c else c for c in l)
        if isinstance(l, list)
        else l
    )


with open("docker-compose.yml") as f:
    compose_data = yaml.safe_load(f)


supervisord_config = ConfigParser()
supervisord_config["supervisord"] = {
    "logfile": "/dev/stdout",
    "logfile_maxbytes": "0",
    "logfile_backups": "0",
    "loglevel": "debug",
    "nodaemon": "true",
    "nocleanup": "true",
}

for service_name, service_config in compose_data["services"].items():
    image = service_config["image"]
    # ensure docker image
    try:
        subprocess.check_call(
            ["docker", "image", "pull", image],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # TODO: Check if there's a `build` key and try to build the image
        print(f"Had an issue when pulling the image for {service_name}, skipping")
        image_definition = {"Config": {"Cmd": "", "Entrypoint": ""}}
    else:
        image_definition = json.loads(
            subprocess.check_output(["docker", "inspect", image])
        )[0]

    entrypoint = service_config.get("entrypoint")
    if not entrypoint:
        entrypoint = image_definition["Config"]["Entrypoint"]

    command = service_config.get("command")
    if not command:
        command = image_definition["Config"]["Cmd"]

    command = (stringify_docker_cmd_list(command) or "").strip()
    entrypoint = (stringify_docker_cmd_list(entrypoint) or "").strip()
    if entrypoint:
        command = entrypoint + " " + command

    # TODO: replace env variable expansions
    program_config = {
        "command": convert_env_var_declarations(command),
        "stdout_logfile": "/dev/stdout",
        "stdout_logfile_maxbytes": "0",
        "stdout_logfile_backups": "0",
    }
    if "environment" in service_config:
        program_config["environment"] = ",".join(
            f"{key}={json.dumps(convert_env_var_declarations(value))}"
            for key, value in service_config["environment"].items()
        )

    supervisord_config[f"program:{service_name}"] = program_config


with open("supervisord.ini", "w") as f:
    supervisord_config.write(f)
