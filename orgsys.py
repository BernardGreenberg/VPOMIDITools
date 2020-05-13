#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

from six import print_, add_metaclass
import sys
import ConfigMan
import yaml
from DupCheckingYamlFix import DCSafeLoader
import midi
from collections import defaultdict
from BMTError import BMTError

REFK = "Refer"
KNOWN_AND_REQD_SMOPTS = set(["Name", "Channel"])

def dict_merge(dict1, dict2):
    d = dict1.copy()
    d.update(dict2)
    return d

_VisibleControlModelClasses = []

class ControlModelMetaclass(type):
    def __init__(cls, name, bases, classdict):
        type.__init__(cls, name, bases, classdict)
        if "YAMLKey" in classdict:
            _VisibleControlModelClasses.append(cls)

class OrganSystemDefinitionError(BMTError):
    String = "Organ system definition"

class Orgdef2Error(BMTError):
    String = "Organ definition"

class ElementTemplate(object):
    def __repr__(self):
        return "<%s %s data=%s at %s>" % \
            (self.__class__.__name__, self.midi_class.__name__, self.data, hex(id(self)))

    def __init__ (self, data, channel, parameters):
        self.channel = channel
        if not (isinstance(data, list) and len(data) >= 1 and isinstance(data[0], str)):
            raise OrganSystemDefinitionError ("Organ control data not a list starting with a string: %s", data)
        midi_class_name = data[0] + "Event"
        if not hasattr(midi, midi_class_name):
            raise OrganSystemDefinitionError("%s not known in midi class library.", midi_class_name)
        self.midi_class = getattr(midi, midi_class_name)
        self.want_channel = not issubclass(self.midi_class, (midi.MetaEvent, midi.SysexEvent))
        self.data = data[1:]
        self.param_map = defaultdict(list)
        for (i, d) in enumerate(self.data):
            if isinstance(d, int):
                pass
            elif d in parameters:
                self.param_map[parameters.index(d)].append(i)
            else:
                raise OrganSystemDefinitionError("Unknown datum in control class def list: %s ", d)

    def substitute(self, tick, values):
        output = self.data[:]
        for (i, v) in enumerate(values):
            if not isinstance(v, int):
                raise OrganSystemDefinitionError("Non-integer supplied to organsys midi command: %s", v)
            for dix in self.param_map[i]:
                output[dix] = v
        if self.want_channel:
            assert self.channel is not None # 0 is ok!!
            return self.midi_class(tick=tick, channel=self.channel, data=output)
        else:
            return self.midi_class(tick=tick, data=output)

@add_metaclass(ControlModelMetaclass)
class BaseModel(object):
    def __init__(self, ydict, name, orgsys):
        super(BaseModel, self).__init__()
        if REFK in ydict:
            refer_base = orgsys.get_model(ydict[REFK])
            del ydict[REFK]
            ydict = refer_base.merge_defparms(name, ydict)
        self.defining_parms = ydict
        self.name = name
        self.channel = ydict.get("Channel", None)
        self.parameters = []
        self.parameter_defaults = []
        self.default_p1 = None
        for p in ydict["Parameters"]:
            if isinstance(p, list):
                (pn, d) = p
                if pn == "P1":
                    self.default_p1 = d
                self.parameters.append(pn)
                self.parameter_defaults.append(d)
            else:
                self.parameters.append(p)
                self.parameter_defaults.append(None)

    def merge_defparms(self, target_name, new_parms):
        diff = set(new_parms) - set(self.defining_parms)
        if diff:
            raise Orgdef2Error("Customization Params (%s) for control class %s not in base class %s.",
                                   ", ".join(list(diff)), target_name, self.name)
        return dict_merge(self.defining_parms, new_parms)

    def customize(self, options, orgsys):
        name = "{" + ", ".join([str(x)+": "+str(y) for x,y in options.items()])+"}"
        del options["Name"]
        return self.__class__(self.merge_defparms(name, options), name, orgsys)

    def make_element_list(self, items):
        return [ElementTemplate(item, self.channel, self.parameters) for item in items]
    def substitute(self, items, tick, cdata):
        return [item.substitute(tick, cdata) for item in items]

    class BaseControl(object):
        def __init__(self, cclass, cdata, name):
            self.name = name
            self.cclass = cclass
            self.cdata = cdata
        def __repr__(self):
            return "<" + self.__class__.__name__ + ' "' + self.name + '" at ' + hex(id(self)) + '>'

    def Control(self, cdata, name):
        if isinstance(cdata, int):
            cdata = [cdata]
            if len(cdata) < len(self.parameters):
                # this is nowhere near good enough.
                cdata = [self.parameter_defaults[0]] + cdata
        return self.control_class(self, cdata, name)


class SingleActionModel(BaseModel):
    YAMLKey = "SingleActionModels"
    def __init__(self, ydict, name, orgsys):
        BaseModel.__init__(self, ydict, name, orgsys)
        self.actor = self.make_element_list(self.defining_parms["Act"])
        self.control_class = self.SingleActionControl

    class SingleActionControl(BaseModel.BaseControl):
        def act (self, tick):
            return self.cclass.substitute(self.cclass.actor, tick, self.cdata)


