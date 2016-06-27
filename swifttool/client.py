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

import argparse
import logging
import os
import sys
import yaml

from fabric.api import env

from swifttool.ring_defintion import ringsdef_helper
from swifttool.manager import capman_helper

LOG = logging.getLogger(__name__)


def _setup_logger(level=logging.INFO):
    logger = logging.getLogger()
    logger.setLevel(level)
    log_handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter(fmt='%(asctime)s %(threadName)s %(name)s '
                            '%(levelname)s: %(message)s',
                            datefmt='%F %H:%M:%S')
    log_handler.setFormatter(fmt)
    logger.addHandler(log_handler)


def scaleup(args):
    _manage(args)


def scaledown(args):
    _manage(args, False)


def _manage(args, scaleup=True):
    if not os.path.isfile(args.config):
        raise Exception("Could not find confguration file '%s'" % args.config)
    capman_helper(args.iterations, args.config, args.meta, args.outdir,
                  scaleup)


def bootstrap(args):
    if not os.path.isfile(args.config):
        raise Exception("Could not find confguration file '%s'" % args.config)
    with open(args.config, 'r') as f:
        config = yaml.load(f)
    ringsdef_helper(config, args.meta, args.outdir)


def main():
    parser = argparse.ArgumentParser(description='Tool to modify swift config')
    parser.add_argument('-d', '--debug', action='store_true',
                        help="display debug messages")
    parser.add_argument('-i', dest='keyfile', help="ssh key to be used")
    parser.add_argument('-u', dest='user', help="user name for ssh")
    parser.add_argument('--config', default='/etc/swift/ring_definition.yml',
                        help="swift ring configuration file")
    parser.add_argument('--outdir', default='/etc/swift',
                        help="output directory for swift rings")
    parser.add_argument('--meta', default=None, help="metadata for session")

    subparsers = parser.add_subparsers()
    parser_genconfig = subparsers.add_parser('bootstrap')
    parser_genconfig.set_defaults(func=bootstrap)

    parser_manage = subparsers.add_parser('scaleup')
    parser_manage.add_argument('--iterations', default=10, type=int,
                               help="number of iterations to modify cluster")
    parser_manage.set_defaults(func=scaleup)

    parser_manage = subparsers.add_parser('scaledown')
    parser_manage.add_argument('--iterations', default=10, type=int,
                               help="number of iterations to modify cluster")
    parser_manage.set_defaults(func=scaledown)

    args = parser.parse_args()

    level = logging.INFO
    if args.debug:
        level = logging.DEBUG
    _setup_logger(level)

    if args.keyfile:
        env.key_filename = args.keyfile
    if args.user:
        env.user = args.user

    try:
        args.func(args)
    except Exception as e:
        LOG.error("There was an error running swifttool: '%s'", e)
        sys.exit(-1)


if __name__ == '__main__':
    main()
