#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#
# Subroutinal package to construct models of time-signature and tempo extents of
# a midi-defined piece, and field queries to it about measure/beat, tick position, and real-time.

import sys
assert(sys.version_info[0] >= 3) #2/3/2024

import midi
from fractions import Fraction
from collections import namedtuple
import weakref
from bisect import bisect_left
from operator import attrgetter

EXCEPTIONAL_TIME_SIGNATURES ={
    (6,8) : (3,8),
    (9,8) : (3,8),
    (12,8) : (3,8),
    (2,2) : (1,4) # contentious
    # let (6,4) and (9,4) stay quarters
    }

DEFAULT_TEMPO_QPM = 120.0
DEFAULT_TIME_SIGNATURE = (4,4)
VERY_BIG_NUMBER = 999999999

class TimeModelError(RuntimeError): pass

    
class MeasureBeat(namedtuple("MB0","measure,beat")):
    def __str__(self):
        return "%d+%s" % (self.measure, self.rep_beat(self.beat))

    @staticmethod
    def rep_beat(n):
        if isinstance(n, int):
            return n
        fr = Fraction(n).limit_denominator(1000)
        #print(fr.numerator, fr.denominator,n,Fraction(n))
        if (fr.denominator & (fr.denominator - 1)) == 0: #power of 2
            if n < .001:
                return 0
            else:
                return n
#            return round(n,5)
        if fr.denominator <= 128:
            return fr
        else:
            return round(n,3)

    @staticmethod
    def parse(spec, default_beat=0):
        if isinstance(spec, str) and '+' in spec: # don't test actual int's
            comps = spec.split("+")
            if not (len(comps) == 2 and all(comps)):
                raise ValueError("Invalid measure+beat string: " + spec)
            m = int(comps[0])
            b = float(comps[1])
            if b == int(b):
                b = int(b)
        else:
            m = int(spec)  # will raise ValueError if bad
            b = default_beat
        return MeasureBeat(m, b)

    
class TimeModel(list):
    def __init__(self, resolution = None, starting_measure = 0):
        super(TimeModel, self).__init__(self)
        self.resolution = resolution
        if resolution:
            self.tempo_model = TempoModel(self)
        else:
            self.tempo_model = None
        self.append(TME(0, DEFAULT_TIME_SIGNATURE, self.resolution, starting_measure))
        self[-1].finish(VERY_BIG_NUMBER) # should never BMT (be empty).

    def add_signature(self, tick, signature):
        assert len(self),"Should never be empty TimeModel."
        measure = self.ticks_to_MB(tick).measure
        self[-1].finish(tick)
        if self[-1].tick == tick:
            del self[-1]
        self.append(TME(tick, signature, self.resolution, measure))
        self[-1].finish(VERY_BIG_NUMBER)

    def finish(self, end_tick):
        if self.tempo_model is not None:
            self.tempo_model.finish(end_tick)
        self[-1].finish(end_tick)

    def dur_to_ticks(self, tickloc, dur):
        for tme in self:
            if tme.ticks_to_MB(tickloc) is not None:
                return int(tme.ticks_per_beat*dur/tme.beat)
        raise TimeModelError("dur_to_ticks: Cannot resolve tick %d to measure/beat reference." % tickloc)

    def add_tempo(self, tick, qpm):
        if self.tempo_model is not None:   # [ ] is tricky! (but shouldn't be empty these days)
            return self.tempo_model.add_tempo(tick, qpm)
        else:
            raise TimeModelError("Tempo Model is not available.")

    def ticks_to_seconds(self, ticks):
        if self.tempo_model is not None:   # [ ] is tricky!
            return self.tempo_model.ticks_to_seconds(ticks)
        else:
            raise TimeModelError("Tempo Model is not available.")

    def ticks_to_MB(self, ticks):
        assert len(self), "TimeModel empty at ticks_to_MB time"
        i = bisect_left(self, ticks)
        if i < len(self):
            return self[i].ticks_to_MB(ticks)
        #Can't use "closed set" tick trick of TempoModel, because adjacent measures do not share any points.
        if ticks == self[-1].final_tick:
            return self[-1].ticks_to_MB(ticks, True)
        raise TimeModelError("Cannot resolve tick %d to measure/beat reference." % ticks)

    def MB_to_ticks(self, measure, beat):  # can't use bisect, __cmp__ works on ticks only.
        for tme in self:
            rslt = tme.MB_to_ticks(measure, beat)
            if rslt is not None:
                return rslt
        raise TimeModelError("Cannot resolve (measure,beat) " + str((measure,beat)) + " to tick reference.")

    @property
    def final_tick(self):
        return self[-1].final_tick

    def dump(self):
        for (i, tme) in enumerate(self):
            tme.dump(i)
        print ("END at tick", self[-1].final_tick, "= m.", self[-1].final_measure)

    def dump_tempo(self):
        assert self.tempo_model is not None
        self.tempo_model.dump()

    
    def process_dynamic_event(self, tick, event):
        if isinstance(event, midi.TimeSignatureEvent):
            self.add_signature(tick, (event.numerator, event.denominator))
        elif isinstance(event, midi.SetTempoEvent) and (self.tempo_model is not None):
            self.tempo_model.add_tempo(tick, event.bpm)
        else:
            pass    #allow use as a filter, choosing its own events of interest


