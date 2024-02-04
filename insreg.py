#! /usr/bin/python

# insreg Virtual Pipe Organ MIDI-preparation system
# Copyright (c) 2016-2020 Bernard S. Greenberg
# GNU General Public License v.3 applies -- see filee LICENSE


SYS_VERSION = "1.0.13"

import sys
assert(sys.version_info[0] >= 3)
import ConfigMan

import os
import sys
from collections import defaultdict
from operator import attrgetter
import itertools

midi = ConfigMan.getMidi()
import yaml
from DupCheckingYamlFix import DCSafeLoader
import argparse
from phraseit import Phraser
from reg_compiler import RegCompiler, commalist, hoist_init_regs

from BMTError import BMTError
from organ import Organ
from reg_events import StopEvent, RoutingEvent, ExpressionEvent
from midi_tool_base import ConverterBase, display_note_event, decode_note, \
    DuckPunchArgs, interpret_random_event, CONTROL_NUMS, IdInfo, set_fio_address_trace
import collision


#Idea here is preventing misspellings.
VALID_PIECE_FIELDS = set("Name Composer Measures Schedule Upbeats Organ StartMeasure SourcePath RouteStaves Octstaves Registration ScoreUrl BeatCounter Combinations PhrasingPath OutputPath UseRealStaves NoOrganOutputPath NoOrganForceStaves Options".split())
REQUIRED_PIECE_FIELDS = set("Name Composer Organ RouteStaves".split())
VALID_OPTIONS = set("MergeTracks Upbeats UseRealStaves GeneralCancel NoGeneralCancel GeneralReset NoPrologue".split())
APP = os.path.splitext(os.path.split(sys.argv[0])[1])[0]
OCTMAP = {"Up" : +12, "Down": -12}
NULL_YAML_NAMES = (None, "None", "null", "") # None comes in for "null", or so I thought
CC_EXPRESSION = CONTROL_NUMS["Expression"]
CC_RESETALL = CONTROL_NUMS["Reset All Controllers"]
KEEP_NONNOTE_EVENTS = (midi.TimeSignatureEvent, midi.KeySignatureEvent, midi.SetTempoEvent,
                        midi.EndOfTrackEvent, midi.TextEvent)
BUT_NOT_EVENTS = (midi.InstrumentNameEvent)
HOIST_DEFAULT = True
SOFT_GENERAL_CANCEL_DEFAULT = True
HARD_GENERAL_CANCEL_DEFAULT = False

class UsageError(BMTError):
    String = "Insreg usage"

def _dttix(track):
    print(map(attrgetter("tick"), track))


def create_TextEvent(cls, tick, text):
    data = list(map(ord, text))
    return cls(tick=tick, text=text, data=data)

def inst_signature(tick, name):
    return create_TextEvent(midi.InstrumentNameEvent, tick, name)



def generic_timebefore_prologue(ppqn, organ, first, delay_secs, controls):
    #Create one measure of 1/4 with quarter-note = seconds-delay wanted.
    vponame = organ.vpo_app_long_name
    prologue = []
    def c(m): prologue.append(m)
    bpm = 60 // delay_secs
    if first:
        c(midi.SetTempoEvent(tick=0,bpm=bpm))
    c(midi.TimeSignatureEvent(tick=0,data=[1,2,24,8]))
    if first:
        c(inst_signature(0, vponame))
        prologue += controls
    c(inst_signature(ppqn, vponame))
    return prologue

def soft_cancel_prologue(ppqn, organ, first, hoisted_revents):
    return generic_timebefore_prologue(ppqn, organ, first, 1, organ.soft_general_cancel(hoisted_revents))

def hard_cancel_prologue(ppqn, gcdata, organ, first):
    return generic_timebefore_prologue(ppqn, organ, first, gcdata["DelaySeconds"],
                                    organ.general_cancel_events(0))

def hoist_nocancel_prologue(ppqn, organ, first, hoisted_revents): #pretty lame
    hoisted_events = list(itertools.chain(*map(StopEvent.execute, hoisted_revents)))
    return generic_timebefore_prologue(ppqn, organ, first, 1, hoisted_events)

