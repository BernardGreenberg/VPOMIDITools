#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import sys
assert(sys.version_info[0] >= 3)


# BSG Midi Tools, routes tracks .... (BMT also= Brooklyn Manhattan Transit)
class _BMTErrorMeta(type):
    def __init__(cls, name, bases, dct):
        super(_BMTErrorMeta, cls).__init__(name, bases, dct)
        if name != "BMTError":
            if not hasattr(cls, "String"):
                raise RuntimeError("BMT error class " + name + " has no \"String\" attribute.")

class BMTError(RuntimeError, metaclass=_BMTErrorMeta):
    def __init__(self, ctlstring, *args):
        if len(args) == 0:
            RuntimeError.__init__(self, ctlstring)
        else:
            RuntimeError.__init__(self, ctlstring % tuple(args))

    def report(self, file=sys.stderr):
        red_text = "\033[0;31m" + str(self) + "\033[0;30m"
        all_text = "\n" + self.String + " error: " + red_text + "\n"
        file.write(all_text)
