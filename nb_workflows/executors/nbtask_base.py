import json
import logging
import shutil
import time
import warnings
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nb_workflows import defaults
from nb_workflows.client.diskclient import DiskClient
from nb_workflows.client.nbclient import NBClient
from nb_workflows.commands import DockerCommand, DockerRunResult
from nb_workflows.types import ExecutionNBTask, ExecutionResult, NBTask
from nb_workflows.types.runtimes import RuntimeData
from nb_workflows.utils import get_version, today_string

from .execid import ExecID

warnings.filterwarnings("ignore", category=DeprecationWarning)


def _prepare_runtime(runtime: Optional[RuntimeData] = None) -> str:
    if not runtime:
        version = get_version()
        _runtime = f"{defaults.DOCKERFILE_IMAGE}:{version}"
    else:
        _runtime = f"{runtime.docker_name}:{runtime.version}"
        if runtime.registry:
            _runtime = f"{runtime.registry}/{runtime}"
    return _runtime


def _simple_retry(func, params, max_retries=3, wait_time=5):
    status = False
    tries = 0
    while status is not True and tries < 3:
        status = func(*params)
        tries += 1
        time.sleep(3)
    return status


class NBTaskExecBase:

    WFID_TMP = "tmp"

    def __init__(self, client: Union[NBClient, DiskClient]):
        self.client = client
        self.logger = logging.getLogger("nbworkf.server")

    @property
    def projectid(self) -> str:
        return self.client.projectid

    def _dummy_wfid(self):
        wfid = ExecID(size=defaults.WFID_LEN - len(self.WFID_TMP))
        return f"{self.WFID_TMP}{wfid}"

    def run(self, ctx: ExecutionNBTask) -> ExecutionResult:
        raise NotImplementedError()

    def register(self, result: ExecutionResult):
        self.client.history_register(result)
        if result.output_name:
            self.client.history_nb_output(result)

    def notificate(self, ctx: ExecutionNBTask, result: ExecutionResult):
        raise NotImplementedError()


class NBTaskDocker(NBTaskExecBase):
    cmd = "nb exec local"

    def build_env(self, data: Dict[str, Any]) -> Dict[str, Any]:
        priv_key = self.client.projects_private_key()
        if not priv_key:
            raise IndexError("No priv key found")

        return {
            defaults.PRIVKEY_VAR_NAME: priv_key,
            defaults.EXECUTIONTASK_VAR: json.dumps(data),
            "NB_WORKFLOW_SERVICE": self.client._addr,
            defaults.BASE_PATH_ENV: "/app",
        }

    def run(self, ctx: ExecutionNBTask) -> ExecutionResult:
        _started = time.time()
        env = self.build_env(ctx.dict())
        cmd = DockerCommand()
        result = cmd.run(self.cmd, ctx.runtime, ctx.timeout, env_data=env)
        error = False
        if result.status != 0:
            error = True

        elapsed = round(time.time() - _started)
        return ExecutionResult(
            projectid=ctx.projectid,
            execid=ctx.execid,
            wfid=ctx.wfid,
            cluster=ctx.cluster,
            machine=ctx.machine,
            runtime=ctx.runtime,
            name=ctx.nb_name,
            params=ctx.params,
            input_=ctx.pm_input,
            elapsed_secs=elapsed,
            error=error,
            error_msg=result.msg,
            created_at=ctx.created_at,
        )

    def notificate(self, ctx: ExecutionNBTask, result: ExecutionResult):
        pass


class NBTaskLocal(NBTaskExecBase):
    def run(self, ctx: ExecutionNBTask) -> ExecutionResult:
        import papermill as pm

        _started = time.time()
        _error = False
        Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
        try:
            pm.execute_notebook(ctx.pm_input, ctx.pm_output, parameters=ctx.params)
        except pm.exceptions.PapermillExecutionError as e:
            self.logger.error(f"jobdid:{ctx.wfid} execid:{ctx.execid} failed {e}")
            _error = True
            self._error_handler(ctx)

        elapsed = time.time() - _started
        return ExecutionResult(
            wfid=ctx.wfid,
            execid=ctx.execid,
            projectid=ctx.projectid,
            name=ctx.nb_name,
            params=ctx.params,
            input_=ctx.pm_input,
            output_dir=ctx.output_dir,
            output_name=ctx.output_name,
            error_dir=ctx.error_dir,
            error=_error,
            elapsed_secs=round(elapsed, 2),
            created_at=ctx.created_at,
        )

    def notificate(self, ctx: ExecutionNBTask, result: ExecutionResult):
        pass

    def _error_handler(self, etask: ExecutionNBTask):
        error_output = f"{etask.error_dir}/{etask.output_name}"
        Path(etask.error_dir).mkdir(parents=True, exist_ok=True)
        shutil.move(etask.pm_output, error_output)