class Converter(collision.mixin,ConverterBase):
    def __init__(self, args):
        ConverterBase.__init__(self, APP, args)
        self.hoist_initial_regs = HOIST_DEFAULT
        self.use_soft_general_cancel = SOFT_GENERAL_CANCEL_DEFAULT
        self.use_hard_general_cancel = HARD_GENERAL_CANCEL_DEFAULT
        self.hoisted_events = [ ]
        self.event_map = {StopEvent: self.do_stop_event,
                          RoutingEvent: self.do_routing_event,
                          ExpressionEvent: self.do_expression_event}

        

    # pale shadow of collision.py -- needed here to diagnose mid-note manual changes
    def manage_onmap(self, event, noteset):
        pitch = event.pitch
        if event.velocity == 0:
            if pitch in noteset: #complains if not in set
                noteset.remove(pitch)
        else:
            noteset.add(pitch)

    def do_stop_event(self, rev, iTrack): # The stop and the track need some relays.
        if self.args.notes:
            print(rev.listing_describe())
        return rev.execute() # returns a list now

    def do_routing_event(self, rev, iTrack):
        if iTrack is None:
            return [ ]
        staff = rev.get_staff()
        division = rev.get_division()
        descriptor = "chnl" if self.use_channels else "staff"
        if self.args.routings:
            print ("ROUTING @tick %d %s: %s %d to %s on channel %d" % (rev.tick, rev.point,
               descriptor, staff, division, division.get_channel()))
        s = iTrack.notes_on[staff]
        if s:
            raise UsageError ("Notes being split between divisions, staff %d, %s",
                    staff, self.diagpoint(s.pop(), rev.tick))
        iTrack.routings[staff] = division
        return [ ]

    def do_expression_event(self, exev, iTrack):
        channel = exev.get_channel()
        value = exev.get_expression_value()
        return [midi.ControlChangeEvent(tick=exev.tick, channel=channel, data=[CC_EXPRESSION, value])]

    def dispatch_reg_event(self, rev):
        # not kosher if we just hash -- subclasses won't be recognized - not faster than "if"
        for (clazz, fcn) in self.event_map.items():
            if isinstance(rev, clazz):
                return fcn
        raise RuntimeError("Unknown type of RegEvent: %s " % (rev,)) #bug, not usage error


    def execute_mature_scheduled_events_1(self, pending_events, tick):
        midi_events = [ ]
        while pending_events.mature(tick):
            rev = pending_events.pop(0)
            for item in self.dispatch_reg_event(rev)(rev, None):
                if self.args.generated:
                    print("Generating @tick", rev.tick, "m+b", rev.point, "\n  ", item)
                midi_events.append(item)
        return midi_events

    def diagpoint(self, note, tick):
        return "%s @tick %s, m+b %s" % (decode_note(note), tick, self.ticks_to_MB(tick))

    def create_track_0(self, old_track):
        assert not old_track.tick_relative
        new_track = midi.Track(tick_relative=False)
        for event in old_track:
            if self.schedule.mature(event.tick):
                new_track += self.execute_mature_scheduled_events_1(self.schedule, event.tick)
            if isinstance(event, KEEP_NONNOTE_EVENTS) and not isinstance(event, BUT_NOT_EVENTS): #includes EOT
                assert isinstance(event, midi.MetaEvent)
                if self.args.notes:
                    print(interpret_random_event(event))
                new_track.append(event.__class__(tick=event.tick, data=event.data[:]))
            elif self.args.deletes and not isinstance(event, midi.NoteEvent):
                print("DELETING", interpret_random_event(event))
        return new_track

    def handle_reroutes(self, routing_events, tick, tx, onmap):
        dest = False
        while routing_events.mature(tick):
            rev = routing_events.pop(0)
            if rev.staff == tx:
                dest = rev.division #ignore mature multiple insts, last is correct
                if self.args.routings:
                    print ("ROUTING @tick %d %s: track %d to %s on channel %d" % (rev.tick, rev.point,
                        tx, dest, dest.get_channel()))
                if onmap:
                    raise UsageError ("Notes being split between divisions, staff %d, %s",
                        tx, self.diagpoint(onmap.pop(), rev.tick))
        return dest

    def rewrite_staff_track(self, tx, old_track, routing_events):
        assert not old_track.tick_relative
        onmap = set()
        div = self.routings[tx]
        octave_disp = self.octave_disps.get(tx, 0)
        new_track = midi.Track(tick_relative=False)
        for event in old_track:
            if isinstance(event, midi.NoteEvent):
                if routing_events.mature(event.tick):
                    div = self.handle_reroutes(routing_events, event.tick, tx, onmap) or div
                self.manage_onmap(event, onmap)
                if self.args.notes:
                    display_note_event(event, self.ticks_to_MB)
                new_track.append(event.copy(pitch=event.pitch + octave_disp, channel=div.channel))
            elif isinstance(event, midi.TimeSignatureEvent):
                pass #ignorem
            else:
                pass #key signature, etc.
        if len(new_track):
            self.verify_integer_ticks(new_track)
            self.verify_order(new_track)
            new_track.append(midi.EndOfTrackEvent(tick=self.time_model.final_tick))
            return new_track
        print("Seemingly no notes in routed track #%d." % tx)
        return False

    def insert_signatures(self, tx, track):
        
        assert track.tick_relative
        res = self.midi_data.resolution
        it0 = tx == 0
        hgc_data = self.orgdef.general_cancel
        insert = []
