#! /usr/bin/python
# phraseit.py, a member of the "insreg" MuseScore-to-Hauptwerk python interface suite.

#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#


#TBD: accept m+b in some capacity; maybe "@" phr qualifier, "Schedule"
# for "Measures" (be like insreg).

import ConfigMan
from six import print_, text_type
import midi
import yaml
from DupCheckingYamlFix import DCSafeLoader
import sys
import os
import re
import time
import argparse
import MidiTimeModel
from fractions import Fraction
from operator import attrgetter
from midi_tool_base import compfrac, ConverterBase, display_note_event, IdInfo, \
     dump_track_channel_content, decode_note
from BMTError import BMTError


ALLSTAVES = "ALL" # [ ] would mean "no staves"
REQD_KEYS = set("Name Composer SourcePath Measures".split())
OPT_KEYS = set("StartMeasure Options PhraseDefs".split())
VALID_PHRKEYS = set("measure beat duration staves default_beat default_duration".split())

class PhraserError(BMTError):
    String = "Phraser"

class Phrasing(object):
    measure = None
    beat = 0
    duration = Fraction(1,16)
    staves = ALLSTAVES
    used = False
    def __init__(self, default=None):
        if default:
            self.staves = default.staves
            self.beat = default.beat
            self.duration = default.duration

    def staffp(self, staff):
        return (self.staves == ALLSTAVES) or staff in self.staves

    def __str__(self):
        if self.staves == ALLSTAVES:
            staves = ""
        else:
            staves = " in " + ",".join(map(str,self.staves))
        return "<%s t %d, m+b %d+%d u %s, d %s=%d%s>" % ("Phrasing", self.tick, self.measure, self.beat, self.used, self.duration, self.duration_ticks, staves)

def validate_phrkeys(spec):
    diff = set(spec.keys()) - VALID_PHRKEYS
    if diff:
        raise PhraserError("Unknown key(s) in mapping-style phrasing spec: %s\n"
                           "Whole spec: %s", ", ".join(list(diff)), spec)

def understand_duration(value, defs):
    if isinstance(value, Fraction):
        return value
    f = compfrac(value)
    if f:
        return f
    if defs and value in defs:
        return defs[value]
    else:
        raise PhraserError ("Non-understood duration: %s", value)

def compile_phrasings(model, indications, defs):
    result = []
    default = Phrasing()
    if defs and "default" in defs:
        default.duration = defs["default"]
    else:
        print_("Using default duration as", default.duration)
    for indic in indications:
        p = None
        if isinstance(indic, dict):
            validate_phrkeys(indic)
            if "default_beat" in indic:
                default.beat = indic["default_beat"]
            if "default_duration" in indic:
                default.duration = understand_duration(indic["default_duration"], defs)
            if "measure" in indic:
                p = Phrasing(default)
                p.measure = indic["measure"]
            else:
                continue
            if p is None:
                p = Phrasing(default)

            if "beat" in indic:
                p.beat = indic["beat"]
            if "staves" in indic:
                p.staves = indic["staves"]
            if isinstance(p.staves,int):
                p.staves = [p.staves]
            if "duration" in indic:
                p.duration = understand_duration(indic["duration"], defs)
        elif isinstance(indic, int):
            p = Phrasing(default)
            p.measure = indic
        elif isinstance(indic, list):
            p = Phrasing(default)
            if len(indic) == 2:
                (p.measure, arg) = indic
                if isinstance(arg, int) or isinstance(arg, float):
                    p.beat = arg
                else:
                    p.duration = understand_duration(arg, defs)
            elif len(indic) == 3:
                (p.measure, p.beat, duration) = indic
                p.duration = understand_duration(duration, defs)
            elif len(indic) == 4:
                raise PhraserError ("Please don't use 4-list phrasings, use m/n fractions.")
            else:
                raise PhraserError("Non-understood duration-spec [list]: %s ", indic)
        else:
            raise PhraserError("Unknown phrasing dict element: %s", indic)
        p.tick = model.MB_to_ticks(p.measure, p.beat)
        p.duration_ticks = model.dur_to_ticks(p.tick - 1, p.duration) #prev measure = -1
        p.start = p.tick - p.duration_ticks
        p.used = False
        result.append(p)
    return sorted(result, key=attrgetter("tick"))

def compile_phrasing_defs(instructions):
    result = {}
    for (name,value) in instructions.items():
        f = compfrac(value)
        if not f:
            raise PhraserError("Phrasing macro \"%s\" not in ##/## fraction format: %s", name, value)
        result[name] = f
    return result

