#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import six
from six import print_
import re
import os
import yaml
from DupCheckingYamlFix import DCSafeLoader
import ConfigMan
midi = ConfigMan.getMidi()
from midi_tool_base import CONTROL_NUMS

import orgsys
import functools
import itertools
from operator import attrgetter
import weakref
from collections import defaultdict
from BMTError import BMTError


REQUIRED_FIELDS = {"Name", "Application", "Channels", "Divisions"}
OPTIONAL_FIELDS = {"GeneralCancel", "Synonyms", "ControlsDefaultOn", "CustomControls", "Version", "ProloguePath", "PrologueEndTick", "DefaultP1", "StopModel", "PrologueExpectedName"}
V1_REQ_FIELDS = {"TemplateOn", "TemplateOff", "DefaultP1"}
REMOVE_V3_FIELDS = {"Channels", "Synonyms", "GeneralCancel"}
ADD_V3_OPTIONAL = {"GeneralReset"}


KNOWN_DIVISION_ATTRIBUTES={"Channel", "Expression", "Synonyms", "NonSpeaking", "DefaultP1"}
KNOWN_STOP_ATTRIBUTES={"Address", "Refer", "Synonyms"}
# F can be "Forte" comb. piston, R can be division name.
IGNORABLE_SUFFIXES = {"fach", "voet", "sterk", "ranks", "rk", "rang", "rangs", "rgs", "f", "file"}
SUSTAINABLE_PREFIXES = ("anches", "octaves", "afsluiter") #lower-cased already
FOOTAGE_RANKS_PAT = re.compile("[ivx0-9]*-?[ivx0-9]+(/\\d)?", re.I)
FORBIDDEN_DIVISION_NAMES = ["None", None, "null", "General", ""]

CC_EXPRESSION = CONTROL_NUMS["Expression"]

def compatenc(s):
    if six.PY2:
        return s.encode("UTF-8:")
    else:
        return s

def prntu(*args):
    print_(compatenc(" ".join(map(six.text_type, args))))

def cpj(lors):
    return ", ".join(list(lors))
def rprl(l, single_case=False):
    ll = list(l)
    if single_case and len(ll) == 1:
        return ll[0]
    return "[" + ", ".join(ll) + "]"


class RegError(BMTError):  # raise my Ebenezer
    String = "Registration"


class OrgdefError(BMTError):
    String = "Organ definition"


@six.python_2_unicode_compatible
class RegErrorPt(RegError):
    def __init__(self, point, ctlstring, *args):
        self.bogus_cache = ctlstring % args  # recursion problem in py2
        self.point = point
        RegError.__init__(self, ctlstring, *args)

    def __str__(self):
        if six.PY2:
            s = self.bogus_cache
        else:
            s = RegError.__str__(self)
        if self.point is None:
            return s
        else:
            return "at m+b %s: %s" %(self.point, s)

def verify_attributes(obj, attrs, defined):
    unknown = set(attrs.keys()) - defined
    if unknown:
        raise OrgdefError("Unknown attribute(s) for %s: %s", obj, ", ".join(list(unknown)))

def canonicalize_stop_name_for_searching(name):
    name = name.lower()
    for (p,q) in ((".", ""), ("'", ""), ("-", " ")):
        name = name.replace(p,q)
    name = re.sub(" +", " ", name)
    return name


class NameSet(set):
    def all_variants(self):
        def vary(name):
            return StopNameVariator().gen(name)
        return functools.reduce(set.union, map(vary, self))