class TempoModel(list):
    def __init__(self, time_model):
        self.time_model = weakref.ref(time_model)
        self.ticks_per_quarter = time_model.resolution
        self.seconds_so_far = 0
        self.append(TempoElement(0, self.time_model, DEFAULT_TEMPO_QPM, 0))
        self[-1].finish(VERY_BIG_NUMBER)
        self.seconds_so_far = 0
        self.icache = 0  #why not, we have one...

    def add_tempo(self, tick, quarters_per_minute):
        self.seconds_so_far = self[-1].finish(tick)  #Guaranteed to be somebody there.
        if self[-1].length_ticks == 0: #two successive changes at same tick.
            del self[-1]  #Gets rid of initial stand-in too, if real guy appears at tick 0
        self.append(TempoElement(tick, self.time_model, quarters_per_minute, self.seconds_so_far))
        

    def ticks_to_seconds(self, tick):
        if self[self.icache].includes(tick):
            return self[self.icache].tick_to_seconds(tick)
        i = bisect_left(self, tick)  #should find whole end_tick, too
        if i >= len(self):  #can't happen for legit input; can't be empty and last elt should cover end.
            raise TimeModelError("Can't convert tick to real-time seconds: " + str(tick))
        self.icache = i
        return self[i].tick_to_seconds(tick)

    def finish(self, end_tick):
        self[-1].finish(end_tick)

    def end_seconds(self):
        assert len(self), "TempoModel.end_seconds called on empty model"
        return self.ticks_to_seconds(self[-1].end_tick)

    def dump(self):
        print("Tempo model:")
        for (i, tmpe) in enumerate(self):
            tmpe.dump(i)
        print("END at %8.4f seconds." % self.end_seconds())


class TempoElement(object):
    def __init__(self, tick, time_model_wr, quarters_per_minute, base_seconds):
        self.time_model_wr = time_model_wr 

        self.qpm = quarters_per_minute

        # (t/q)*(q/m) = t/m
        ticks_per_minute = time_model_wr().resolution * float(quarters_per_minute)
        minutes_per_tick = 1.0/ticks_per_minute
        self.seconds_per_tick = minutes_per_tick*60.0

        self.base_tick = tick
        self.base_seconds = base_seconds
        self.end_tick = VERY_BIG_NUMBER
        self.compute_length()

    def includes(self, tick):
        return (tick >= self.base_tick) and (tick <= self.end_tick) #yes the end overlaps for this purpose.

    def __lt__(self, x):  #py3 needs
        return self.end_tick < x #Doesn't include overlap.  If x is my end-tick, I am less than it.

    def __cmp__(self, x):
        if self.base_tick > x: return 1
        if self.__lt__(x): return -1 #see comment there
        return 0

    def compute_length(self):
        self.length_ticks = self.end_tick - self.base_tick
        self.length_seconds = self.seconds_per_tick * self.length_ticks

    def finish(self, tick):
        self.end_tick = tick
        self.compute_length()
        return self.base_seconds + self.length_seconds

    def tick_to_seconds(self, tick):
        assert self.includes(tick) or tick == self.end_tick,"Tempo model element sent a tick not its own."
        return self.base_seconds + (tick - self.base_tick) * self.seconds_per_tick

    def dump(self, i):
        mb = self.time_model_wr().ticks_to_MB(self.base_tick)
        print("TmpE %2d mb %-08s tick %5d sec %6.2f: %5.1f qt/m, %4.0f ticks/sec. Len %5d ticks = %7.3f sec"
                   % (i, mb, self.base_tick, self.base_seconds,
                       self.qpm, 1.0/self.seconds_per_tick, self.length_ticks, self.length_seconds))

