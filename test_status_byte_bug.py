#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import six
import os
import sys
import midi

"""
The MIDI standard requires "running status" to be decached with the occurrence of
either a Sysex message or a Meta message, and a new status byte output with the next
"General message". As of August 2017, "Vishnu Bob"'s semi-standard "python-midi"
package on GitHub (https://github.com/vishnubob/python-midi) fails to do this. A
Pull Request (submitted patch) for a fix posted by one "MrCheeze"
(https://github.com/MrCheeze/python-midi) has been pending since January 2016, but
as of mid-2015 Vishnu Bob seems to have returned to Trimurti Bob activity, and is
not immanent (package unchanged).  MIDI files produced by that package are
non-compliant, or as MrCheeze less gently frames it, "corrupt".  These files, when
they contain Sysex messages, cause the Hauptwerk application (and an unknown number
of others) to diagnose the error and reject them (or worse).  Unsurprisingly, they
can be read back without error by python-midi as is.

I also have a private version and patches of python-midi that fix this bug, and
tools to facilitate revealing its presence in files already written.  A read-write
pass through any fixed version (including Mr. Cheeze's) will fix such corrupt files.

This single-file package contains minimal functions and a command to test for the
bug in whatever version of python-midi is called up by its "import midi" in whatever
version of Python (2 or 3) in which it runs. Note that Vishnu Bob python-midi has
separate Python2 and Python3 branches (my private version is 2/3 compatible via
six.py).  --BSG 2 Sept 2017
"""


def is_midi_status_byte_problem_ok():
    #There are no ambiguities or optional choices in the file representation of this sequence.
    #It will produce MIDI output of length 33 or 34 in the presence or absence of the bug,
    #respectively. It is absolutely minimal.
    track = midi.Track(
        events=[midi.NoteOnEvent(channel=5,tick=0,pitch=66,velocity=64),
                midi.SysexEvent(tick=1,data=[1]),
                midi.NoteOnEvent(channel=5,tick=2,pitch=66,velocity=0)]) #will get own status iff fixed
    pattern = midi.Pattern(resolution=480, tracks=[track])
    stringfile = six.BytesIO()
    midi.write_midifile(stringfile, pattern)
    length = stringfile.tell()
    return length == 34

def _py_midi_info():
    file_reader_module = midi.FileReader.__module__
    return "\n".join(["Python " + sys.version,
                    "midi module from " + os.path.abspath(midi.__file__),
                    "FileReader in module " + file_reader_module])

def verify():
    if not is_midi_status_byte_problem_ok():
        raise RuntimeError(_py_midi_info() +
                            "\nThis version of the MIDI package fails to write required status bytes.")
    return True


if __name__ == "__main__":
    six.print_(_py_midi_info())
    fixed = is_midi_status_byte_problem_ok()
    six.print_("Status problem is %s here" % ("fixed" if fixed else "BROKEN"))
    sys.exit(0 if fixed else 2)