#Almost all stops on all organs will not require a real dict.  Cache 1 element.
class CheapWeakDict:
    def __init__(self):
        self.wdict = None
        self._key = None # let val assert if not set

    def __contains__(self, key):
        return (key == self._key) or (self.wdict and key in self.wdict)

    def __len__(self):
        if self._key is None:
            return 0
        elif self.wdict is None:
            return 1
        else:
            return len(self.wdict) #happily includes original value

    def __getitem__(self, key):
        if key == self._key():
            return self.val
        assert self.wdict and key in self.wdict,"Key not found in CheapWeakDict when expected."
        return self.wdict[key]

    def __setitem__(self, key, val):
        if self._key is None:
            self._key = weakref.ref(key)
        if key == self._key():
            self.val = val
        else:
            if self.wdict is None:
                self.wdict = weakref.WeakKeyDictionary()
                self.wdict[self._key()] = self.val
            self.wdict[key] = val

    def items(self):
        return list(self.wdict.items()) if self.wdict else [(self._key(), self.val)]


class StopNameVariator(object):
    def gen(self, name):
        self.variants = set()
        self.recurse(canonicalize_stop_name_for_searching(name).split())
        return self.variants

    def recurse(self, comps):
        self.variants.add(" ".join(comps))
        if len(comps) > 1:
            if comps[-1] in IGNORABLE_SUFFIXES:
                self.recurse(comps[0:-1])
            elif FOOTAGE_RANKS_PAT.match(comps[-1]):
                self.recurse(comps[0:-1])
            elif comps[0] in SUSTAINABLE_PREFIXES:
                self.recurse(comps[0:1])
        
    
class SubstitutableTemplate(object):
    def __init__(self, template, parmnames):
        self.template = template
        self.indices = tuple (template.index(v) for v in parmnames)
    def substitute(self, adr):
        copy = self.template[:]
        for (i, ac) in enumerate(adr):
            copy[self.indices[i]] = ac
        return copy