class TME(object):  # Time model element  -- new time management system 1/10/17

    def __init__(self, tick, signature, resolution, base_measure):
        self.base_measure = base_measure
        self.len_ticks = self.len_beats = self.len_measures = -1
        self.tick = tick
        (nn, dd) = signature
        if signature in EXCEPTIONAL_TIME_SIGNATURES:
            self.beat = Fraction(*EXCEPTIONAL_TIME_SIGNATURES[signature])
        else:
            self.beat = Fraction(1, dd)
        self.ticks_per_beat = int(resolution * (self.beat / Fraction(1,4)))
        self.signature = signature
        fsignature = Fraction(*signature)
        if fsignature % self.beat != 0:
            raise RuntimeError("Measure length %s does not divide by beat %s." % (signature, self.beat))
        self.beats_per_measure = fsignature // self.beat
        self.ticks_per_measure = self.ticks_per_beat * self.beats_per_measure

    def finish(self, tick):
        self.len_ticks = tick - self.tick
        self.len_beats = self.len_ticks // self.ticks_per_beat
        self.len_measures = self.len_beats // self.beats_per_measure

    def dump(self, i):
       print ("TME %i: @ticks %5d m %2d, sig %s beat %s tpb %d bpm %d len T/B/M (%d %d %d)" \
           %(i, self.tick, self.base_measure, self.signature, self.beat, self.ticks_per_beat, \
              self.beats_per_measure, self.len_ticks, self.len_beats, self.len_measures))

    def ticks_to_MB(self, tick,force=False):
        ticks_into_tme = tick - self.tick
        if ticks_into_tme < 0 or ticks_into_tme >= self.len_ticks:
            if not (force and ticks_into_tme == self.len_ticks):
                return None
        measures_into_tme = ticks_into_tme // self.ticks_per_measure
        measure_tmerel_ticks =  measures_into_tme * self.ticks_per_measure
        beats_into_measure = float(ticks_into_tme - measure_tmerel_ticks) /self.ticks_per_beat
        measure = measures_into_tme + self.base_measure
        return MeasureBeat(measure, beats_into_measure)

    def __lt__(self, x):  #py3 needs
        return self.tick + self.len_ticks <= x #This EXCLUDES the == point, for "I am less than x".

    def __cmp__(self, x):  #for tick searching, not measures
        if self.tick > x: return 1
        if self.__lt__(x): return -1
        return 0

    def MB_to_ticks(self, measure, beat):
        measures_into_tme = measure - self.base_measure
        if measures_into_tme < 0 or measures_into_tme >= self.len_measures:
            return None
        measure_ticks = self.tick + (measures_into_tme * self.ticks_per_measure)
        beat_ticks = int( beat * self.ticks_per_beat ) #beat can be flonum or frac
        return measure_ticks + beat_ticks

    #needed to talk about end of track/piece
    @property
    def final_tick(self):
        return self.tick + self.len_ticks
    @property
    def final_measure(self):
        return self.base_measure + self.len_measures


def build_time_model(midilist, starting_measure=0):
    model = TimeModel(resolution=midilist.resolution, starting_measure=starting_measure)
    assert midilist[0].tick_relative
    cur_tick = 0
    for event in midilist[0]:  #time sigs and tempi must appear here.
        cur_tick += event.tick
        if isinstance(event, midi.TimeSignatureEvent):
            model.add_signature(cur_tick, (event.numerator, event.denominator))
        elif isinstance(event, midi.SetTempoEvent):
            model.add_tempo(cur_tick, event.bpm) #includes incremental logic
    highest_sum = max(0, *[sum(map(attrgetter("tick"), track)) for track in midilist]) # need 2 args!
    model.finish(highest_sum)
    return model
