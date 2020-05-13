#Adaptation/derivative of @vishnubob (Giles Hall) python MIDI system
#For BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard Greenberg
#Offered according to GNU Public License Version 3
#See LICENSE in project directory.
#

import six
from warnings import *
from containers import *
from events import *
from struct import unpack, pack
from constants import *
from util import *
ADDRESS_TRACE = False

"""
The MIDI standard specifies that "Meta and Sysex events cancel Running Status", that is, the first "General
Message" immediately following either must have its own Status Byte; it does not acquire the Status Byte
of the latest General Message before the Meta or Sysex. Earlier versions of this program were unaware of this,
and generated invalid MIDI files in which Running Status was expected to persist and be used through them.  Such
files cause some software (notably not this package itself), particularly the Hauptwerk virtual pipe organ
application, to correctly diagnose "missing status byte" (following a Sysex) and fail to accept them.  While
this package is now fixed to generate the needed status bytes on the first "General Message" following Sysex and
Meta events, it must not diagnose their absence in its own input as errors, lest it not be able to read files
it itself has generated in the past.  So I have made it a "Warn", issued only once per input file. -BSG
"""

RUNNING_STATUS_COMPATIBILITY_MESSAGE = """
Warning: Missing Status Byte in MIDI file.  A General Message MIDI event was found following a Sysex or Meta event \
without its own Status Byte.  We will continue to read it, because earlier versions of this package generated such files, \
and you may not be able to regenerate it. However, this file may cause other applications to fail. \
If you can regenerate this file with this version of python-midi, please do so.
This warning will be suppressed for the remainder of the file.
"""


class FileReader(object):

    def __init__(self):
        super(FileReader, self).__init__()
        self.last_event_class = None
        self.RSCompat_reported = False

    def read(self, midifile):
        pattern = self.parse_file_header(midifile)
        for track in pattern:
            self.parse_track(midifile, track)
        return pattern
        
    def parse_file_header(self, midifile):
        # First four bytes are MIDI header
        magic = midifile.read(4)
        if magic != b'MThd':
            raise TypeError ("Bad header in MIDI file.")
        # next four bytes are header size
        # next two bytes specify the format version
        # next two bytes specify the number of tracks
        # next two bytes specify the resolution/PPQ/Parts Per Quarter
        # (in other words, how many ticks per quarter note)
        data = unpack(">LHHH", midifile.read(10))
        hdrsz = data[0]
        format = data[1]
        tracks = [Track() for x in range(data[2])]
        resolution = data[3]
        # XXX: the assumption is that any remaining bytes
        # in the header are padding
        if hdrsz > DEFAULT_MIDI_HEADER_SIZE:
            midifile.read(hdrsz - DEFAULT_MIDI_HEADER_SIZE)
        return Pattern(tracks=tracks, resolution=resolution, format=format)
            
    def parse_track_header(self, midifile):
        # First four bytes are Track header
        magic = midifile.read(4)
        if magic != b'MTrk':
            raise TypeError( "Bad track header in MIDI file: " + str(magic))
        # next four bytes are track size
        trksz = unpack(">L", midifile.read(4))[0]
        return trksz

    def parse_track(self, midifile, track):
        self.RunningStatus = None
        trksz = self.parse_track_header(midifile)
        trackdata = iter(midifile.read(trksz))
        while True:
            try:
                event = self.parse_midi_event(trackdata)
                track.append(event)
            except StopIteration:
                break

    def parse_midi_event(self, trackdata):
        # first datum is varlen representing delta-time
        tick = read_varlen(trackdata)
        # next byte is status message (or perhaps < 128 first byte of "general-message" data)
        stsmsg = midi_byte2int(six.next(trackdata))
        # is the event a MetaEvent?
        if MetaEvent.is_event(stsmsg):
            cmd = midi_byte2int(next(trackdata))
            if cmd not in EventRegistry.MetaEvents:
                warn("Unknown Meta MIDI Event: " + str(cmd), Warning)
                cls = UnknownMetaEvent
            else:
                cls = EventRegistry.MetaEvents[cmd]
            datalen = read_varlen(trackdata)
            data = [midi_byte2int(next(trackdata)) for x in range(datalen)]
            self.last_event_class = cls
            return cls(tick=tick, data=data, metacommand=cmd)
        # is this event a Sysex Event?
        elif SysexEvent.is_event(stsmsg):  # Will only fire if channel is 0 (i.e., 0xF0)
            data = []
            while True:
                datum = midi_byte2int(next(trackdata))
                if datum == 0xF7:
                    break
                data.append(datum)
            self.last_event_class = SysexEvent                
            return SysexEvent(tick=tick, data=data)
        # not a Meta MIDI event or a Sysex event, must be a general message, preceded by status message or not.
        else:
            key = stsmsg & 0xF0
            if SysexEvent.is_event(key): #with channel anded out
                raise RuntimeError("Status byte " + hex(stsmsg) + " (invalid Sysex with nonzero channel) in file.")
            if key in EventRegistry.Events: #All bytes >= 0x80 (128) should be in there by high nibble.  We verify that.
                data = []
                self.RunningStatus = stsmsg
            else:

                # This requires that the leading byte of general-message data can never have a high bit on, i.e
                # cannot be > 127, i.e., pitch, velocity, controller value, etc.  New check in writing side enforces this.
                self.validate_running_status()
                key = self.RunningStatus & 0xF0
                data = [stsmsg]         #Byte was not statusmsg, but first byte of data.

            cls = EventRegistry.Events[key]
            data += [midi_byte2int(next(trackdata)) for x in range(cls.length - len(data))]
            channel = self.RunningStatus & 0x0F
            self.last_event_class = cls
            return cls(tick=tick, channel=channel, data=data)
        #Can never get here.  Every possible byte value is defined.
        raise RuntimeError( "Unknown MIDI Event: 0x%02X" % (stsmsg,))  #guaranteed to crash...was "Warning", now "RuntimeError"

    def validate_running_status(self):
        if self.RSCompat_reported:
            return
        elif self.RunningStatus is None:
            raise RuntimeError("MIDI data (< 128) in stream with no pending Status Message.")
        elif issubclass (self.last_event_class, (MetaEvent, SysexEvent)):
            self.RSCompat_reported = True
            warn(RUNNING_STATUS_COMPATIBILITY_MESSAGE, Warning)
        return

    def has_running_status_errors(self):
        return self.RSCompat_reported


