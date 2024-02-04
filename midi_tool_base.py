#! /usr/bin/python

# Base class and utilities for MuseScore-to-Hauptwerk python interface suite, incl. MIDI utilities.

#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import sys
assert(sys.version_info[0] >= 3) #2/3/2024

import os
import re

import time
from fractions import Fraction
from collections import defaultdict, namedtuple

import ConfigMan
import midi
assert midi == ConfigMan.getMidi()
import test_status_byte_bug

import MidiTimeModel


from midi import write_midifile

def set_fio_address_trace(b):
    midi.ADDRESS_TRACE = b #won't do much unless using patch package


CHROMATIC_SCALE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#",  "A", "A#", "B"]
NOTE_RE = re.compile("^[a-gA-G](#*|b*) *\\d$")
INSTRUMENTS = {}

#http://nickfever.com/music/midi-cc-list
CONTROL_NAMES = {0:"Bank Select", 1:"Modulation", 2:"Breath Controller", 4:"Foot Controller",
                 5:"Portamento Time", 6:"Data Entry MSB",
                 7:"Volume", 8:"Balance", 10:"Pan", 11:"Expression",
                 91:"EFF1(Reverb)", 92:"EFF2(Tremolo)", 93:"EFF3(Chorus)", 94:"EFF4(Detune)",
                 95: "EFF5(Phaser)", 98: "NRParm LSB", 99: "NRParm MSB",
                 120:"All Sound Off",
                 121:"Reset All Controllers", 122:"Local On/Off", 123:"All Notes Off",
                 126:"Mono Mode", 127:"Poly Mode"}
CONTROL_NAMES.update({("CTRLR %d LSB" % (x-31)):x for x in range(31,64)})
CONTROL_NUMS = {name:num for num,name in CONTROL_NAMES.items()}


class DuckPunchArgs(object):
    def __init__(self, **kwargs):
        for (name,val) in kwargs.items():
            setattr(self,name,val)
    def __getattr__(self, name): #everything else asked, "No!"
        return False

class IdInfo(namedtuple("IdInfoBase", ["name", "composer"])):
    def __str__(self):
        return ('"%s" by %s' % (self.name, self.composer))
    @staticmethod
    def from_yaml(yinfo):
        try:
            return IdInfo(yinfo["Name"], yinfo["Composer"])
        except KeyError:
            raise RuntimeError("Name and Composer fields are required in all driver files.")
    
class EditableTrackIterator(object):   #3/27/2017 #Py3 only, 2/3/2024
    def __init__(self, track):
        self.track = track
        self.index = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self.index >= len(self.track):
            raise StopIteration
        return_value = self.track[self.index]
        self.index += 1
        return return_value
    def delete_last(self, checkval):
        if self.index == 0 or checkval is not self.track[self.index - 1]:
            raise RuntimeError("EditableTrackIterator's element to be deleted is not " + str(checkval))
        del self.track[self.index - 1]
        self.index -= 1
        return None
    def insert_before_last(self, item):
        if self.index == 0:
            raise RuntimeError("EditableTrackIterator insertion before 0 not valid")
        self.track.insert(self.index - 1, item)
        self.index += 1
        return item

def _ki1(x):
    q,r = divmod(x+1, 7) # +1: starts on F, not C, and that's not arbitrary (seq of sharps).
    n = chr((5+4*r)%7 + ord("A")) # 4 is the "fifth" of cycle of fifths. 5 starts with "F".
    if q < 0:
        return n + "b"*(-q)
    elif q > 0:
        return n + "#"*q
    else:
        return n


def interpret_key(n):
    maj = _ki1(n)
    min = _ki1(n+3)
    return maj + " major/" + min + " minor"

def instrument_name(x):
    if not INSTRUMENTS:
        path = ConfigMan.find_app_auxiliary("midi_instruments.txt")
        for line in open(path):
            m = re.match(r"(\d+)\.\s+(.*)", line)
            if m:
                INSTRUMENTS[int(m.group(1))-1] = m.group(2)
    return INSTRUMENTS[x]

def interpret_random_event_1(event):
    if isinstance(event, midi.SetTempoEvent):
        qpm = event.bpm
        fracly = Fraction(qpm).limit_denominator(1000)
        if fracly.numerator % fracly.denominator == 0:
            qpm = fracly
        else:
            qpm ="%7.3f" % qpm
        return "%s q/min, data %s" % (qpm, event.data)
    elif isinstance(event, midi.TimeSignatureEvent):
        return "%d / %d" % (event.numerator, event.denominator)
    elif isinstance(event, midi.KeySignatureEvent):
        return interpret_key(event.alternatives)
    elif isinstance(event, midi.ProgramChangeEvent):
        return "%s (%d)" % (instrument_name(event.value), event.value) 
    elif isinstance(event, midi.ControlChangeEvent):
        cnum = event.control
        cnam = CONTROL_NAMES.get(cnum, "Control") + (" (%d)" % cnum)
        return "%s to %d" % (cnam, event.value)
    elif isinstance(event, midi.MetaEventWithText):
        return '"%s"' % event.text
    elif isinstance(event, midi.UnknownMetaEvent):
        return "meta event code %d %s" % (event.metacommand, event)
    else:
        return "data=%s" % event.data # list ok, not tuple
    

def interpret_random_event(event, no_ticks=False):
    if no_ticks:
        s = ""
    elif hasattr(event, "channel"):
        s = "%d %9d " % (event.channel, event.tick)
    else:
        s = "  %9d " % event.tick
    return s + event.name + ": " +  interpret_random_event_1(event)
        
