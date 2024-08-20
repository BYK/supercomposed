#!/usr/bin/env python3

from configparser import ConfigParser
from dotenv import load_dotenv, dotenv_values
from functools import cache
from itertools import chain
import json
import os
import re
import subprocess
import yaml


load_dotenv(".env")


ENV_VAR_RE = r"""(?x)
    \$([^\{\}:\s]+)\b # simple $ENV_VAR style
    |
    \$\{([^\{:\s\}]+)(?:\:[^\{\:\s\}]+)?\} # ${ENV_VAR:val} style
"""

RESTART_POLICY_MAP = {
    "no": "false",
    "on-failure": "unexpected",
    "always": "true",
    "unless-stopped": "true",
}


def convert_env_var_declarations(s, r=r"%(ENV_\1\2)s"):
    return re.sub(ENV_VAR_RE, r, str(s)) if s else s


def stringify_docker_cmd_list(l):
    return (
        " ".join(json.dumps(c) if " " in c else c for c in l)
        if isinstance(l, list)
        else l
    )


with open("docker-compose.yml") as f:
    compose_data = yaml.safe_load(f)
services = compose_data["services"]

supervisord_config = ConfigParser()
supervisord_config["supervisord"] = {
    "logfile": "/dev/stdout",
    "logfile_maxbytes": "0",
    "logfile_backups": "0",
    "loglevel": "debug",
    "nodaemon": "true",
    "nocleanup": "true",
    "environment": ",".join(
        f"{key}={json.dumps(value)}" for key, value in dotenv_values(".env").items()
    ),
}


@cache
def get_service_dependency_depth(service_name, start_from=0):
    dependencies = services[service_name].get("depends_on", {})
    return start_from + (
        max(
            get_service_dependency_depth(dependency, start_from + 1)
            for dependency in dependencies
        )
        if dependencies
        else 0
    )


for service_name, service_config in services.items():
    image = convert_env_var_declarations(
        service_config["image"],
        lambda m: os.environ.get(m[1] or m[2], ""),
    )

    if "build" in service_config:
        build_info = service_config["build"]
        build_args = build_info["args"]
        if isinstance(build_args, list):
            build_arg_list = [
                (
                    "--build-arg",
                    arg,
                )
                for arg in build_args
            ]
        else:
            build_arg_list = [
                (
                    (
                        "--build-arg",
                        f"{arg}={convert_env_var_declarations(val, lambda m: os.environ.get(m[1] or m[2], ''))}",
                    )
                    if val
                    else ("--build-arg", arg)
                )
                for arg, val in build_args.items()
            ]

        subprocess.check_call(
            ["docker", "build", "-t", image, build_info["context"]]
            + list(chain(*build_arg_list)),
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
    else:
        # ensure docker image
        subprocess.check_call(
            ["docker", "image", "pull", image],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )

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

    program_config = {
        "command": convert_env_var_declarations(command),
        "stdout_logfile": "/dev/stdout",
        "stdout_logfile_maxbytes": "0",
        "stdout_logfile_backups": "0",
    }
    if "environment" in service_config:
        program_config["environment"] = ",".join(
            (
                f"{key}={json.dumps(convert_env_var_declarations(value))}"
                if value
                else f"{key}=%(ENV_{key})s"
            )
            for key, value in service_config["environment"].items()
        )
    # Dependency depth starts from 0, priority should start from 1 (I assume?)
    program_config["priority"] = get_service_dependency_depth(service_name) + 1

    restart_policy = service_config.get(
        "restart_policy", service_config.get("restart", "no")
    )
    program_config["autorestart"] = RESTART_POLICY_MAP[restart_policy]

    supervisord_config[f"program:{service_name}"] = program_config


with open("supervisord.conf", "w") as f:
    supervisord_config.write(f)