#        if it0 and hgc_data:
#            print "HGHGHAHAHA"
#           # insert +=  hard_cancel_prologue(res, hgc_data, self.orgdef, it0)

        if self.hoist_initial_regs:
            if self.use_soft_general_cancel:
                insert += soft_cancel_prologue(res, self.orgdef, it0, self.hoisted_events)
            else:
                insert += hoist_nocancel_prologue(res, self.orgdef, it0, self.hoisted_events)
        elif self.use_hard_general_cancel and hgc_data:
            insert += hard_cancel_prologue(res, hgc_data, self.orgdef, it0)
        else:
            insert += [inst_signature(0, self.orgdef.vpo_app_long_name)]
        track[0:0] = insert

    def munge_midi_data(self):  #gets overriden by old-code version, when latter present.
        self.midi_data.make_ticks_abs()
        routing_events = self.schedule.clone(RoutingEvent)
        new_midi = midi.Pattern(format=self.midi_data.format, resolution=self.midi_data.resolution,
                                tick_relative=False, tracks=[ ])
        new_midi.append(self.create_track_0(self.midi_data[0]))
        for (tx,old_track) in enumerate(self.midi_data):
            if tx in self.routings:
                new_track = self.rewrite_staff_track(tx, old_track, routing_events.clone())
                if new_track:
                    new_midi.append(new_track)
        self.midi_data = new_midi
        self.midi_data.make_ticks_rel()