@six.python_2_unicode_compatible
class Organ(object):
    def __init__(self, organ_name, abs=False):
        if abs:
            path = os.path.abspath(organ_name)
        else:
            path = ConfigMan.find_organ_definition(organ_name)
        prntu (organ_name + " organ definition file " + path)
        self.divisions = set()
        self.divmap = {}
        self.byaddr = {}
        try:
            self.load_yaml(path)
            self.verify_channel_uniqueness()
            self.set_up_general_pseudodiv()
            self.set_up_prologue_controls()
        except RegError as r:
            raise OrgdefError (six.text_type(r))

    def load_yaml(self, path):
        ydef = yaml.load(open(path), Loader=DCSafeLoader)
        self.version = ydef.get("Version", 1)
        ydef_fields = set(ydef.keys())
        required = REQUIRED_FIELDS.copy()
        optional = OPTIONAL_FIELDS.copy()
        if self.version == 1:
            required |= V1_REQ_FIELDS
        if self.version >= 3:
            required -= REMOVE_V3_FIELDS
            optional -= REMOVE_V3_FIELDS
            optional |= ADD_V3_OPTIONAL

        all_fields = required | optional
        dif = ydef_fields-all_fields
        if dif:
            raise OrgdefError("Unknown fields in %s: %s", path, ", ".join(dif))
        difr = required - ydef_fields
        if difr:
            raise OrgdefError("Required fields missing in %s: %s" , path, ", ".join(difr))

        alt_stop_model = ydef.get("StopModel", None)
        self.sysdef = orgsys.OrganSystem.FindAndCreate(ydef["Application"], alt_model=alt_stop_model)

        self.default_p1 = ydef.get("DefaultP1", self.sysdef.default_default_p1())
        assert self.default_p1 is not None # 0 for g.o.

        self.name = ydef["Name"]
        if self.version == 1:
            self.stop_control_templates = [SubstitutableTemplate(ydef[tn], ("P1", "P2")) for tn in ("TemplateOff", "TemplateOn")]

        self.general_cancel = ydef.get("GeneralReset", ydef.get("GeneralCancel", None)) #optional
        self.custom_controls = ydef.get("CustomControls", {})
        self.prologue_path = ydef.get("ProloguePath", None)
        self.prologue_expected_name = ydef.get("PrologueExpectedName", None)
        # ticks in GO-generated prologue are just milliseconds; it's probably
        # measuring and 0 on output would be fine.  So by 10 ms it's surely all done.
        self.prologue_end_tick = ydef.get("PrologueEndTick", 10)
        if self.needs_prologue:
            if not self.prologue_path:
                raise OrgdefError("ProloguePath is required for organ definitions for %s.", self.sysdef.long_name)
            self.prologue = self.read_prefab_prologue()
        else:
            self.prologue = None

        self.controls_default_on = ydef.get("ControlsDefaultOn", {})

        self.expctls = {}
        # Register the registered registers.
        for (divname, stopdata) in ydef["Divisions"].items():
            Division.verify_division_name(divname)
            division = self.add_division(divname)
            division.default_p1 = self.default_p1 #unless attribute changes it
            if "Attributes" in stopdata:
                if self.version < 3:
                    raise OrgdefError("Pseudo-stop \"Attributes\" appears, but not Version 3 orgdef.")
                self.process_division_attributes(stopdata["Attributes"], division)
            elif self.version >= 3:
                raise OrgdefError ('"Attributes" missing from declaration of %s', division)

            for (name, adr) in stopdata.items():
                if name == "Attributes":
                    pass
                elif name == "Expression":
                    self.expctls[division] = adr
                elif isinstance(adr, dict) and self.version >= 3:
                    self.process_stop_attributes(name, adr, division)
                else:
                    if not isinstance(adr, (int, list)):
                        raise OrgdefError('Stop "%s" address must be integer or list: %s', name, adr)
                    addr = tuple (adr) if isinstance (adr, list) else (division.default_p1, adr)
                    self.record_stop_avatar(addr, name, division)

        if "Synonyms" in ydef:
            for (name, synonyms) in ydef["Synonyms"].items():
                if not isinstance(synonyms, list):
                    synonyms = [synonyms]
                self.get_division(name).add_names(synonyms)

        if self.version < 3:
            for (e,d) in enumerate(ydef["Channels"]):
                self.get_division(d).set_channel(e)
        else:
            for div in self.divisions:
                div.process_refers()

        for div in self.divisions:
            div.calculate_variants()

        for div in self.divisions: #not needed in 3, but it checks that this is so.
            div.rehome_stops_speaking()

        for (div, dest) in self.expctls.items():
            target = div if dest is True else self.get_division(dest)
            div.set_expression_route(target)

    def process_division_attributes(self, attributes, division):
        verify_attributes(division, attributes, KNOWN_DIVISION_ATTRIBUTES)
        if "Channel" in attributes:
            division.set_channel(attributes["Channel"])
        elif attributes.get("NonSpeaking", False):
            division.set_channel(None)
        else:
            raise OrgdefError("No channel (or NonSpeaking: Yes) declared for %s", division)
        if "Synonyms" in attributes:
            syns = attributes["Synonyms"]
            if not isinstance(syns, list):
                if "," in syns:
                    raise OrgdefError("Commas in %s division synonyms -- please. Use [...]", name)
                syns = [syns]
            division.add_names(syns)
        if "Expression" in attributes:
            self.expctls[division] = attributes["Expression"]
        if "DefaultP1" in attributes:
            division.default_p1 = attributes["DefaultP1"]


    def process_stop_attributes(self, name, attributes, division):
        verify_attributes("Stop " + name, attributes, KNOWN_STOP_ATTRIBUTES)
        if "Address" in attributes:
            adr = attributes["Address"]
            if not isinstance(adr, (int, list)):
                 raise OrgdefError('Stop "%s" address must be integer or list: %s', name, adr)
            addr = tuple (adr) if isinstance (adr, list) else (division.default_p1, adr)
            stop = self.record_stop_avatar(addr, name, division)
        elif "Refer" in attributes:
            division.refers[name] = attributes["Refer"]
        else:
            raise OrgdefError("Neither 'Address' nor 'Refer' in stop data for %s in %s", name, division.main_name)
        if "Synonyms" in attributes:
            syns = attributes["Synonyms"]
            if isinstance(syns, six.string_types):
                if "," in syns:
                    raise OrgdefError("No commas in %s stop synonyms, please. Use [...]", name)
                syns = [syns]
            for n in syns:
                stop.add_name(n, division)

    def verify_channel_uniqueness(self): # cant screw up < v.3
        dic = defaultdict(list)
        for div in self.get_speaking_divisions():
           dic[div.channel].append(div) 
        for (chan, divs) in dic.items():
            if len(divs) > 1:
                raise OrgdefError("Multiple divisions on channel %d: %s",
                                  chan, ", ".join([d.main_name for d in divs]))

    def __repr__(self):
        return "<Organ %s, %d divisions>" % (self.name, len(self.divisions))

    def __str__(self):
        return self.name

    def add_div_name(self, div, name):
        self.divmap[name] = div

    def add_division(self, divname):
        div = Division(self, divname)
        self.divisions.add(div)
        self.add_div_name(div, divname)
        return div

    def get_division(self, divname, point=None):
        if divname in self.divmap:
            return self.divmap[divname]
        raise RegErrorPt(point, "Division \"%s\" not found in %s." %(divname, self))

    def get_speaking_divisions(self):
        return sorted(filter(lambda x: x.channel is not None, self.divisions),
                      key = attrgetter("channel"), reverse=True)

    def record_stop_avatar(self, address, name, division):
        if address in self.byaddr:
            stop = self.byaddr[address]
            if self.version >= 3:
                raise OrgdefError('Attempt to reuse address %s for "%s" on %s; already assigned to %s.\n' \
                'Use "Synonyms:" or "Refer:" stop attributes in Version 3 orgdef, or resolve duplication.',
                list(address), name, division, stop)

            if stop not in division.stops:
                division.add_stop(stop)
                stop.set_first_name(division, name)
            stop.add_name(name, division)
        else:
            stop = division.create_stop(name, address)
            stop.set_first_name(division, name)
            self.byaddr[address] = stop
        return stop

    def general_cancel_events(self, tick):
        gcdata = self.general_cancel
        if not gcdata:
            raise OrgdefError("General Cancel called for, but does not appear in organ definition.")
        elif "Sysex" in gcdata:
            return [midi.SysexEvent(tick=tick, data=gcdata["Sysex"])]
        elif "System" in gcdata:
            return self.sysdef.ActSystemSingleAction(tick, gcdata["System"])
        else:
            raise OrgdefError("Non-understood GeneralCancel spec: %s", general_cancel)

    @property
    def needs_prologue(self):
        return self.version > 1 and self.sysdef.needs_prologue()

    @property
    def needs_track_merge(self):
        return self.version > 1 and self.sysdef.needs_track_merge()

    @property
    def vpo_app_long_name(self):
        return self.sysdef.long_name

    @property
    def vpo_app_short_name(self):
        return self.sysdef.short_name

    def __str__(self):
        return self.name

    def subst_stop_draw_template(self, status, adr):
        return self.stop_control_templates[int(status)].substitute(adr)

    def set_up_general_pseudodiv(self):
        generals = self.add_division("General")
        self.divisions.remove(generals)
        for div in self.divisions:
            generals.stops.update(div.stops)

    def set_up_prologue_controls(self):
        self.prologue_controls_on = set()
        for (divname, stopnames) in self.controls_default_on.items():
            div = self.get_division(divname)
            if not isinstance(stopnames, list):
                stopnames = [stopnames]
            for stopname in stopnames:
                self.prologue_controls_on.add(div.get_stop(stopname))
        self.prologue_controls = self.get_division("General").stops - self.prologue_controls_on
    
    def soft_general_cancel(self, on_revents):
        onctls = self.prologue_controls_on
        for rev in on_revents:
            onctls.add(rev.stop)

        def exel(stops, status):
            def exes(s):
                s.set_status(status)
                return s.execute(status, 0)
            return list(itertools.chain(*map(exes, stops)))
        return exel(self.prologue_controls, False) + exel(onctls, True) + self.open_expressions()

    def open_expressions(self):
        return [midi.ControlChangeEvent(tick=0, channel=div.expression_route.channel,
                                         data=[CC_EXPRESSION, 127])
                for div in self.get_enclosed_divisions()]

    def get_enclosed_divisions(self):
        return filter(attrgetter("expression_route"), self.divisions)
    
    def get_prefab_prologue(self):
        assert self.prologue
        return self.prologue

    def read_prefab_prologue(self):
        midifile = midi.read_midifile(ConfigMan.find_orgdef_auxl(self.prologue_path))
        t0 = midifile[0]
        abst = 0
        for (i,e) in enumerate(t0):
            abst += e.tick
            e.tick = 0
            if abst >= self.prologue_end_tick:
                return t0[0:i]
            if isinstance(e, midi.InstrumentNameEvent) and self.prologue_expected_name:
                if e.text != self.prologue_expected_name:
                    raise OrgdefError("Instrument name in %s is \"%s\", but organ definition expects \"%s\".",
                                          self.prologue_path, e.text, self.prologue_expected_name)
        return t0[0:-1] # hope not end-track

    def outyaml(self):
        def div_order(div):
            return {0: 50, None: 100}.get(div.channel, div.channel)

        prntu("\nName:", self.name, "\nApplication:", self.vpo_app_short_name)
        prntu("Version:", self.version)
        prntu("StopModel:", self.sysdef.DefaultStopModel.name)
        if self.version < 3:
                prntu("Channels:", rprl(map(attrgetter("main_name"), \
                                             sorted(filter(lambda d:d.channel is not None, \
                                                            self.divisions), \
                                                    key=attrgetter("channel")))))
        if self.default_p1 != self.sysdef.default_default_p1():
            prntu("DefaultP1: ", self.default_p1)
        if self.prologue_path:
            prntu("ProloguePath: ", self.prologue_path)
        if self.prologue_expected_name:
            prntu("PrologueExpectedName: ", self.prologue_expected_name)
        prntu("Divisions:")
        for div in sorted(self.divisions, key=div_order):
            div.outyaml("  ")

