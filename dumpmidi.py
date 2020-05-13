#! /usr/bin/python

#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#


from six import print_, PY3
XOrd = int if PY3 else ord

import os
import six
import sys, time
import argparse
import warnings

import ConfigMan
import midi
assert midi == ConfigMan.getMidi()

from MidiTimeModel import TimeModel, build_time_model
from midi_tool_base import dump_track_channel_content, decode_note, interpret_random_event
import incremental_read_midi as IRM

HELP_TEXT = \
"""Dump MIDI file as events, with tracks and relative ticks, and optionally
real-time seconds and/or file position. Default mode is to use standard MIDI readin.
In incremental (-i) mode, will read file and dump one event at a time, to facilitate
debugging malformed files. Output format explained below."""

EPILOG= \
"""Event output format:
  168.537 215  4  2 2   1439    8639  Off C 3 4+2.998
[seconds][AAA  L] T C   RRRR    BBBB  ddddddd m+b
 AAA L = file byte address & length, present only with -i.
 T = Track#, C=Channel# (when applicable)
 RRRR = relative tick stored in event, BBBB = track-accumulated absolute tick
 ddddd = event-specific data, m+b = measure and beat (beat 0 relative)
 seconds = real-time location of event, present only with -s.
 C 3 is standard pitch notation (i.e., an octave below middle C)
"""


_short_key = "t# chn relT   absT  m+beat"
BIG_MEASURE = 10**10


"""
Here is what a standard recursive-descent dump of a python-midi tree looks like.  This is the default mode.
"""

def sekey(args):
    return "seconds " * int(args.seconds)

def dump_midi_file_batchily(file_path, args, tracks_to_dump):
    pattern = midi.read_midifile(file_path)   #VB's python-midi
    #These days, build_time_model can't fail; There are default time-signature and tempo.
    time_model = build_time_model(pattern, args.starting_measure);   #BSG system, not python-midi.
    print_("Resolution %d, format %d, %d tracks." % (pattern.resolution, pattern.format, len(pattern)))

    time_model.dump()
    if args.seconds:
        time_model.dump_tempo()

    print_("\n" + sekey(args) + _short_key)
    for (track_number, track) in enumerate(pattern):
        dump_track_channel_content(track_number, track)
        if tracks_to_dump and track_number not in tracks_to_dump:
            continue
        if not args.brief:
            print_("")

        abs_tick = 0
        for event in track:
            abs_tick += event.tick
            measure = time_model.ticks_to_MB(abs_tick).measure
            if measure < args.fromm: continue
            if measure > args.to: break

            if (not args.brief) and args.seconds:
                print_("%7.3f" % time_model.ticks_to_seconds(abs_tick), end = " ")
            dump_event(abs_tick, track_number, event, time_model, args.brief)

#Shared event-dumper. Note that address/length in batch mode is already printed without a newline.
def dump_event(abs_tick, track_number, event, time_model, brief):
    measure_beat = time_model.ticks_to_MB(abs_tick)
    if isinstance(event, midi.NoteEvent):
        vel = event.velocity
        if not brief:
            if isinstance(event, midi.NoteOffEvent):
                command = "Off "
                v = " v %d" % vel if vel != 0 else ""
            elif isinstance(event, midi.NoteOnEvent):
                command = "On  " if vel > 0 else "Off0"
                v = " v %d" % vel if (vel != 80 and vel !=0) else ""
            else:
                assert False,"Mystery Note Event " + str(event)
            note = decode_note(event.pitch)  #BSG's system, not VB's.        #

            print_ ("%d %d   %4d %7d  %s %s %s%s" % \
                (track_number, event.channel, event.tick, abs_tick, command, note, measure_beat, v))
        """
    elif isinstance(event, midi.TimeSignatureEvent):
        event_tix = "" if brief else "%4d" % event.tick
        print_("%d     %4s %7d  TIME SIGNATURE %d/%d @ %s" % \
            (track_number, event_tix, abs_tick, event.numerator, event.denominator, measure_beat))
         """
    elif not brief:
        chid = event.channel if hasattr(event, "channel") else " "
        event_desc = interpret_random_event(event, True)
        print_("%d %s   %4d   %5d  %-8s %s" % (track_number, chid, event.tick, abs_tick, measure_beat, event_desc))

"""
This is the incremental descent of the future-laden MIDI tree.  It differs from the standard, synchronous, one
only in its need to retrieve raw python-midi objects from their IRM wrappers; the iterations are identical.
Also, the time model is maintained incrementally during the first track.
"""