#        self.insert_prologues()


    def insert_prologues(self):
        if not self.orgdef.needs_prologue:
            for tx,t in enumerate(self.midi_data):
                assert t.tick_relative
                self.insert_signatures(tx, t)
    
    def expand_relative_path(self, path):
        if path.startswith("/"):
            return path
        elif path.startswith("~"):
            return os.path.expanduser(path)
        else:
            mydir = os.path.split(os.path.abspath(self.args.PieceDef))[0]
            return os.path.join(mydir, path)

    def call_phraser(self, phrasing_path, midi_path):
        print(APP+": calling phraser on", midi_path)
        phraser = Phraser(DuckPunchArgs(check=True,idinfo=self.id))
        phraser.process_files(phrasing_path, midi_path)
        self.midi_data = phraser.midi_data

        print(APP+": Phrasing complete. Beginning registration on phrased output.")

    def verify_required_fields(self, ypiece):
        have_fields = set(ypiece.keys())
        bad_fields = have_fields - VALID_PIECE_FIELDS
        missing_fields = REQUIRED_PIECE_FIELDS - have_fields
        if missing_fields:
            raise UsageError("Missing required field(s) in Registration file: %s ", ", ".join(list(missing_fields)))
        if bad_fields:
            raise UsageError("Unknown fields(s) in Registration file: %s ", ", ".join(list(bad_fields)))
        self.verify_one_of(True, ypiece, ["Measures", "Schedule", "Registration"])

    def setup_maps(self, ypiece):
        rsfield = ypiece["RouteStaves"]
        self.routings = {}
        def route_it(pair):
            (i, divname) = pair
            if not isinstance(i, int):
                raise UsageError("Non-integer as index in division mapping: %s", i)
            if divname not in NULL_YAML_NAMES:
                self.routings[i] = self.orgdef.get_division(divname)

        if isinstance(rsfield, dict):
            for s in rsfield.items(): route_it(s) #map can't be used for effect in Py3k
        else:
            for s in enumerate(commalist(rsfield)): route_it(s)

        self.octave_disps = {}
        for (chan,way) in ypiece.get("Octstaves", {}).items():
            if way not in OCTMAP:
                raise UsageError("Bad octave transpose 'way': %s", way)
            self.octave_disps[chan] = OCTMAP[way]

    def process_files(self, piecefile, input_midi_path):
        self.use_channels = False
        self.report_app_signature(os.path.abspath(__file__))

        self.no_midi_please(piecefile)
        if not os.path.isfile(piecefile):
            raise UsageError("File does not exist: %s", piecefile)
        ypiece = yaml.load(open(piecefile), Loader=DCSafeLoader)
        self.verify_required_fields(ypiece)
        self.id = IdInfo.from_yaml(ypiece)

        self.options = commalist(ypiece.get("Options", None)) or []
        badopt = set(self.options) - VALID_OPTIONS
        if badopt: raise UsageError("Unknown Options: %s", ", ".join(badopt))

        organ_name = ypiece["Organ"]
        print("Processing", self.id)
        print("    for organ at", organ_name)
        self.orgdef = Organ(organ_name)

        cargs = DuckPunchArgs(kombination=self.args.kombination)
        self.schedule = RegCompiler(cargs).compile(ypiece, self.orgdef)

        self.NoOrganOutputPath = ypiece.get("NoOrganOutputPath", None)
        try:
            self.NoOrganForceStaves = set(map(int, (commalist(ypiece.get("NoOrganForceStaves", "") or [ ]))))
        except ValueError as e:
            raise UsageError("Bad staff number in NoOrganForceStaves: " + str(e))

        self.use_hard_general_cancel |= "GeneralReset" in self.options
        if self.orgdef.version < 2 or self.orgdef.needs_prologue:
            self.use_soft_general_cancel = self.hoist_initial_regs = False
        else:
            self.use_soft_general_cancel &= "NoPrologue" not in self.options
            self.use_soft_general_cancel |= "GeneralCancel" in self.options
        if self.use_soft_general_cancel:
            self.use_hard_general_cancel = False

        self.use_channels = "UseChannels" in self.options  #10/17/17 RealStaves now default, unless UseChannels given
        if self.use_channels:
            if self.NoOrganOutputPath:
                raise UsageError("Can't use NoOrganOutputPath in UseChannels mode.")
            if ypiece.get("UseRealStaves","UseRealStaves" in self.options): #old way
                raise UsageError("Conflicting directions to UseChannels and UseRealStaves.")
            print("Routing by channel number = MuseScore instrument/Mixer row#.")

        self.setup_maps(ypiece)

        if input_midi_path is None:
            input_midi_path = self.expand_relative_path(ypiece["SourcePath"])
        if self.args.opath is None and "OutputPath" in ypiece:
            self.args.opath = ypiece["OutputPath"]


        upbp = "Upbeats" in ypiece or "Upbeats" in self.options
        if upbp and "StartMeasure" in ypiece:
            raise UsageError ("Can't use both Upbeats & StartMeasure; Upbeats = StartMeasure:0")
        base_measure = 0 if upbp else ypiece.get("StartMeasure", 1)

        try:
            self.read_and_time_model(input_midi_path, base_measure)
        except IOError as e:
            print(e, file=sys.stderr)
            sys.exit(4)

        if not self.use_channels:
            for i in self.routings:
                if i < 0 or i >= len(self.midi_data):
                    raise UsageError("Staff #%d given as routing source not present in MIDI file.", i)
            print("Midi file has %d tracks; keeping %d (%s)." %
                    (len(self.midi_data), len(self.routings), ", ".join(map(str, self.routings.keys()))))
        if any(n >= len(self.midi_data) for n in self.NoOrganForceStaves):
            raise UsageError("Staff number in NoOrganForceStaves not present in score.")

        if self.args.time_model:
            self.time_model.dump()

        self.schedule.tickize(self.MB_to_ticks)
        if "PhrasingPath" in ypiece:
            phrasing_path = self.expand_relative_path(ypiece["PhrasingPath"])
            self.call_phraser(phrasing_path, input_midi_path)

        if self.hoist_initial_regs or self.use_soft_general_cancel:
           self.hoisted_events = hoist_init_regs(self.schedule)
           print("%d registration events hoisted to before piece." % len(self.hoisted_events))

        self.premunged_data = self.copy_midi_data(self.midi_data)
        self.munge_midi_data()  #dispatches to oldcode iff present

        #This is no longer optional.
        self.all_track_collision_analyze()

        if self.orgdef.needs_prologue and "NoPrologue" not in self.options:
            prol = self.orgdef.get_prefab_prologue()
            self.midi_data[0][0:0] = prol
            print("Inserted prefabricated prologue, %d events." % len(prol))
    
        mergeit = "MergeTracks" in self.options or self.orgdef.needs_track_merge
        if mergeit:
            self.merge_tracks()
        else:
            self.division_partition_tracks()

        self.insert_prologues()  #need some cond


        self.report_all_tracks()

        if self.args.check:
            print(self.REDify("Warning! NO OUTPUT! Output suppressed with -c!!"))
        else:
            if self.NoOrganOutputPath:
                self.output_no_organ_tracks()
            self.write_file(input_midi_path, self.orgdef.vpo_app_short_name)

    
    def verify_one_of(self, mandatory, ydef, values):
        intersection = set(ydef) & set(values)
        if mandatory and not intersection:
            raise UsageError("One of %s is required.", ", ".join(values))
        elif len(intersection) > 1:
            raise UsageError("Can't have more than one of %s.", ", ".join(values))

    @staticmethod
    def copy_event(event):
        assert not isinstance(event, midi.SysexEvent), "Shouldn't be copying Sysex Event"
        if isinstance(event, midi.Event): #buggily bases Sysex in Vishnu Bob
            return event.copy()
        elif isinstance(event, midi.MetaEvent):
            return event.__class__(tick=event.tick,data=event.data[:], metacommand=event.metacommand)
        else:
            assert "Shouldn't be copying non-channel, non-meta event"

    def copy_midi_data(self, md):
        return midi.Pattern(resolution=md.resolution,format=md.format, \
                            tracks=map(self.copy_track_nd_any, md), tick_relative=md.tick_relative)

    def copy_track_nd_any(self,track):
        return midi.Track(events=list(map(self.copy_event, track)), tick_relative=track.tick_relative)

    def copy_track_nd_rel(self, track):
        assert not track.tick_relative
        t = self.copy_nd_track_any(track)
        t.make_ticks_rel()
        return t

    def output_no_organ_tracks(self):
        provided_track_numbers = set(range(len(self.premunged_data)))
        #won't work if some staff is switched to organ later but not mentioned at the start.
        #pretty bogus thing to do, anyway
        organ_track_numbers = set(self.routings)
        wanted_track_numbers = provided_track_numbers - organ_track_numbers
        wanted_track_numbers |= self.NoOrganForceStaves
        if not wanted_track_numbers:
            print(ConverterBase.REDify("No non-organ tracks. Not writing not-organ MIDI."))
            return
        new_midi = midi.Pattern(resolution=self.midi_data.resolution,tick_relative=True)
        if 0 not in wanted_track_numbers:
            new_midi.append(self.copy_track_nd_rel(self.create_track_0(midi_data[0])))
        for i in sorted(wanted_track_numbers):
            assert self.premunged_data[i].tick_relative
            new_midi.append(self.copy_track_nd_any(self.premunged_data[i]))

        path = self.expand_relative_path(self.NoOrganOutputPath)
        midi.write_midifile(path, new_midi)
        print("Wrote non-organ MIDI %s, %d staves, %d bytes." % \
                (path, len(wanted_track_numbers), os.path.getsize(path)))
            
    def heart_of_merge(self, first_index):
        for t in self.midi_data:
            assert not t.tick_relative
        chaingen = itertools.chain(*(self.midi_data[first_index:]))
        new_track = sorted(chaingen, key=attrgetter("tick")) # all in order merged
        return midi.Track(filter(lambda e: not isinstance(e, midi.EndOfTrackEvent), new_track),tick_relative=False)

    def cap_track(self, track):
        assert track.tick_relative
        track.append(midi.EndOfTrackEvent(tick=1))

    def merge_tracks(self):
        print("Collapsing %d tracks into one." % len(self.midi_data))
        assert self.midi_data[0].tick_relative
        self.midi_data.make_ticks_abs() # will check-assert abs
        self.midi_data = midi.Pattern(resolution=self.midi_data.resolution,
                                      format = 0, tick_relative = False,
                                      tracks = [self.heart_of_merge(0)])
        self.midi_data.make_ticks_rel()
        self.cap_track(self.midi_data[0])

    def division_partition_tracks(self):
        assert self.midi_data[0].tick_relative
        self.midi_data.make_ticks_abs()
        tkbychan = {}
        for div in self.orgdef.get_speaking_divisions():
            tkbychan[div.channel] = midi.Track(tick_relative=False)
        for event in self.heart_of_merge(1):
            assert not isinstance(event, midi.SysexEvent),"Shouldn't be copying Sysex"
            if isinstance(event, midi.TimeSignatureEvent):
                for (cno, evs) in tkbychan.items():
                    evs.append(event)
            elif isinstance(event, midi.Event):
                tkbychan[event.channel].append(event)
        track_names = ["conductor"]
        self.midi_data[1:] = [ ]
        for div in self.orgdef.get_speaking_divisions():
            events = tkbychan[div.channel]
            if len(events) and not all(map(lambda e: isinstance(e, midi.TimeSignatureEvent), events)):
                track_names.append(div.main_name)
                self.midi_data.append(events)
        print("Redistributing into %d tracks: %s." %
            (len(self.midi_data), ", ".join(track_names)))
        self.midi_data.make_ticks_rel()
        for t in self.midi_data[1:]:
            self.cap_track(t)