class ReversibleModel(BaseModel):
    YAMLKey = "ReversibleModels"
    def __init__(self, ydict, name, orgsys):
        BaseModel.__init__(self, ydict, name, orgsys)
        ydict = self.defining_parms
        self.actors = [self.make_element_list(ydict[False]), self.make_element_list(ydict[True])]
        self.control_class = self.ReversibleControl
        
    def directed_act(self, tick, cdata, onoff):
        return self.substitute(self.actors[int (not not onoff)], tick, cdata)

    def __repr__(self):
        return "<" + self.__class__.__name__ + " \"" + self.name + "\" at " + hex(id(self)) + ">"

    class ReversibleControl(BaseModel.BaseControl):
        def on (self, tick):
            return self.directed_act(tick, self.cdata, True)
        def off (self, tick):
            return self.directed_act(tick, self.cdata, False)
        def directed_act(self, tick, onoff):
            return self.cclass.directed_act(tick, self.cdata, onoff)


class OrganSystem(object):
    @staticmethod
    def FindAndCreate(name, alt_model=None):
        path = ConfigMan.find_orgsys_definition(name)
        print_ (name + " system definition file " + path)
        y = yaml.load(open(path, "rb"), Loader=DCSafeLoader)
        if name != y["ShortName"]:
            raise OrganSystemDefinitionError("File %s does not define organ system \"%s\" as expected.", path, name)
        return OrganSystem(y, alt_model)

    def get_defined_model(self, name):
        if name not in self.Models:
            raise Orgdef2Error("Stop model \"%s\" not known.", name)
        return self.Models[name]

    def get_model(self, name_or_dict):
        assert name_or_dict,"Null model name/dict provided"
        if isinstance(name_or_dict, dict):
            if "Name" not in name_or_dict:
                raise Orgdef2Error("Stop model options do not include \"Name\".")
            model = self.get_defined_model(name_or_dict["Name"])
            return model.customize(name_or_dict, self)
        return self.get_defined_model(name_or_dict)

    def def_models(self, defs, cls):
        unlinearized = set(defs.keys())
        linearized = []
        while unlinearized:
            for name in unlinearized:
                defn = defs[name]
                if REFK not in defn:
                    linearized.append(name)
                    break
                ref = defn[REFK]
                if ref not in defs:
                    raise OrganSystemDefinitionError("Unknown \"%s\" in model: %s", REFK, ref)
                if ref in linearized:
                    linearized.append(name)
                    break
            else:
                raise OrganSystemDefinitionError("Circularity in \"%s\" chain.", REFK)
            unlinearized.remove(linearized[-1])
        for name in linearized:
            self.Models[name] = cls(defs[name], name, self)

    def register_basic_models(self, yaml_data):
        self.Models = {}
        for model_class in _VisibleControlModelClasses:
            key = model_class.YAMLKey
            if key in yaml_data:
                self.def_models(yaml_data[key], model_class)

    def define_syswide_controls(self, control_specs):
        self.Controls = {}
        for group in control_specs:
            clazz = self.get_model(group["Class"])
            for (name, data) in group.items():
                if name != "Class":
                    self.Controls[name] = clazz.Control(data, name)

    def __init__(self, y, alt_model):
        self.long_name = y["LongName"]
        self.short_name = y["ShortName"]
        self.Tests = y.get("Tests", [ ])
        self.Options = y.get("Options", [ ])
        self.register_basic_models(y)
        self.define_syswide_controls(y.get("Controls", {}).values())
        self.DefaultStopModel = self.get_model(alt_model or y["DefaultStopModel"])

    def ActDefaultStopModel(self, tick, data, onoff):
        return self.DefaultStopModel.directed_act(tick, data, onoff)

    def ActSystemReversible(self, tick, name, onoff):
        return self.Controls[name].directed_act(tick, onoff)

    def default_default_p1(self):
        return self.DefaultStopModel.default_p1

    def ActSystemSingleAction(self, tick, name):
        return self.Controls[name].act(tick)

    def needs_prologue(self):
        return "Prologue" in self.Options

    def needs_track_merge(self):
        return "TrackMerge" in self.Options

    def isControlKnown(self, name, reversible):
        ctl = self.Controls.get(name, False)
        return ctl and (reversible == isinstance(ctl, ReversibleModel.ReversibleControl))

    def __repr__(self):
        return "<" + self.__class__.__name__ + " \"" + self.short_name + "\" at " + hex(id(self)) + ">"


def _test_orgsys(osd):
    test_map = {"Control": osd.isControlKnown, "Reversible": osd.ActSystemReversible,
                "Stop": osd.ActDefaultStopModel, "Single": osd.ActSystemSingleAction}
    print_("Defined", osd.short_name, "tests.")
    for test in osd.Tests:
        test_type, test_parms = test[0], test[1:]
        print_("TEST:", test_type, test_parms)
        if test_type == "Control":
            assert osd.isControlKnown(*test_parms)
        fcn = test_map[test_type] #let it blow up if not
        print_(fcn(*test_parms))


def _test(name):
    osd = OrganSystem.FindAndCreate(name)
    print_ ("Orgsys def:", osd)
    print_ ("Default stop model:", osd.DefaultStopModel)
    print_ ("Deft Stop Model actors[1]:", osd.DefaultStopModel.actors[1])
    print_ ("Create DSM Reversible:", osd.DefaultStopModel.ReversibleControl(osd.DefaultStopModel, [1,2,3], "Zungen ab"))
    print_ ("Default P1:", osd.DefaultStopModel.default_p1)
    if osd.Tests:
        _test_orgsys(osd)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        _test("Hauptwerk")
    else:
        _test(sys.argv[1])