def dump_midi_file_incrementally(file_path, args, hexfile):

    midi_header = IRM.AsyTreeFileReader().access(file_path)   #Our header structure; python-midi's isn't needed
    time_model = TimeModel(midi_header.resolution,starting_measure=args.starting_measure)

    print_("%d tracks. Resolution=%d, format %d" % (midi_header.n_tracks, midi_header.resolution, midi_header.format))
    if not args.brief:
        print_(sekey(args) +"fadr ln  " + _short_key)
    for track in midi_header.tracks:   # Iterate over "list" (actually generator) of tracks...
        track_number = track.index
        if not args.brief:
            print("")   
        print_ ("TRACK    %d @ byte %d, byte length %d" % (track_number, track.address, track.length))

        abs_tick = 0
        for item in track.events:      #extract "event list" (actually, "event-generator") from wrapper ...
            event = item.event         #extract python-midi event from wrapper ...
            abs_tick += event.tick     #have to account rel/abs ticks no matter how events are obtained....

            #Incremental accumulation of tempo and time-signature models is now possible!
            #At least with my files, these events only occur in track 0.
            if track_number == 0 and isinstance(event, (midi.TimeSignatureEvent, midi.SetTempoEvent)):
                time_model.process_dynamic_event(abs_tick, event)

            if not args.fromm <= time_model.ticks_to_MB(abs_tick).measure <= args.to:
                continue

            #Prefix address and length to each line before dumping it if opted.
            if not args.brief:
                if args.seconds:
                    print_("%7.3f" % time_model.ticks_to_seconds(abs_tick), end = " ")
                print_("%4d %2d" % (item.address, item.length), end = "  ")

            dump_event(abs_tick, track_number, event, time_model, args.brief)
            if args.hex:
                hexfile.seek(item.address, 0)
                data = ["%02X" % XOrd(c) for c in hexfile.read(item.length)]
                print_("   %4d      %s" % (item.address, " ".join(data)))

def check_midi_file(file_path):
    reader = IRM.AsyTreeFileReader()
    if not hasattr(reader,"has_running_status_errors"):
        raise RuntimeError("This version of the MIDI package doesn't support running status error testing.")
    midi_header =  reader.access(file_path)
    warnings.filterwarnings('ignore')
    for track in midi_header.tracks:
        for item in track.events:
            if reader.has_running_status_errors():
                return False
    return True

def argerr(string):
    print_(sys.argv[0] + ':', string, file=sys.stderr)
    sys.exit(1)

def parse_and_validate_args():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=HELP_TEXT, epilog=EPILOG)
    def aa(*argsa,**argsk):
        parser.add_argument(*argsa,**argsk)
    aa('-b', '--brief', action="store_true", help="Only show track summaries, no notes or other events.")
    aa('-c', '--check', action="store_true", help="Check file for Status Byte cancellation failures. Reports, and returns error status to shell if present.")
    aa('-f', '--from', dest="fromm", metavar="meas#", type=int, default=0, help="First measure number to dump.")
    aa('-i', '--incremental', action="store_true", help="Read file (possibly malformed) incrementally, additionally displaying event addresses and lengths.")
    aa('-m', '--measure', dest="starting_measure", metavar="meas#", default=1, type=int, help="Number of first measure in file, default 1, which is wrong for upbeats.")
    aa('-s', '--seconds', action="store_true", help="Show real-time seconds pos. of each event.")
    aa('-t', '--to', type=int, default=BIG_MEASURE,metavar="meas#", help="Last measure number to dump.")
    aa('-T', '--Track',metavar="tk,tk,tk", help="Only dump certain tracks; cannot be used with -i")
    aa('-x', '--hex', action="store_true", help="Dump events in hex as well; requires -i, e.g., -ix")

    aa('path',  nargs=1, help="file to dump")
    args = parser.parse_args()

    if args.check and (args.incremental or args.hex or args.brief or args.seconds):
        argerr("--check cannot be used with --incremental, --hex, --brief, or --seconds.")
    if args.hex and not args.incremental:
        argerr("--hex can only be used in incremental mode.")
    if (args.fromm != 0 or args.to != BIG_MEASURE or args.Track) and (args.check or args.brief):
        argerr("--from/--to/--track cannot be used with --check or --brief.")
    if args.Track and args.incremental:
        argerr("Cannot select specific tracks in incremental (-i) mode.")
    return args

def main():
    args = parse_and_validate_args()
    tracks_to_dump = decode_tracks_arg(args.Track)  #ok if None

    file_path = args.path[0]
    absp = os.path.abspath(file_path)    
    if not os.path.isfile(absp):
        print_("Error: File does not exist:", absp, file=sys.stderr) #no stack trace!
        sys.exit(2)

    if not args.check:
        print_ ("Source %s, modified %s" % \
                (os.path.abspath(__file__), time.ctime(os.path.getmtime(__file__))))
        print_ ("In python", sys.version.split("\n")[0])
        print_ ("\nMIDI file %s, %d bytes, mod. %s." % \
            (os.path.abspath(file_path), os.path.getsize(file_path), time.ctime(os.path.getmtime(file_path))))


    if args.check:
        if check_midi_file(absp):
            print_("OK:     %s has no status byte problems." % absp)
            sys.exit(0)
        else:
            print_("ERRORS: %s has one or more status byte problems." % absp)
            sys.exit(2)
    elif args.incremental:
        print_("Decoding in incremental mode.\n")
        if args.hex:
            with open(file_path, "rb") as hexfile:
                dump_midi_file_incrementally(file_path, args, hexfile)
        else:
                dump_midi_file_incrementally(file_path, args, None)
    else:
        print_("Decoding in batch mode.\n")
        dump_midi_file_batchily(file_path, args, tracks_to_dump)

def decode_tracks_arg(arg):
    if arg is None:
            return None
    tx = arg.split(",")
    try:
        return list(map(int, tx))
    except ValueError:
        argerr("Bad -Tracks arg: " + arg)




if __name__ == "__main__":
    main()
