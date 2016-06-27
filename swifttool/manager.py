#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2014, Blue Box Group, Inc.
# Copyright (c) 2014, Craig Tracey <craigtracey@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#

import copy
import logging
import math
import os
import shutil
import time
import yaml
from swift.common.ring import RingBuilder

from swifttool.ring_defintion import ringsdef_helper
from swifttool.utils import RING_TYPES, get_devices

LOG = logging.getLogger(__name__)

_STATUS_FILE = 'capacity.status'


class CapacityManager(object):

    def __init__(self, iterations=10,
                 def_file='/etc/swift/ring_definition.yml',
                 scaleup=True, metadata=None,
                 outdir='/etc/swift/'):
        self.iterations = iterations
        self.def_file = def_file
        self.scaleup = scaleup
        self.cluster_changes = {}
        self.outdir = outdir
        self.cluster_config = None
        self.metadata = metadata
        self.current_status = os.path.join(outdir, _STATUS_FILE)
        self._calculate_cluster_changes()

    def __del__(self):
        # Cleanup the files we create
        os.remove(self.current_status)
        os.remove(self.def_file + ".orig")

    def update_cluster(self):
        LOG.info("Starting to update the cluster")
        # Save the copy
        shutil.copyfile(self.def_file, self.def_file + ".orig")
        self._write_status(0)
        min_part_hours = int(self._get_builder('account').min_part_hours)
        for iteration in range(1, self.iterations + 1):
            changed, cfg = self._update_cluster_nodes()
            if not changed:
                break
            # Write up the new config in /etc/swift
            with open(self.def_file, 'w') as stream:
                yaml.dump(cfg, stream, default_flow_style=False,
                          explicit_start=True)
            LOG.info("Rings distributed. Sleeping for %d "
                     "hours 1 minutes" % min_part_hours)
            # Sleep for min_part_hours + minute
            time.sleep((min_part_hours * 60 * 60) + 60)
            # Check if rings need to be rebalanced
            self._rebalance_rings(cfg, min_part_hours)
            # Update status
            self._write_status(int((100 * iteration) / self.iterations))
            LOG.info("Iteration %d complete" % iteration)

        LOG.info("Finished updating the cluster")
        # Things finished with no errors - copy the original file back
        shutil.copyfile(self.def_file + ".orig", self.def_file)

    def _rebalance_rings(self, config, min_part_hours):
        """
        If the balance factor or rings is more than 5
        we force rebalance to assign the partitions.
        This function will force reassignment and also updates the rings.
        Note: This is usually rare and happens when too much capacity
        was being added in single iteration.
        """
        LOG.debug("Checking if rings need to be rebalanced")
        while True:
            need_modify = False
            for ring_type in RING_TYPES:
                if need_modify:
                    continue
                builder = self._get_builder(ring_type)
                balance = builder.get_balance()
                if balance > 5 and balance / 100.0 > builder.overload:
                    need_modify = True
            # Did not modify rings, return
            if not need_modify:
                break
            # Rebalance rings and sleep for min_part_hours
            LOG.info("Rebalancing rings to assign missed partitions")
            ringsdef_helper(config, self.metadata, self.outdir,
                            rebalance_only=True)
            LOG.info("Sleeping additional %d hours for rebalance" %
                     min_part_hours)
            time.sleep(min_part_hours * 60 * 60)

    def _write_status(self, percent_done):
        with open(self.current_status, 'w') as status_file:
            status_file.write(str(percent_done))

    def _update_cluster_nodes(self):
        updated_cfg = copy.deepcopy(self.cluster_config)
        # Update the cluster config to what
        # we desirei
        changed = False
        for builder in RING_TYPES:
            if builder not in self.cluster_changes:
                continue
            LOG.debug("Updating %s builder." % builder)
            for builder_changes in self.cluster_changes[builder]:
                for zone, devices in builder_changes.items():
                    # Get the current configuration from rings
                    ring_devs = self._get_ring_devices(builder, zone)
                    ip = self._get_zone_ip(devices)
                    updated_cfg['zones'].setdefault(zone, {})
                    updated_cfg['zones'][zone].setdefault(ip, {})
                    updated_cfg['zones'][zone][ip].setdefault('disks', {})
                    newdevs = []
                    for dev in devices:
                        devname = dev['device']
                        if devname not in ring_devs:
                            initial_weight = 0
                        else:
                            initial_weight = ring_devs[devname]
                        if initial_weight == dev['target']:
                            continue
                        devinfo = {}
                        if 'metadata' in dev:
                            devinfo['metadata'] = dev['metadata']
                        devinfo['blockdev'] = devname
                        devinfo['weight'] = int(initial_weight +
                                                dev['step'])
                        if abs(devinfo['weight'] - dev['target']) < 50:
                            devinfo['weight'] = dev['target']
                        LOG.debug("Setting the %s device capacity to %s",
                                  devname, str(devinfo['weight']))
                        newdevs.append(devinfo)
                        changed = True
                    updated_cfg['zones'][zone][ip]['disks'][builder] = \
                        newdevs
        if changed:
            ringsdef_helper(updated_cfg, self.metadata, self.outdir)
        return changed, updated_cfg

    @staticmethod
    def _get_zone_ip(devs):
        for dev in devs:
            if 'ip' in dev:
                return dev['ip']

    def _calculate_cluster_changes(self):
        with open(self.def_file, 'r') as config_file:
            self.cluster_config = yaml.load(config_file)
        expected_config = get_devices(self.cluster_config['zones'])
        current_config = self._get_all_cluster_devices()
        # To get devices to be removed we look for devices present in
        # current_config and not in expected_config
        # To get list of devices to be added look for devices in expected
        # not in current
        if self.scaleup:
            changes = self._get_devices_changed(expected_config,
                                                current_config)
        else:
            changes = self._get_devices_changed(current_config,
                                                expected_config)
        self._normalize_changes(changes)
        # Each ring will be list of devices to be modified
        # Each device (in ring) will have following attributes:
        # - zone
        # - ip
        # - device
        # - target (device weight when adding, 0 when removing)
        # - step (amount to change every time till target reached)
        # step is defaulted to 10% or -10% of target

    def _normalize_changes(self, source):
        for builder in RING_TYPES:
            for zone, devadd in source[builder].items():
                self.cluster_changes.setdefault(builder, [])
                devices = []
                for device in devadd:
                    dev = {}
                    dev['ip'] = device['ip']
                    dev['device'] = device['device']
                    if 'metadata' in device:
                        dev['metadata'] = device['metadata']
                    if 'delta' in device:
                        step = int(math.ceil(device['delta'] /
                                             self.iterations))
                    else:
                        step = int(math.ceil(device['weight'] /
                                             self.iterations))
                    if self.scaleup:
                        dev['target'] = device['weight']
                        dev['step'] = step
                    else:
                        dev['target'] = 0
                        dev['step'] = -1 * step
                    devices.append(dev)
                self.cluster_changes[builder].append({zone: devices})

    @staticmethod
    def _get_devices_changed(devices1, devices2):
        devices = {}
        for builder in RING_TYPES:
            devices[builder] = {}
            for zone, devs in devices1[builder].iteritems():
                if zone not in devices2[builder]:
                    devices[builder].setdefault(zone, []).extend(devs)
                else:
                    for dev in devs:
                        found = False
                        add_dev = True
                        for dev2 in devices2[builder][zone]:
                            if found:
                                continue
                            if (dev2['ip'] == dev['ip'] and
                                    dev2['device'] == dev['device']):
                                found = True
                                dev['delta'] = abs(
                                    int(dev2['weight']) - int(dev['weight']))
                                if dev['delta'] == 0:
                                    # Found exact match device don't add
                                    add_dev = False
                        if add_dev:
                            devices[builder].setdefault(zone, []).append(dev)
        return devices

    def _get_ring_devices(self, ring_type, zone):
        builder = self._get_builder(ring_type)
        search_values = {}
        search_values['zone'] = int(zone[1:])
        devs = builder.search_devs(search_values)
        devlist = {}
        for dev in devs:
            devlist[dev['device']] = dev['weight']
        return devlist

    def _get_builder(self, ring_type):
        return RingBuilder.load(os.path.join(self.outdir,
                                             ring_type + '.builder'))

    def _get_all_cluster_devices(self):
        devices = {}
        for builder in RING_TYPES:
            ring_builder = self._get_builder(builder)
            devices[builder] = {}
            for dev in ring_builder.devs:
                if dev is None:
                    continue
                device = {}
                device['ip'] = dev['ip']
                device['weight'] = dev['weight']
                device['device'] = dev['device']
                devices[builder].setdefault('z' + str(dev['zone']),
                                            []).append(device)
        return devices


def capman_helper(iterations, config, meta, outdir, scaleup):
    if os.path.exists(os.path.join(outdir, _STATUS_FILE)):
        raise Exception("Scale up/down operation is currently in progress")
    capman = CapacityManager(iterations, config, scaleup, meta, outdir)
    capman.update_cluster()
