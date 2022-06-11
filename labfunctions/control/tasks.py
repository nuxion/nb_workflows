from datetime import datetime
from typing import Any, Dict

from labfunctions import client, types
from labfunctions.cluster2 import ClusterControl, ClusterTaskCtx
from labfunctions.executors import ExecID
from labfunctions.executors.docker_exec import docker_exec
from labfunctions.runtimes.builder import builder_exec
from labfunctions.utils import get_version, today_string


def notebook_dispatcher(data: Dict[str, Any]):
    ctx = types.ExecutionNBTask(**data)
    result = docker_exec(ctx)
    return result.dict()


def workflow_dispatcher(data: Dict[str, Any]):
    ctx = types.ExecutionNBTask(**data)
    ctx.execid = str(ExecID())

    today = today_string(format_="day")
    _now = datetime.utcnow().isoformat()
    ctx.params["NOW"] = _now
    ctx.created_at = _now
    ctx.today = today
    result = notebook_dispatcher(ctx.dict())
    return result


def build_dispatcher(data: Dict[str, Any]):
    ctx = types.runtimes.BuildCtx(**data)
    result = builder_exec(ctx)
    return result.dict()


def create_instance(data: Dict[str, Any]):
    ctx = ClusterTaskCtx(**data)
    cluster = ClusterControl(
        ctx.cluster_file,
        ssh_user=ctx.ssh_key_user,
        ssh_key_public_path=ctx.ssh_public_key_path,
    )
    res = cluster.create_instance(ctx.machine_name)
    return res.dict()