def decode_note(n):
    n8v = n // 12 - 1
    return "%-2s%d" % (CHROMATIC_SCALE[n % 12], n8v)

def encode_note(s):
    if not NOTE_RE.match(s):
        raise RuntimeError ("Can't encode note \"" + s + "\".")
    basic = CHROMATIC_SCALE.index(s[0].upper())
    s = s[1:] #"bb" is B flat
    return (int(s[-1]) - int("0") + 1)*12 + basic + s.count("#") - s.count("b")

def display_note_event(event,  beatler):

    vel = event.velocity
    if vel != 64:
        sfx = "  v %d" % vel
    else:
        sfx = ""
    if isinstance(event, midi.NoteOnEvent):
        cmd = "On  "
        if vel == 0:
            cmd = "Off0"
            sfx = ""
    else:
        cmd = "Off "
    if beatler:
        brep = "%-9s" % (beatler(event.tick),) #MeasureBeat's look like tuples to %-arg supplier.
    else:
        brep = ""
    print ("%d   %7d %s %s %s%s" % (event.channel, event.tick, brep, cmd, decode_note(event.pitch), sfx))

def compfrac(value):
     m = re.match("(\\d+)/(\\d+)", value)
     if m is None:
         return None
     return Fraction(int(m.group(1)), int(m.group(2)))

def dump_track_channel_content(index, track):
    note_ctr = defaultdict(int)
    ctrl_ctr = defaultdict(int)
    meta_ct = 0
    sysex_ct = 0
    for ev in track:
        if isinstance(ev, midi.NoteEvent):
            note_ctr[ev.channel] += 1
        elif isinstance(ev, midi.MetaEvent):
            meta_ct += 1
        elif isinstance(ev, midi.SysexEvent):
            sysex_ct += 1
        else:
            ctrl_ctr[ev.channel] += 1
    def p(x):
        sys.stdout.write(x)
    p("Track %2d: " % index)
    f = [0]  # Can't set lexical var in Py 2
    def sapd(s):
        if f[0]: p(", ")
        p(s)
        f[0] += len(s)
    def ctrrept(name, ctr):
        if len(ctr):
            sapd(name + ": " + ", ".join("%d:%d" % chct for chct in sorted(ctr.items())))
    def srept(tag, ct):
        if ct:
            sapd("%s %d" % (tag, ct))
    srept("Sysex", sysex_ct)
    srept("Meta", meta_ct)
    ctrrept("Control/ch", ctrl_ctr)
    ctrrept("Notes/ch", note_ctr)
    p("\n")


class ConverterBase(object):
    def __init__(self, app, args):
        self.app = app
        self.args = args

    @staticmethod
    def verify_status_byte_fix():
        test_status_byte_bug.verify()

    @staticmethod
    def REDify(s):
        return "\033[0;31m" + s + "\033[0;30m"

    @staticmethod
    def report_midi_track(index, track):
        dump_track_channel_content(index, track)

    def report_all_tracks(self):
        for (tx, track) in enumerate(self.midi_data):
            self.report_midi_track(tx, track)

    def munge_midi_data(self): #default
        for (tx, track) in enumerate(self.midi_data):
            self.report_midi_track(tx, track)
            self.munge_midi_track(track)

    def write_file(self, input_path, suffix):
        for t in self.midi_data:
            assert t.tick_relative
        self.verify_status_byte_fix()
        if self.args.opath:
            target = self.args.opath
        else:
            dir,sname = os.path.split(input_path)
            basic,ext = os.path.splitext(sname)
            target = os.path.join(dir, basic + "." + suffix + ext)
        target = os.path.abspath(os.path.expanduser(target))
        write_midifile(target, self.midi_data)
        print ("Wrote ", target+",", "len=", os.path.getsize(target), "bytes.\n"+time.ctime())

    def verify_integer_ticks(self, track):
        for i in range(1, len(track)):
            tick = track[i].tick
            if not isinstance(tick, int):
                (m,b) = self.time_model.ticks_to_MB(int(tick))
                raise RuntimeError("Tick " + str(tick) + " " + str(type(tick)) + " at m/b " + 
                  str(m) + " " + str(b))


    def verify_order(self, track):
        for i in range(1, len(track)):
            if track[i].tick < track[i-1].tick:
                (m, b) = self.time_model.ticks_to_MB(track[i-1].tick) # i is bad, use i - 1
                print ("Out of order error near measure", m, "beat", b, file=sys.stderr)
                raise RuntimeError("Ticks in track out of order, index " + str(i))

    def no_midi_please(self, path):
        if path.endswith(".mid"):
            print (self.app + ":", "Please don't give me MIDI files, thanks!", file=sys.stderr)
            sys.exit(2)

    def read_and_time_model(self, path, start_measure = 1, quiet=False):
        input_midi_path = os.path.abspath(os.path.expanduser(path))

        if not quiet:
            print (self.app + ":", "Processing ", input_midi_path)
        self.midi_data = midi.read_midifile(input_midi_path)

        self.time_model = MidiTimeModel.build_time_model(self.midi_data, start_measure)

    def report_app_signature(self, path):
        print (self.app+":", path, "modified: %s" % time.ctime(os.path.getmtime(path)))
        print ("In python", sys.version.split("\n")[0])

    def MB_to_ticks(self, m, b):
        return self.time_model.MB_to_ticks(m,b)

    def ticks_to_MB(self, ticks):
        return self.time_model.ticks_to_MB(ticks)




