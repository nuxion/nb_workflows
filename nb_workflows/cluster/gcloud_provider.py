import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from libcloud.compute.base import Node, NodeLocation
from libcloud.compute.providers import get_driver
from libcloud.compute.types import Provider
from pydantic import BaseSettings

from nb_workflows.types.machine import (
    BlockInstance,
    BlockStorage,
    ExecMachineResult,
    ExecutionMachine,
    MachineInstance,
    MachineRequest,
)

from .base import ProviderSpec


class GCConf(BaseSettings):
    service_account: str
    project: str
    pem_file: Optional[str] = None
    datacenter: Optional[str] = None
    credential_file: Optional[str] = None

    class Config:
        env_prefix = "NB_GCE_"


def get_gce_driver():
    GCE = get_driver(Provider.GCE)
    return GCE


def generic_zone(name, driver) -> NodeLocation:
    return NodeLocation(id=None, name=name, country=None, driver=driver)


class GCEProvider(ProviderSpec):
    def __init__(self, conf: Optional[GCConf] = None):
        self.conf = conf or GCConf()
        G = get_gce_driver()
        # _env_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        # if _env_creds:
        #     conf.credential_file = _env_creds

        self.driver = G(
            self.conf.service_account,
            key=self.conf.pem_file,
            project=self.conf.project,
            datacenter=self.conf.datacenter,
        )

    def _get_volume(self, vol_name):
        volumes = [v for v in self.driver.list_volumes() if v.name == vol_name]
        if len(volumes) > 0:
            return volumes[0]
        return None

    def _get_volumes_to_attach(self, volumes: List[BlockStorage]):
        to_attach = []
        for vol in volumes:
            v = self._get_volume(vol.name)
            if v:
                to_attach.append(v)
            if not v and vol.create_if_not_exist:
                created = self.driver.create_volume(
                    vol.size,
                    vol.name,
                    location=vol.location,
                    snapshot=vol.snapshot,
                    ex_disk_type=vol.kind,
                )
                to_attach.append(created)
        if to_attach:
            return to_attach
        return None

    def create_machine(self, node: MachineRequest) -> MachineInstance:
        metadata = {
            "items": [
                {"key": "ssh-keys", "value": f"{node.ssh_user}: {node.ssh_public_cert}"}
            ]
        }
        volumes = None
        if node.volumes:
            volumes = self._get_volumes_to_attach(node.volumes)

        tags = node.labels.get("tags")
        _labels = deepcopy(node.labels)
        del _labels["tags"]
        labels = _labels

        instance = self.driver.create_node(
            node.name,
            size=node.size,
            image=node.image,
            location=node.location,
            ex_network=node.network,
            ex_metadata=metadata,
            ex_tags=tags,
            ex_labels=labels,
        )
        if volumes:
            for v in volumes:
                self.driver.attach_volume(instance, v)

        res = MachineInstance(
            machine_id=f"/gce/{node.location}/{node.name}",
            machine_name=node.name,
            location=node.location,
            main_addr=instance.private_ips[0],
            private_ips=instance.private_ips,
            public_ips=instance.public_ips,
        )
        return res

    def list_machines(
        self, location: Optional[str] = None, tags: Optional[List[str]] = None
    ) -> List[MachineInstance]:
        nodes = self.driver.list_nodes(ex_zone=location)
        filtered_nodes = []
        if tags:
            _tags = set(tags)
            for n in nodes:
                if n.state == "running" and n["extra"]["tags"] in set(_tags):
                    filtered_nodes.append(n)
        else:
            filtered_nodes = nodes
        final = []
        for n in filtered_nodes:
            if n.state == "running":
                _n = MachineInstance(
                    node_id=n.id,
                    machine_name=n.name,
                    location=n.extra["zone"].name,
                    tags=n.extra["tags"],
                    main_addr=n.private_ips[0],
                    private_ips=n.private_ips,
                    public_ips=n.public_ips,
                )
                final.append(_n)
        return final

    def destroy_machine(self, node: Union[str, MachineInstance]):
        name = node
        if isinstance(node, MachineInstance):
            name = node.machine_name
        nodes = self.driver.list_nodes()
        _node = [n for n in nodes if n.name == name][0]
        _node.destroy()

    def create_volume(self, disk: BlockStorage) -> BlockInstance:
        vol = self.driver.create_volume(
            disk.size,
            disk.name,
            location=disk.location,
            snapshot=disk.snapshot,
            ex_disk_type=disk.kind,
        )
        block = BlockInstance(id=vol.id, **disk.dict())
        block.extra = vol.extra
        return block

    def destroy_volume(self, disk: Union[str, BlockStorage]) -> bool:
        name = disk
        if isinstance(disk, BlockStorage):
            name = disk.name
        vol = [v for v in self.driver.list_volumes() if v.name == name][0]
        rsp = self.driver.destroy_volume(vol)
        return rsp

    def attach_volume(self, node: MachineInstance, disk: BlockStorage) -> bool:
        _node = [n for n in self.driver.list_nodes() if n.name == node.machine_name][0]
        vol = [v for v in self.driver.list_volumes() if v.name == disk.name]

        res = self.driver.attach_volume(_node, vol)
        return res

    def detach_volume(self, node: MachineInstance, disk: BlockStorage) -> bool:
        _node = [n for n in self.driver.list_nodes() if n.name == node.machine_name][0]
        vol = [v for v in self.driver.list_volumes() if v.name == disk.name]
        res = self.driver.detach_volume(vol, _node)
        return res
