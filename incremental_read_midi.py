#
#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import sys
assert(sys.version_info[0] >= 3) #2/3/2024

import midi
from collections import namedtuple

"""
Two wrappers around Vishnu Bob's (henceforth, "VB", author of  https://github.com/vishnubob/python-midi, last commit 2015)
internal MIDI FileReader class to facilitate debugging badly-formed MIDI files.  These classes (the second just a better
design than the first) allow events in buggy/broken files (including truncated ones) to be accessed and inspected
sequentially before the point where the problem is encountered.  It also produces the positions and lengths of items
in the file, critical for debugging (but of no other use, so not otherwise available).

VB's system assumes and expects well-formed files.  It recursively descends upon a file, its tracks, and each tracks'
events, constructing and returning a complete three-level tree (with no file-position information).

These classes exploit VB's header-reading and event-construction methods (some of which, annoyingly, expect a binary file
object, and others a byte-producing iterator--hence, we have to construct such iterators with additional functionality).
We shadow his top-level and track-walking methods in order to return structures that contain unfulfilled generators
for their next-level contents instead of lists, allowing descent/iteration almost (there are wrapper structures) identical to
that for for walking VB's tree, but which are fed by a supplier which has not read a single byte beyond the nodes that have
up to that point been pulled from it.

by BSG  week of 13-19 Aug 2017
"""

#These 3 wrappers are cleaner than storing private properties in midi items (which __slots__ might break, anyway).
#The goals are to expose the file pos/len meta-info, as well as preserving the midi pkg's structure meanings.
Header  = namedtuple("Header", ("resolution", "format", "n_tracks", "tracks"))
Track   = namedtuple("Track", ("index", "address", "length", "events"))
Event   = namedtuple("Event", ("address", "length", "running_status", "event"))

#Private iterator class for indexables that allows asking about "pos" (tell()).
class TellableArrayIterator(object): #next vs __next__
    def __init__(self, S):
        self.S = S
        self.pos = 0
    def __iter__(self):
        self.pos = 0
        return self
    def __next__(self):
        if self.pos >= len(self.S):
            raise(StopIteration)
        else:
            v = self.S[self.pos]
            self.pos += 1
            return v
    def tell(self):
        return self.pos


#This class is the earlier "stabat" this, returning a single yield stream of intermixed types which must be selected by type.

class AsyFileReader(midi.FileReader):
    #Generator method returning intermixed items, including those produced by asyparse_track.
    def asyread(self, midifile_path):
        with open(midifile_path, "rb") as midifile:
            header = self.parse_file_header(midifile)
            n_tracks = len(header)
            yield Header(header.resolution, header.format, n_tracks, None)
            for track_number in range(n_tracks):
                for item in self.asyparse_track(midifile, track_number):
                    yield item

    #Generator method returning intermixed track and event items
    def asyparse_track(self, midifile, track_number):
        self.RunningStatus = None
        track_base = midifile.tell()
        track_length = self.parse_track_header(midifile)
        yield Track(track_number, track_base, track_length, None)
        data_base_addr = midifile.tell()
        track_data = TellableArrayIterator(midifile.read(track_length))
        while True:
            try:
                event_offset = track_data.tell()
                running_status = self.RunningStatus
                event = self.parse_midi_event(track_data)
                yield Event(event_offset + data_base_addr, track_data.tell() - event_offset, running_status, event)
            except StopIteration:
                break

class RechargeableFileCharacterIterator(object): #next vs __next__
    def __init__(self, file):
        self.file = file
        self.pos = 0  #file-absolute
        self.flen = self.file.seek(0,2) #EOF
        self.file.seek(self.pos,0)
        self.limit = self.flen

    #Recharge with a new, limited-length "view" into the file
    def set_view(self, length):
        self.pos = self.file.tell()    #pos is file-absolute
        self.limit = self.pos + length #as is self.limit
        
    def __iter__(self):
        return self

    def __next__(self):
        if self.eofp():
            raise(StopIteration)
        else:
            v = self.file.read(1)[0]
            self.pos += 1  #should match file position
            return v

    #Always in total file.
    def tell(self):
        return self.pos

    #For current "view"
    def eofp(self):
        return self.pos >= self.limit

#This class is the second attempt, which returns a proper tree of nested generators, permitting the caller to be
#written isomorphically to one processing via recursive descent a pre-read tree

class AsyTreeFileReader(midi.FileReader):
    #Synchronous method returning a structure containing Track generator
    def access(self, midifile_path):
        self.file = open(midifile_path, "rb") #don't use "with" because of coroutinity; this fcn ends too soon to close.
        self.file_iterator = RechargeableFileCharacterIterator(self.file)
        header = self.parse_file_header(self.file)  #VB calls it "pattern"
        n_tracks = len(header)  #VB header ("pattern") is built on "list".
        return Header(header.resolution, header.format, n_tracks, self.track_gen(n_tracks))

    #Generator method for sequence of Tracks
    def track_gen(self, n_tracks):
        for track_number in range(n_tracks):
            yield self.parse_track(track_number)
        self.file.close()

    #Synchronous method returning a structure containing Event generator
    def parse_track(self, track_number):
        self.RunningStatus = None  #Lower level needs this.
        track_base = self.file.tell()
        track_length = self.parse_track_header(self.file)
        self.file_iterator.set_view(track_length)
        return Track(track_number, track_base, track_length, self.event_list_gen())

    #Generator method for sequence of Events
    def event_list_gen(self):
        while not self.file_iterator.eofp():
            try:
                address = self.file_iterator.tell()
                running_status = self.RunningStatus
                event = self.parse_midi_event(self.file_iterator)
                yield Event(address, self.file_iterator.tell() - address, running_status, event)
            except StopIteration:
                raise RuntimeError("Track and Event ran out of data prematurely at pos %d, last event @ %d" % \
                                   (self.file.tell(), address)) #address can't not be set.