class FileWriter(object):
    def write(self, midifile, pattern):
        self.write_file_header(midifile, pattern)
        for track in pattern:
            self.write_track(midifile, track)

    def write_file_header(self, midifile, pattern):
        # First four bytes are MIDI header
        packdata = pack(">LHHH", 6,    
                            pattern.format, 
                            len(pattern),
                            pattern.resolution)
        midifile.write(b'MThd' + packdata)
            
    def write_track(self, midifile, track):
        buf = b''
        self.RunningStatus = None
        if ADDRESS_TRACE:
            bas = midifile.tell() + len(self.encode_track_header(0))
            print_("TRACK BASE after hdr", bas)
 
        for event in track:
            newstuff = self.encode_midi_event(event)
            if ADDRESS_TRACE:
                print_ (len(buf)+bas, len(newstuff), event)
            buf += newstuff

        buf = self.encode_track_header(len(buf)) + buf
        midifile.write(buf)

    def encode_track_header(self, trklen):
        return b'MTrk' + pack(">L", trklen)

    def encode_midi_event(self, event):
        ret = b''
        ret += write_varlen(event.tick)
        # is the event a MetaEvent?
        if isinstance(event, MetaEvent):
            ret += midi_pack_bytes([event.statusmsg, event.metacommand])
            ret += write_varlen(len(event.data))
            ret += midi_pack_bytes(event.data)
            # https://www.csie.ntu.edu.tw/~r92092/ref/midi/ :
            # Running status is cancelled by any <sysex_event> or <meta_event>
            self.RunningStatus = None ## BSG 24 Aug 2017
        # is this event a Sysex Event?
        elif isinstance(event, SysexEvent):
            ret += midi_pack_bytes([0xF0])
            ret += midi_pack_bytes(event.data)
            ret += midi_pack_bytes([0xF7])
            # https://www.csie.ntu.edu.tw/~r92092/ref/midi/ :
            # Running status is cancelled by any <sysex_event> or <meta_event>
            self.RunningStatus = None ## BSG 3 Jan 2017 (python3 5/5/2017 Hola!)
        # not a Meta MIDI event or a Sysex event, must be a general message
        elif isinstance(event, Event):
            if not self.RunningStatus or \
                self.RunningStatus.statusmsg != event.statusmsg or \
                self.RunningStatus.channel != event.channel:
                    self.RunningStatus = event
                    ret += midi_pack_bytes([event.statusmsg | event.channel])
            if event.data[0] < 0 or event.data[0] > 127:
                raise ValueError( "Leading byte of midi data > 127, aliases status bytes: " + str(event))
            ret += midi_pack_bytes(event.data)
        else:
            raise ValueError( "Unknown MIDI Event: " + str(event))
        return ret

def write_midifile(midifile, pattern):
    if isinstance(midifile, six.string_types):
        midifile = open(midifile, 'wb')
    writer = FileWriter()
    return writer.write(midifile, pattern)

def read_midifile(midifile):
    if isinstance(midifile, six.string_types):
        midifile = open(midifile, 'rb')
    reader = FileReader()
    return reader.read(midifile)
