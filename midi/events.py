#Adaptation/derivative of @vishnubob (Giles Hall) python MIDI system
#For BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard Greenberg
#Offered according to GNU Public License Version 3
#See LICENSE in project directory.
#

import six
from six import print_
import math

trace = print_
trace = lambda *x: None

# The __slots__ optimizations have all been removed, but the reasoning is tortuous.
# __slots__ served two purposes, (1) the optimization of storage by not having a dict in
# instances, and (2) the forbidding of the creation of user-defined properties in objects
# which have declared __slots__.  (1) is no longer necessary -- see
# http://www.thomas-cokelaer.info/tutorials/python/slots.html
# Python 3.3 or so implements transparent "shared-key dictionaries" for classes,
# which require virtually no storage if no values are stored, and clone into writeability
# if any are. So that motivation is gone. (2) is trickier -- it's not clear how
# Python makes this work, but the purpose of it is to disallow the creation of per-instance
# dicts.  It works in 2.7 as well as 3.4. This mechanism has two side-effects, both
# debatable, (2a) a coding-purity argument about whether user-property storing is appropriate
# and (2b) the salutary effect of diagnosing incorrectly-spelled property names (as
# opposed to storing an incorrect property).  2b can be simulated by a bunch of tricky
# metaclass programming (see http://stackoverflow.com/questions/3603502/prevent-creating-new-attributes-outside-init/39243708)
# but a non-obvious fact dooms this effort:
#
# It turns out that in spite of __slots__ declarations in both the AbstractEvent base class
# and the terminal Event classes, terminal events (in 2.7) DO NOT REFUSE RANDOM PROPERTY
# STORES, for as hinted on the cokelaer page cited above, the presence or absence of __slots__
# in different elements of an inheritance chain has an effect on the dict behavior of the
# terminal classes. In fact, there is here a layer of classes (MetaEvent, NoteEvent, and
# SysexEvent (the latter terminal)) between AbstractEvent and the terminal classes, and they
# do NOT have __slots_, and this causes the terminal classes to fail to refuse random-
# property stores, and thus, the 2.7 code allows random property stores in the user-visible
# objects.
#
# Thus, neither motivation for the __slots__ perseveres into Python 3.  Finally, the
# __slots__ declarations cannot simply be left alone for Python 3, because they cause
# the assignment of initial values to class variables (e.g., property descriptors) to fail.
# For this reason, they have to go, and for the reasons above, they can go.
#
# The new _data_byte_slots_ pseudo-declarations are similar in form to the retired
# declarations, but are neither to save storage nor inhibit random propertizing: they
# simplify and document containing code.
#
#  --BSG 5/8/2017
#
# Rewritten 8/4/2017 for Python 3.6/2.7 compat (__init_subclass__, no more need for metaclass)


class DataIndexDescriptor(object):
    def __init__(self, dx):
        self.dx = dx
        
    def __get__(self, instance, cls):
        return instance.data[self.dx]
    
    def __set__(self, instance, value):
        instance.data[self.dx] = value

class BigEndianDescriptor(object):
    def __init__(self, length):
        self.length = length
    def rrange(self):
        return range(self.length-1, -1, -1) # for 5, "4 3 2 1 0"
    def __get__(self, instance, owner):
        assert(len(instance.data) == self.length)
        vals = [instance.data[self.length - x - 1] << (8*x) for x in self.rrange()]
        return sum(vals)
    def __set__(self, instance, val):
        instance.data = [(val >> (8*x)) & 0xFF for x in self.rrange()]


#This implements "_data_byte_slots_", which is a parallel mechanism to
#regular __slots__ for an ordered byte vector (and doesn't shut off random props).
def implement_data_byte_slots(cls):
    if hasattr(cls, "_data_byte_slots_"):  #note that this finds inherited attrs
        slots = cls._data_byte_slots_
        trace ("implement_data_bytes %s (%s)" % (cls.__name__, ", ".join(slots)))
        setattr(cls, "length", len(slots))
        for (x, fname) in enumerate(slots):
            setattr(cls, fname, DataIndexDescriptor(x))

class EventRegistry(object):
    Events = {"name":"Events"}
    MetaEvents = {"name":"MetaEvents"}

    @staticmethod
    def register_event_class(catalogue, event_class, key):
        if key is not None:
            trace ("registering %s in %s key %s" %(event_class.name, catalogue["name"], key))
            assert key not in catalogue, "Event %d already registered as %s." % (key, catalogue[key].__name__)
            catalogue[key] = event_class
    @staticmethod
    def verify_all_signatures():
        for key in range(0x80, 0xF0, 0x10):
            if key not in EventRegistry.Events:
                raise RuntimeError("0x%02X not in EventRegistry General Message table." % (key,))

class AbstractEventMetaclass(type):
    def __init__(cls, name, bases, classdict):
        type.__init__(cls, name, bases, classdict)
        trace("Init AbstractEventMetaclass", cls, cls.__bases__)
        implement_data_byte_slots(cls)

