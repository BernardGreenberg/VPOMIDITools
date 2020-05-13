#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import ConfigMan
import six
import itertools
import midi    
from collections import defaultdict
from operator import attrgetter
from six import print_

# 9 Nov 2017 -- 1 year after the apocalypse

def is_it_note_on(event):
    if isinstance(event, midi.NoteOnEvent):
        return event.velocity > 0 #NoteOn with 0 velocity is a shutoff
    else:
        return False

def round_tick(tick, beatler):
    tock = tick + 1
    mb = beatler(tick)
    mob = beatler(tock)
    if len(str(mob)) < len(str(mb)):
        return tock
    return tick

def event_same(event1, event2):
    return event1.channel == event2.channel and event1.pitch == event2.pitch and \
        is_it_note_on(event1) == is_it_note_on(event2)

def event_suspicious_same(event1, event2):
    return isinstance(event1, midi.NoteEvent) and isinstance(event2, midi.NoteEvent) and \
       event1.tick == event2.tick and event1.pitch == event2.pitch and event1.channel == event2.channel
        

class mixin(object):

    def all_track_collision_analyze(self, fix=True):
        self.midi_data.make_ticks_abs()
        self.remove_zero_length_notes()
        maps = defaultdict(set)  #  map for each channel encountered.
        thieves = set()
        shutoff_coll = turnon_coll = 0
        unitrack = self.heart_of_merge(0)
        for (event_position,event) in enumerate(unitrack):
            if not isinstance(event, midi.NoteEvent):
                continue
            pitch = event.pitch
            m = maps[event.channel]
            if is_it_note_on(event):
                if pitch in m:
                    turnon_coll += 1
                    if self.args.collisions:
                        print_("Redundant note-on ch %d, %s" \
                            %  (event.channel, self.diagpoint(event.pitch, event.tick)))
                    thieves.add(event) #try to elim turnon-collisions
    
                    #4 Jan 2018; 1.0.10 
                else:
                    m.add(pitch)
            elif pitch in m:  #it's a seemingly expected note-off
                m.remove(pitch)
            else:             #it's a "swiped shutoff", needs be fixed
                baddie = self.find_shutoff_thief(unitrack, event_position, event)
                if baddie:  #could be at same tick; detected in subroutine
                    shutoff_coll += 1
                    thieves.add(baddie)
        if thieves:
            print_("%d damaging shutoff collisions, %d keyboard-redundant note-on's."
                   % (shutoff_coll, turnon_coll))
        if fix and thieves:
            self.remove_shutoff_thieves(thieves)  #remove all the thieves we encountered
        self.midi_data.make_ticks_rel()
        return len(thieves)

    def find_shutoff_thief(self, track, this_index, event):
        for back_index in six.moves.range(this_index-1, -1, -1):
            earlier_event = track[back_index]
            if not isinstance(earlier_event, midi.NoteEvent):
                continue
            if event_same(earlier_event, event): #channel, pitch, off-on-ness identical.
                if earlier_event.tick == event.tick:  #Not a problem for THIS event.
                    ftick = round_tick(earlier_event.tick, self.ticks_to_MB)
                    print_("same-time event ch %d, %s, %s" \
                            %  (event.channel, self.diagpoint(event.pitch, ftick), earlier_event))
                    return None
                
                if self.args.collisions:
                    ftick = round_tick(earlier_event.tick, self.ticks_to_MB)
                    print_("Premature shutoff ch %d, %s" \
                            %  (event.channel, self.diagpoint(event.pitch, ftick)))

                return earlier_event
        else:
            print_("Can't find note shutoff thief for", self.diagpoint(event.pitch, event.tick))
        return None

    def remove_shutoff_thieves(self, events):
        print_(len(events), "unison collisions to be removed.")
        for tno,track in enumerate(self.midi_data):
            if any(e in events for e in track):
                new_track = [e for e in track if e not in events]
                oldct,newct = len(track), len(new_track)
                print_("Track %d reduced from %d to %d for %d unison collisions." \
                        % (tno, oldct, newct, oldct - newct))
                track[:] = new_track

    def remove_zero_length_notes(self):
        for tno,track in enumerate(self.midi_data):
            indices_to_remove = set()
            for i in range(0, len(track) - 1):
                event1 = track[i]
                event2 = track[i+1]
                if event_suspicious_same(event1, event2):
                    if is_it_note_on(event1) and not is_it_note_on(event2):
                        if self.args.collisions:
                            ftick = round_tick(event1.tick, self.ticks_to_MB)
                            print_("Track %d[%d] 0-length note ch %d, %s" %
                                    (tno, i, event1.channel, self.diagpoint(event1.pitch, ftick)))
                        indices_to_remove.add(i)
                        indices_to_remove.add(i+1)
            if (len(indices_to_remove)):
                track[:] = [e for (j, e) in enumerate(track) if j not in indices_to_remove]
                print_("Shortened track %d for %d 0-length notes" % (tno, len(indices_to_remove)/2))
            

if __name__ == "__main__":
    import argparse
    from midi_tool_base import ConverterBase, DuckPunchArgs, decode_note

    class CollisionShop(mixin, ConverterBase):
        def __init__(self, args, path):
            ConverterBase.__init__(self, "collision", args)
            self.read_and_time_model(path, 1)
            ct = self.all_track_collision_analyze(fix=False)
            print_(path + ":", ct, "shutoff collisions.")

        def diagpoint(self, note, tick):
            return "%s @tick %s, m+b %s" % (decode_note(note), tick, self.ticks_to_MB(tick))

    parser = argparse.ArgumentParser(description="Check midi files for shutoff collisions.")
    parser.add_argument('Path', nargs="+", help="Path of MIDI file to be checked.")
    parser.add_argument('-v', '--verbose', action="store_true", help="Report each collision (first measure assumed #1)")
    args = parser.parse_args()
    for path in args.Path:
        CollisionShop(DuckPunchArgs(collisions=args.verbose), path)