class Phraser(ConverterBase):
    def __init__(self, args):
        ConverterBase.__init__(self, "phraseit", args)

    def process_midi_track(self, tx, track):
        dump_track_channel_content(tx, track)
        track.make_ticks_abs()
        queue = list(filter(lambda p: p.staffp(tx), self.phrasings))
        for event in track:
            while queue and queue[0].tick < event.tick:
                queue.pop(0)
            if len (queue) == 0:
                break

            p = queue[0]       #minding our p's and queues
            in_interval = event.tick > p.start   # == does not need any help!
            if isinstance(event, midi.NoteOffEvent):
                if in_interval:
                    self.cut_back_note(event, p, tx)
            elif isinstance(event, midi.NoteOnEvent):
                if event.velocity == 0:
                    if in_interval:
                        self.cut_back_note(event, p, tx)
                else:
                    if event.tick == p.tick:  #This is ok -- note starts at end
                        pass
                    elif in_interval:
                        raise PhraserError \
                          ("Note starts in middle of phrasing, track %d @ %s (%s):\n  %s",
                            tx, self.time_model.ticks_to_MB(p.tick),
                           decode_note(event.pitch), p)

        track[:] = sorted(track, key=attrgetter("tick"))[:]
        self.verify_order(track) # a little silly, but I'm superstitious here...
        track.make_ticks_rel()

    def cut_back_note(self, event, phrasing, tx):
        if self.args.verbose:
            print_("CUTBACK tk %2d@%-6s from %d to %d (%s)" % 
                (tx, self.time_model.ticks_to_MB(phrasing.tick),
                 phrasing.tick - event.tick, phrasing.duration_ticks,
                 decode_note(event.pitch)))
        event.tick = phrasing.start
        self.notes_modified += 1
        phrasing.used = True

    def report_results(self):
        for item in self.phrasings:
            if not item.used:  #get 'em all listed
                print_(self.REDify("UNUSED phrasing: " + str(item)))
        phru = sum (int(p.used) for p in self.phrasings)
        print_(phru, "of", len(self.phrasings), "phrasings used,", end=" ")
        print_(self.notes_modified, "notes modified")

    def process_files(self, piecefile, input_midi_path):
        self.no_midi_please(piecefile)

        try:
            self.piece =  yaml.load(open(piecefile), Loader=DCSafeLoader)
        except (yaml.error.YAMLError, IOError) as e:
            print_(self.REDify("YAML error:"), e, file=sys.stderr)
            sys.exit(4)

        otherid = getattr(self.args, "idinfo", None)

        keys = set(self.piece.keys())
        missing = REQD_KEYS - keys
        if otherid or input_midi_path:
            missing -= {"SourcePath"}
        if missing:
            raise PhraserError ("Required keys missing in %s: %s", piecefile, ", ".join(list(missing)))
        surplus = keys - (REQD_KEYS | OPT_KEYS)
        if surplus:
            raise PhraserError ("Unknown keys in %s: %s", piecefile, ", ".join(list(surplus)))

        self.id = IdInfo.from_yaml(self.piece) #hasattr has documented bugs
        if otherid and (self.id != otherid):
            raise PhraserError("Phrasing file is for %s, not %s, which is calling the Phraser.",
                    self.id, otherid)
        base_measure = self.piece.get("StartMeasure", 1)
        options = self.piece.get("Options", {})
        if "Upbeats" in self.piece or "Upbeats" in options:
            base_measure = 0
        if input_midi_path is None:
            input_midi_path = self.piece["SourcePath"]

        try:
            self.read_and_time_model(input_midi_path, base_measure)
        except IOError as e:
            print_(e, file=sys.stderr)
            sys.exit(4)

        self.notes_modified = 0
        self.phrasing_defs = compile_phrasing_defs(self.piece.get("PhraseDefs", {}))

        self.phrasings = compile_phrasings(self.time_model, self.piece["Measures"], self.phrasing_defs)
        if self.args.list:
            for p in self.phrasings:
                print_(p)

        for (tx, track) in enumerate(self.midi_data):
            self.process_midi_track(tx, track)

        self.report_results()
        if not all(p.used for p in self.phrasings):
            raise PhraserError("There are unconsumed phrasings. Aborting.")

        if not self.args.check:
            self.write_file(input_midi_path, "phrased")

def main():
    parser = argparse.ArgumentParser(description="Add phrasing breaks to MuseScore midi")
    parser.add_argument('PieceDef',  nargs=1, help="Text (YAML) definition of piece and phrasings")
    parser.add_argument('MidiPath',  nargs="?", help="Optional midi path, override one in PieceDef")
    parser.add_argument('-o', '--opath', metavar="path", help="output path; default inpath.phrased.mid")
    parser.add_argument('-c', '--check', action="store_true", help="don't write, just check and process")
    parser.add_argument('-n', '--notes', action="store_true", help="report 'NoteOn/Off' events")
    parser.add_argument('-l', '--list', action="store_true", help="report compiled phrasing schedule")
    parser.add_argument('-v', '--verbose', action="store_true", help="report actions taken")
    args = parser.parse_args()
    try:
        Phraser(args).process_files(args.PieceDef[0], args.MidiPath)
    except PhraserError as e:
        e.report(file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()    
