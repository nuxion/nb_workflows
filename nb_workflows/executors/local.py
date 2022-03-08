import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Union

import papermill as pm

from nb_workflows import client
from nb_workflows.conf import defaults, load_client
from nb_workflows.notebooks import nb_job_executor
from nb_workflows.types import ExecutionResult, ExecutionTask2, NBTask


def _simple_retry(func, params, max_retries=3, wait_time=5):
    status = False
    tries = 0
    while status is not True and tries < 3:
        status = func(*params)
        tries += 1
        time.sleep(3)
    return status


def local_exec_env() -> Union[ExecutionResult, None]:
    """
    Control the notebook execution.
    TODO: implement notifications
    TODO: base executor class?
    """
    # Init
    nb_client = client.nb_from_settings_agent()
    logger = logging.getLogger(__name__)

    # CTX creation
    ctx_str = os.getenv(defaults.EXECUTIONTASK_VAR)

    etask = ExecutionTask2(**json.loads(ctx_str))
    logger.info(f"jobdid:{etask.jobid} execid:{etask.executionid} Starting")

    # Execution
    result = notebook_executor(etask)

    # Registration
    status = _simple_retry(nb_client.history_nb_output, (result,))
    status_register = _simple_retry(nb_client.history_register, (result,))
    if not status or not status_register:
        logger.error(
            f"jobdid:{etask.jobid} execid:{etask.executionid} Fail registration"
        )

    logger.info(
        f"jobdid:{etask.jobid} execid:{etask.executionid} Finish in {result.elapsed_secs} secs"
    )
    return result


def notebook_executor(etask: ExecutionTask2) -> ExecutionResult:

    _error = False
    _started = time.time()
    logger = logging.getLogger(__name__)

    Path(etask.output_dir).mkdir(parents=True, exist_ok=True)

    try:
        pm.execute_notebook(etask.pm_input, etask.pm_output, parameters=etask.params)
    except pm.exceptions.PapermillExecutionError as e:
        logger.error(f"jobdid:{etask.jobid} execid:{etask.executionid} failed {e}")
        _error = True
        error_handler(etask)

    elapsed = time.time() - _started
    return ExecutionResult(
        jobid=etask.jobid,
        executionid=etask.executionid,
        projectid=etask.projectid,
        name=etask.nb_name,
        params=etask.params,
        input_=etask.pm_input,
        output_dir=etask.output_dir,
        output_name=etask.output_name,
        error_dir=etask.error_dir,
        error=_error,
        elapsed_secs=round(elapsed, 2),
        created_at=etask.created_at,
    )


def error_handler(etask: ExecutionTask2):

    error_output = f"{etask.error_dir}/{etask.output_name}"
    Path(error_output).mkdir(parents=True, exist_ok=True)
    shutil.move(etask.pm_output, error_output)