@six.add_metaclass(AbstractEventMetaclass)
class AbstractEvent(object):
    name = "Generic MIDI Event"
    length = 0
    #statusmsg = 0x0

    def __init__(self, **kw):
        if type(self.length) == int:
            defdata = [0] * self.length
        else:
            defdata = []
        self.tick = 0
        self.data = defdata
        for key in kw:
            setattr(self, key, kw[key])

    def __cmp__(self, other):
        if self.tick < other.tick: return -1
        elif self.tick > other.tick: return 1
        return cmp(self.data, other.data)

    def __baserepr__(self, keys=[]):
        keys = ['tick'] + keys + ['data']
        body = []
        for key in keys:
            val = getattr(self, key)
            keyval = "%s=%r" % (key, val)
            body.append(keyval)
        body = str.join(', ', body)
        return "midi.%s(%s)" % (self.__class__.__name__, body)

    def __repr__(self):
        return self.__baserepr__()


class EventMetaclass(AbstractEventMetaclass):
    def __init__(cls, name, bases, classdict):
        AbstractEventMetaclass.__init__(cls, name, bases, classdict)
        if not classdict.get("NoEventReg", False):
            trace("Init EventMetaclass", cls, cls.__bases__)
            EventRegistry.register_event_class(EventRegistry.Events, cls, cls.statusmsg)

@six.add_metaclass(EventMetaclass)
class Event(AbstractEvent):
    name = 'Event'
    NoEventReg = True  #This applies only to local class; looked up in dict, not inherited

    def __init__(self, **kw):
        if 'channel' not in kw:
            kw = kw.copy()
            kw['channel'] = 0
        super(Event, self).__init__(**kw)

    def copy(self, **kw):
        _kw = {'channel': self.channel, 'tick': self.tick, 'data': self.data}
        _kw.update(kw)
        return self.__class__(**_kw)

    def __cmp__(self, other):
        if self.tick < other.tick: return -1
        elif self.tick > other.tick: return 1
        return 0

    def __repr__(self):
        return self.__baserepr__(['channel'])

    @classmethod
    def is_event(cls, statusmsg):
        return (cls.statusmsg == (statusmsg & 0xF0))

"""
MetaEvent is a special subclass of AbstractEvent that is not meant to
be used as a concrete class.  It defines a subset of Events known
as the Meta events.
"""

class MetaEventMetaclass(AbstractEventMetaclass):
    def __init__(cls, name, bases, classdict):
        AbstractEventMetaclass.__init__(cls, name ,bases, classdict)
        if not classdict.get("NoEventReg", False):
            trace("Init MetaEventMetaclass", cls, cls.__bases__)
            EventRegistry.register_event_class(EventRegistry.MetaEvents, cls, cls.metacommand)

@six.add_metaclass(MetaEventMetaclass)
class MetaEvent(AbstractEvent):
    statusmsg = 0xFF  # This does get used.
    #metacommand = 0x0  # this must not be defined here; subclasses must define it; registration will diagnose if not.
    name = 'Meta Event'
    NoEventReg = True

    @classmethod
    def is_event(cls, statusmsg):
        return (statusmsg == MetaEvent.statusmsg)


"""
NoteEvent is a special subclass of Event that is not meant to
be used as a concrete class.  It defines the generalities of NoteOn
and NoteOff events.
"""


class NoteEvent(Event):
    NoEventReg=True
    name = "Note"
    _data_byte_slots_ = ['pitch', 'velocity']

class NoteOnEvent(NoteEvent):
    statusmsg = 0x90
    name = 'Note On'

class NoteOffEvent(NoteEvent):
    statusmsg = 0x80
    name = 'Note Off'

class AfterTouchEvent(Event):
    _data_byte_slots_ = ['pitch', 'value']
    statusmsg = 0xA0
    name = 'After Touch'
 
class ControlChangeEvent(Event):
    _data_byte_slots_ = ['control', 'value']
    statusmsg = 0xB0
    name = 'Control Change'

class ProgramChangeEvent(Event):
    _data_byte_slots_ = ['value']
    statusmsg = 0xC0
    name = 'Program Change'

class ChannelAfterTouchEvent(Event):
    _data_byte_slots_ = ['value']  #VishnuBob had data[1], not 0.  Cannot have been right.
    statusmsg = 0xD0
    name = 'Channel After Touch'

class PitchWheelEvent(Event):
    #_data_byte_slots_ = ['pitch', 'hctip']  ## more work
    statusmsg = 0xE0
    length = 2
    name = 'Pitch Wheel'

    def get_pitch(self):
        return ((self.data[1] << 7) | self.data[0]) - 0x2000
    def set_pitch(self, pitch):
        value = pitch + 0x2000
        self.data[0] = value & 0x7F
        self.data[1] = (value >> 7) & 0x7F
    pitch = property(get_pitch, set_pitch)

