#Adaptation/derivative of @vishnubob (Giles Hall) python MIDI system
#For BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard Greenberg
#Offered according to GNU Public License Version 3
#See LICENSE in project directory.
#

import os,sys
sys.path.insert(0, os.path.split(__file__)[0])
from containers import *
from events import *
from struct import unpack, pack
from util import *
from fileio import *
