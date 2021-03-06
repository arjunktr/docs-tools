import sys
import os.path

import deploy
import stage
import sphinx
import clean
import git
import process
import delegated
import tools
import stats
import generate
from make import make, force, serial, pool

import fabric
fabric.state.output.status = False
fabric.state.output.aborts = True
fabric.state.output.warnings = True
fabric.state.output.running = False
fabric.state.output.user = True

from fabric.api import task
from docs_meta import get_conf