def main():
    print("VPOMIDITools v. "+SYS_VERSION+" Copyright (C) 2016-2020 by Bernard Greenberg",
               "GNU General Public License V.3 applies; see LICENSE.",
              "", sep="\n", file=sys.stderr)

    def aa(*args, **kwargs):
        parser.add_argument(*args, **kwargs)
    parser = argparse.ArgumentParser(description="Convert MuseScore midi to Virtual Pipe Organ midi")
    aa('PieceDef', help="Text (YAML) definition of piece")
    aa('MidiPath',  nargs="?", help="Optional midi path, when not in PieceDef")
    aa('-o', '--opath', metavar="path", help="output path; overrides OutputPath if present. "
       "Default inpath.<vpoapp-name>.mid")
    aa('-c', '--check', action="store_true",help="don't write, just check and process")
    aa('-n', '--notes', action="store_true",help="report 'Note On/Off' events")
    aa('-g', '--generated', action="store_true",help="report MIDI events generated")
    aa('-r', '--routings', action="store_true",help="report staff->division routing events generated")
    aa('-d', '--deletes', action="store_true",help="report deletions of non-note events")
    aa('-X', '--collisions', action='store_true',help="report unison collisions (will fix anyway)")
    aa('-k', '--kombination', action='store_true',help="report combination action")
    aa('-t', '--time_model', action='store_true',help="report time (signature) model")
    aa('-v', '--verbose', action='store_true',help="report all the above (except -n/notes)")
    aa('-O', '--oldcode', action='store_true')
    aa('-A', '--Addresses', action='store_true',help="report generated midi events with file addresses")

    args = parser.parse_args()

    if not args.check:
        ConverterBase.verify_status_byte_fix()
    if args.Addresses:
        set_fio_address_trace(True)
        midi.ADDRESS_TRACE=True
    if args.verbose:
        args.time_model = args.kombination = args.collisions = \
          args.deletes = args.routings = args.generated = True

    try:
        if args.oldcode:
            RunOldcode(args)
        else:
            Converter(args).process_files(args.PieceDef, args.MidiPath)
    except BMTError as e: #all cases of registration, phraser, orgdef, etc.
        e.report(file=sys.stderr)
        sys.exit(2)
    except (yaml.error.YAMLError) as e:
        print(ConverterBase.REDify("YAML error:"), e, file=sys.stderr)
        sys.exit(2)

def RunOldcode(args):
    VALID_OPTIONS.add("UseChannels")
    import channel_capable_insreger as CCI # eg, InsMax(Insreger)
    class channel_capable_converter(CCI.mixin, Converter): pass
    print("Using old channel-capable code.")
    channel_capable_converter(args).process_files(args.PieceDef, args.MidiPath)


if __name__ == "__main__":
    main()    