class Division(object):
    def __init__(self, organ, name):
        self._organ = weakref.ref(organ)
        self.main_name = name
        self.stops = set()
        self.synonyms = set([name])
        self.stop_map = {}
        self.refers = {}
        self.ambiguous = set()
        self.channel = None
        self.expression_route = None

    @staticmethod
    def verify_division_name(name):
        if name in FORBIDDEN_DIVISION_NAMES:
            raise OrgdefError("Forbidden division name: \"%s\". Do not use.", name)

    @property
    def organ(self):
        return self._organ()

    def set_expression_route(self, alium):
        self.expression_route = alium

    def get_expression_channel(self):
        return self.expression_route.get_channel()

    def set_channel(self, channel):
        self.channel = channel

    def get_channel(self):
        return self.channel

    def is_speaking(self):
        return self.channel is not None  #pedal is 0, not truthy!

    def create_stop(self, name, address):
        if all(isinstance(x, int) for x in address):
            if self.organ.version == 1:
                stop = SpeakingStop(self, name, address)
            else:
                stop = SystemSpeakingStop(self, name, address, self.organ.sysdef)
        elif address[0] == "CustomControl":
            stop = CustomControlStop(self, name, address)
        elif address[0] == "System" and self._organ().version > 1:
            stop = SystemControlStop(self, name, address, self._organ().sysdef)
        else:
            raise OrgdefError("Non-understood stop address %s for %s on %s", address, name, self)
        self.add_stop(stop)
        stop.add_name(stop.main_name, self)
        return stop

    def add_stop(self, stop):
        stop.ensure_div_name_set(self)
        self.stops.add(stop)
        return stop
    
    def get_stop(self, name, point=None):
        key = canonicalize_stop_name_for_searching(name)
        if key not in self.stop_map:
            symptom = "ambiguous" if key in self.ambiguous else "not found"
            raise RegErrorPt(point, '"%s" %s in %s "%s".', name, symptom, self.organ.name, self.main_name)
        return self.stop_map[key]

    def add_names(self, names):
        for n in names:
            self.verify_division_name(n)
        self.synonyms.update(names)
        for n in names:
            self.organ.add_div_name(self, n)

    def process_refers(self):
        for (name, pair) in self.refers.items():
            (divname, target_name) = pair
            div = self.organ.get_division(divname)
            #name tables not set up yet -- that's why we're here.
            for stop in div.stops:
                if stop.main_name == target_name:
                    stop.ensure_div_name_set(self)
                    stop.add_name(name, self)
                    self.stop_map[canonicalize_stop_name_for_searching(name)] = stop
                    break
            else:
                raise OrgdefError('"Refer:" target %s pair not found there. Must match declared name exactly.', pair)

    def calculate_variants(self):
        all = set()
        self.ambiguous = set()#used at lookup time to distinguish "not found" from "ambiguous"
        for stop in self.stops:
            variants = stop.get_div_name_set(self).all_variants()
            self.ambiguous.update(all & variants)
            all.update(variants)
        for stop in self.stops:
            variants = stop.get_div_name_set(self).all_variants()
            for v in variants - self.ambiguous:
                self.stop_map[v] = stop

    def rehome_stops_speaking(self):
        for stop in self.stops:
            stop.rehome_to_speaking()

    def outy_div_attributes(self, ind):
        ind4 = ind + "   "
        rph = " residing here, %d secondary avatars"  % len(self.refers) if self.refers else ""
        prntu("\n%s%s:  # %d stops/controls%s." %  (ind, self.main_name, len(self.stops), rph))
        prntu(ind, " Attributes:")
        if self.channel is None:
            prntu(ind4, "NonSpeaking: Yes")
        else:
            prntu(ind4, "Channel:    ", self.channel)
        if len(self.synonyms) > 1:
            prntu(ind4, "Synonyms:   ", rprl(self.synonyms - {self.main_name}, True))
        if self.expression_route:
            prntu(ind4, "Expression: ", self.expression_route.main_name)
        if self.default_p1 != self.organ.default_p1:
            prntu(ind4, "DefaultP1: ", self.default_p1)


    def outyaml(self, ind):
        ind2 = ind + " "
        try:
            sort_key = stop_sorter.key
        except NameError:
            sort_key = attrgetter("main_name")

        if self.organ.version >= 3:
            self.outy_div_attributes(ind)
        else:
            synset = self.synonyms - {self.main_name}
            synstr = ", synonyms: " + ", ".join(list(synset)) if synset else ""
            prntu("\n%s%s:  # %d stops/controls. Channel %s%s." % \
                (ind, self.main_name, len(self.stops), self.channel, synstr))
        prntu()

        ind2a = ind2 + " "
        for stop in sorted(self.stops, key=sort_key):
            stop.outyaml(ind2a, self)
        for (name, target) in self.refers.items():
            prntu(ind2, "%-15s {Refer: %s}" % (name+":", rprl(target)))

    def __repr__(self):
        return "<Division " + self.main_name + ", " + str(len(self.stops)) + " stops>"

