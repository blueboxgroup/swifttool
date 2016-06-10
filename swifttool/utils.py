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

import logging
import re

from fabric.api import execute, hide, sudo

RING_TYPES = ['account', 'container', 'object']

_host_lshw_output = {}

LOG = logging.getLogger(__name__)


def _parse_lshw_output(output, blockdev):
    disks = re.split('\s*\*', output.strip())
    alldisks = []
    for disk in disks:
        d = {}
        for line in disk.split('\n'):
            match = re.match('^-(\w+)', line)
            if match:
                d['class'] = match.group(1)
            else:
                match = re.match('^\s+([\w\s]+):\s+(.*)$', line)
                if match:
                    key = re.sub('\s', '_', match.group(1))
                    val = match.group(2)
                    d[key] = val
        if 'class' in d:
            alldisks.append(d)

    for d in alldisks:
        if d['logical_name'] == blockdev:
            serial = d['serial']
            match = re.match('\s*(\d+)[MG]iB.*', d['size'])
            if not match:
                raise Exception("Could not find size of disk %s" % disk)
            size = int(match.group(1))
            return size, serial


def _fab_get_disk_size_serial(ip, blockdev):
    with hide('running', 'stdout', 'stderr'):
        global _host_lshw_output
        output = None
        if ip in _host_lshw_output:
            output = _host_lshw_output[ip]
        else:
            output = sudo('lshw -C disk', pty=False, shell=False)
            _host_lshw_output[ip] = output
        return _parse_lshw_output(output, blockdev)


def get_disk_size_serial(ip, blockdev):
    with hide('running', 'stdout', 'stderr'):
        out = execute(_fab_get_disk_size_serial, ip, blockdev, hosts=[ip])
        return out[ip]


def get_devices(zones, metadata=None):
    devices = {}
    for builder in RING_TYPES:
        devices[builder] = {}
        for zone, nodes in zones.iteritems():
            devices[builder][zone] = []
            for node, disks in nodes.iteritems():
                ringdisks = []
                # Add all disks designated for ringtype
                if isinstance(disks['disks'], dict):
                    if builder in disks['disks']:
                        ringdisks += disks['disks'][builder]
                elif isinstance(disks['disks'], list):
                    ringdisks = disks['disks']
            for ringdisk in ringdisks:
                device = {}
                device['weight'] = None
                device['metadata'] = metadata
                device['device'] = None
                device['ip'] = node
                if not isinstance(ringdisk, dict):
                    device['device'] = ringdisk
                    match = re.match('(.*)\d+$', ringdisk)
                    blockdev = '/dev/%s' % match.group(1)
                    # treat size as weight and serial as metadata
                    weight, serial = get_disk_size_serial(node, blockdev)
                    device['weight'] = weight
                    if not metadata:
                        device['metadata'] = serial
                else:
                    device['device'] = ringdisk['blockdev']
                    device['weight'] = ringdisk['weight']
                    if 'metadata' in ringdisk:
                        device['metadata'] = ringdisk['metadata']
                devices[builder][zone].append(device)
    return devices