class SysexEvent(Event):
    statusmsg = 0xF0
    name = 'SysEx'
    NoEventReg = True #shouldn't really be based on "Event", but maybe someone depends on it
    length = 'varlen'

    @classmethod
    def is_event(cls, statusmsg):
        return (cls.statusmsg == statusmsg)

class SequenceNumberMetaEvent(MetaEvent):
    name = 'Sequence Number'
    metacommand = 0x00
    length = 2

class MetaEventWithText(MetaEvent):
    NoEventReg=True    #not inherited by subclasses
    length = "varlen"  #inherited by all subclasses
    def __init__(self, **kw):
        super(MetaEventWithText, self).__init__(**kw)
        if 'text' not in kw:
            self.text = ''.join(chr(datum) for datum in self.data)
    
    def __repr__(self):
        return self.__baserepr__(['text'])

#This technique is more concise than individual cookie-cutter-identical definitions.  All text-bearing metaevents are
#identical in form and cannot have parameters other than their text.  If there are reasons for any to be split out,
#e.g., peculiarities in representation, naming convention, or other program artifacts, simply remove from, or just
#don't place in this list, and define the class with MetaEventWithText parent and "name" and "metacommand" as needed.
#Not so good for definition-aware source-editors ...
TEXT_META_EVENTS = [ #metacommand, name
                     (0x01, "Text"),
                     (0x02, "Copyright"),
                     (0x03, "Track Name"),
                     (0x04, "Instrument Name"),
                     (0x05, "Lyrics"),
                     (0x06, "Marker"),
                     (0x07, "Cue Point"),
                     (0x08, "Program Name"),
                     (0x09, "Device Name")
                     ]

def register_text_meta_events():
    for (metacommand, name) in TEXT_META_EVENTS:
        class_name = name.replace(" ", "") + "Event"  #Roll your own class, or add a third field if this does not apply
        #This is what "class" stmt creation would do by itself -- access and call parent's metaclass, assign global var
        globals()[class_name] = MetaEventWithText.__class__(class_name,                #name
                                                            (MetaEventWithText,),      #bases
                                                            {"name":name, "metacommand":metacommand}) #class dictionary

register_text_meta_events()
try: TextEvent
except NameError:  raise RuntimeError("MetaEventWithText classes didn't get defined.")

class UnknownMetaEvent(MetaEvent):
    name = 'Unknown'
    # This class variable must be overriden in instances by calling the constructor with metacommand=...,
    # which sets a local variable of the same name to shadow the class variable.
    metacommand = None

    def __init__(self, **kw):
        super(MetaEvent, self).__init__(**kw)
        self.metacommand = kw['metacommand']

    def copy(self, **kw):
        kw['metacommand'] = self.metacommand
        return super(UnknownMetaEvent, self).copy(kw)

    def __repr__(self):                            #BSG added 27 Aug 2017
        return self.__baserepr__(['metacommand'])  #....

class ChannelPrefixEvent(MetaEvent):
    name = 'Channel Prefix'
    metacommand = 0x20
    length = 1

class PortEvent(MetaEvent):
    name = 'MIDI Port/Cable'
    metacommand = 0x21

class TrackLoopEvent(MetaEvent):
    name = 'Track Loop'
    metacommand = 0x2E

class EndOfTrackEvent(MetaEvent):
    name = 'End of Track'
    metacommand = 0x2F

class SetTempoEvent(MetaEvent):
    #__slots__ = ['bpm', 'mpqn'] #does nothing in 2.7, not accepted in 3.4
    name = 'Set Tempo'
    metacommand = 0x51
    length = 3
    mpqn = BigEndianDescriptor(3)

    def set_bpm(self, bpm):
        self.mpqn = int(float(6e7) / bpm)
    def get_bpm(self):
        return float(6e7) / self.mpqn
    bpm = property(get_bpm, set_bpm)

class SmpteOffsetEvent(MetaEvent):
    name = 'SMPTE Offset'
    metacommand = 0x54

class TimeSignatureEvent(MetaEvent):
    _data_byte_slots_ = ['numerator', '_raw_denominator', 'metronome', 'thirtyseconds']
    name = 'Time Signature'
    metacommand = 0x58

    def get_denominator(self):
        return 2 ** self._raw_denominator
    def set_denominator(self, val):
        self._raw_denominator = int(math.log(val, 2))
    denominator = property(get_denominator, set_denominator)

class KeySignatureEvent(MetaEvent):
    _data_byte_slots_ = ['_raw_key', 'minor']
    name = 'Key Signature'
    metacommand = 0x59

    def get_alternatives(self):
        d = self._raw_key
        return d - 256 if d > 127 else d
    def set_alternatives(self, val):
        self._raw_key = 256 + val if val < 0 else val
    alternatives = property(get_alternatives, set_alternatives)

class SequencerSpecificEvent(MetaEvent):
    name = 'Sequencer Specific'
    metacommand = 0x7F

EventRegistry.verify_all_signatures()