@six.python_2_unicode_compatible
class Stop(object):
    def __init__(self, division, name, address):
        self.set_home(division, name)
        self.address = address
        self.status = False #drawn or not
        self.cstatus = False # compile-time status.
        self.namesets = CheapWeakDict()
        self.ensure_div_name_set(division)
        self.set_first_name(division, name)

    def set_first_name(self, division, name):
        self.namesets[division].first = name

    @property
    def division(self):
        return self._division()

    def set_home(self, division, name):
        self.main_name = name
        self._division = weakref.ref(division)

    def ensure_div_name_set(self, div):
        if div not in self.namesets:
            self.namesets[div]  = NameSet()

    def get_div_name_set(self, div):   #will fail if not there, no assert needed
        return self.namesets[div]

    def add_name(self, name, division):
        self.get_div_name_set(division).add(name)

    def set_status(self, status):
        self.status = status

    def substitute_draw_template(self, status):
        return self.division.organ.subst_stop_draw_template(status, self.address)

    def rehome_to_speaking(self):
        if (not self.division.is_speaking()) and len(self.namesets) > 1:
            assert self.division.organ.version < 3,"Shouldn't be rehoming in V3"
            for (division, names) in self.namesets.items(): #this gets strong references
                if division.is_speaking():
                    self.set_home(division, self.namesets[division].first)
                    #print_("Rehomed to", self)
                    break
            else:
               assert False, "Can't find speaking division hosting " + str(self)

    def outyaml(self, ind, containing_division):
        org = self.division.organ
        nameset = self.get_div_name_set(self.division)
        nprdiv = self.division != containing_division #only happens < 3
        addendum = " # " + self.__repr__() if nprdiv else ""
        adr = self.address[1] if self.address[0] == self.division.default_p1 else list(self.address)
        if org.version < 3 or len(nameset) < 2:
            prntu(ind + ("%-15s %s%s" % (nameset.first+":", adr, addendum)))
            if len(nameset) > 1:
                prntu(ind,"# Multiple names on", self.division.main_name + ":", cpj(nameset))
        else:
            prntu(ind+("%-15s" % (nameset.first+":")))
            prntu(ind+("  Address:     %s" % adr))
            prntu(ind+("  Synonyms:    %s" % rprl(nameset - {self.main_name}, True)))

    def __str__(self):
        return self.main_name + " on " + self.division.main_name

    def __repr__(self):
        if six.PY2:
            return ("<" + type(self).__name__ + " " +self.__unicode__() + ">").encode("utf-8")
        else:
            return ("<" + type(self).__name__ + " " +self.__str__() + ">")

class SpeakingStop(Stop):
    def execute(self, status, tick):
        data = self.substitute_draw_template(self.status)
        return [midi.SysexEvent(tick=tick, channel=0, data=data)]

class SystemSpeakingStop(Stop):
    def __init__(self, div, name, address, sysdef):
        super(SystemSpeakingStop, self).__init__(div, name, address)
        self.sysdef = sysdef
    def execute(self, status, tick):
        return self.sysdef.ActDefaultStopModel(tick, self.address, status)

class SystemControlStop(Stop):
    def __init__(self, div, name, address, orgsys):
        (ctltype, self.ctlname) = address #type already checked
        if not orgsys.isControlKnown(self.ctlname, True):
            raise RegError("Stop \"%s\": system control \"%s\" not defined or not reversible.", name, self.ctlname)
        Stop.__init__(self, div, name, address)
        self.orgsys = orgsys
    def execute(self, status, tick):
        return self.orgsys.ActSystemReversible(tick, self.ctlname, status)

class CustomControlStop(Stop):
    def __init__(self, div, name, address):
        (ctltype, ctlname) = address #type already checked
        try:
            self.ctldata = div.organ.custom_controls[ctlname]
        except KeyError:
            raise RegError("Stop %s custom control %s not defined.", name, ctlname)
        Stop.__init__(self, div, name, address)

    def execute(self, status, tick):
        stuff = self.ctldata[status]
        car,cdr = stuff[0],stuff[1:]
        return [midi.ControlChangeEvent(tick=tick,channel=car,data=x) for x in cdr]



if __name__ == "__main__":
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="Test organ defintion file.")
    parser.add_argument("orgname", nargs="+",help="organ-names or orgdef-pathnames")
    parser.add_argument("-s", "--stop" ,help="stop-name to look up")
    args = parser.parse_args()

    try:
        import stop_sorter
    except ImportError:
        pass
    try:
        for orgname in args.orgname:
            o = Organ(orgname, abs=orgname.endswith("orgdef"))
            if args.stop:
                div,stop = args.stop.split(":")
                print_(o.get_division(div).get_stop(stop))
            else:
                o.outyaml()
    except BMTError as e:
        # o will never have the "right" value when creation fails!
        print_("\nFor orgdef %s:" % orgname, file=sys.stderr)
        e.report(file=sys.stderr)
        sys.exit(2)
