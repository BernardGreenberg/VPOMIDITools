#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import six
from six import print_
from operator import attrgetter, itemgetter
from MidiTimeModel import MeasureBeat
from organ import RegError, RegErrorPt
from reg_events import RegEventStack, StopEvent, RoutingEvent, ExpressionEvent

EXPRESSION_EXPRESSIONS = {"open": 127, "closed":0}

def commalist(stuff):
    if stuff is None or isinstance(stuff, list):
        return stuff
    if isinstance(stuff, int):
        return [stuff]
    assert isinstance(stuff, six.string_types)
    return [x.strip() for x in stuff.split(",")]


class Command(object):
    dict = {}
    def __init__(self, name, arrayarg=False, order=10):
        self.name = name
        self.prio = order
        self.arrayarg = arrayarg
        self.__class__.dict[name] = self
    def __call__(self, method):
        self.method = method
        return method
    def __str__(self):
        return "<Command %s prio=%d, arrayarg=%s>" % (self.name, self.prio, self.arrayarg)


class RegCompiler:
    def __init__(self, args):
        self.opts = args

    @Command("Combination",order=4)
    def compile_combination_application(self, point, division, args):
        if len(args) != 1:
            raise RegErrorPt(point, "Invalid combination reference %s", args)
        combname = args[0]
        dname = division.main_name
        try:
            newbag = self.combinations[dname][combname]
        except KeyError:
            raise RegErrorPt(point, "Comb. %s unknown on %s", combname, dname)
        except TypeError:
            raise RegErrorPt(point, "Invalid combination name: %s", combname)

        curbag = set(filter(attrgetter("cstatus"), division.stops))

        for stop in curbag - newbag:
            if self.opts.kombination:
                print_("%s %s %s removing %s" % (dname, combname, point, stop))
            self.events.add_reg(point, False, stop)
        for stop in newbag - curbag:
            if self.opts.kombination:
                print_("%s %s %s adding %s" % (dname, combname, point, stop))
            self.events.add_reg(point, True, stop)

    def compile_combination_dictionary(self, combinations_spec):
        result = {}
        generals = result["General"] = {"Cancel" : set()}
        for div in self.orgdef.divisions:
            result[div.main_name] = {"Cancel" : set()}
        for (groupname, args) in combinations_spec.items():
            if groupname == "General":
                for (gname, defs) in args.items():
                    bag = set()
                    for (divname, stops) in defs.items():
                        div = self.orgdef.get_division(divname)
                        bag.update(map(div.get_stop, commalist(stops)))
                    generals[gname] = bag
            else:
                div = self.orgdef.get_division(groupname)
                dcombs = result[div.main_name]
                for (cname, stops) in args.items():
                    dcombs[cname] = set(map(div.get_stop, commalist(stops)))
        return result

    def compile_expression_expression(self, exp, point):
        if isinstance(exp, int):
            if exp < 0 or exp > 127:
                raise RegErrorPt(point, "'Expression' expression out of range: %d", exp)
            return exp
        elif exp in EXPRESSION_EXPRESSIONS:
            return EXPRESSION_EXPRESSIONS[exp]
        elif isinstance(exp, six.string_types) and exp.endswith("%"):
            try:
                v = int(int(exp[:-1])*1.27)
                if v < 0 or v > 127:
                    raise RegErrorPt(point, "'Expression' expression out of range: %d (%s)", v, exp)
                return v
            except ValueError:
                pass
        raise RegErrorPt(point, "Invalid 'Expression' expression: %s", exp)

    @Command("Expression")
    def compile_expression_directive(self, point, division, args):
        if division.expression_route is None:
            raise RegErrorPt(point, "%s is not enclosed ('Expression' invalid)", division)
        if len(args) == 1:
            val = self.compile_expression_expression(args[0], point)
            self.events.add_expression(point, division, val)
        elif len(args) == 4:
            (measure, beat) = point
            (start_setting_e, end_setting_e, length_beats, nsteps) = args
            start_setting = self.compile_expression_expression(start_setting_e, point)
            end_setting = self.compile_expression_expression(end_setting_e, point)
            inc_setting = float(end_setting - start_setting)/nsteps
            inc_beat = float(length_beats)/nsteps
            for i in range(nsteps + 1):
                npoint = MeasureBeat(measure, beat + i*inc_beat) # floats understood here
                nvalue = int(start_setting + i*inc_setting)
                self.events.add_expression(npoint, division, nvalue)
        else:
            raise RegErrorPt(point, "Invalid 'Expression' value: %s", args)


    @Command("Add", arrayarg=True)
    def compile_Add(self, point, division, arg):
        self.events.add_reg(point, True, division.get_stop(arg, point))

    @Command("Remove", order=6)
    def compile_Remove(self, point, division, args):
        if args == ["All"]:
            self.compile_combination_application(point, division, ["Cancel"])
        else:
            for arg in args:
                self.events.add_reg(point, False, division.get_stop(arg, point))

    @Command("RouteStaves", arrayarg=True)
    def compile_RouteStaves(self, point, division, arg):
        self.events.add_rte(point, arg, division)

    def order_reg_directions(self, directions):
        new_directions = [ ]
        generals = []
        for (divname, div_commands) in directions.items():
            if divname == "General":
                generals.append(("General", None, div_commands))
            else:
                for (command, args) in div_commands.items():
                    try:
                        prio = Command.dict[command].prio
                    except KeyError:
                        prio = 100    #don't diagnose bogus command here.
                    new_directions.append( (prio, divname, command, args) )
        return generals + list(map(itemgetter(1, 2, 3), sorted(new_directions)))

    def compile_schedule_item(self, point, item):
        (divname, cmd, rawargs) = item
        if divname == "General":
            gdiv = self.orgdef.get_division(divname, point)
            self.compile_combination_application(point, gdiv, [rawargs])
        else:
            if cmd in Command.dict:
                division = self.orgdef.get_division(divname, point)
                handler = Command.dict[cmd]
                args = commalist(rawargs)
                if handler.arrayarg:
                    for arg in args:
                        handler.method(self, point, division, arg)
                else:
                    handler.method(self, point, division, args)
            else:
                raise RegErrorPt(point, "Unknown registration command: %s\nValid commands: %s.",
                                 cmd, ", ".join(Command.dict))
  
    def compile_schedule(self, measures):
        by_time_point = {} #can't be multiples at one point, old or new: def. of "dict"
        for (measure, entry) in measures.items():
            if not isinstance(entry, dict):
                raise RegError("Schedule entry for measure %s is not a mapping.", measure)
            #Auld style -- nested per-beat dicts
            if all((isinstance(key, int) or isinstance(key, float)) for key in entry):
                if not isinstance(measure, int):
                    raise RegError("Integer measure number expected in old-format " \
                                   "schedule entry, not \"%s\".", measure)
                for (beat, directions) in entry.items():
                    by_time_point[MeasureBeat(measure, beat)] = directions
            #Gnew style -- 32+.25 or 32 (+0)
            elif all(isinstance(key, six.string_types) for key in entry):
                try:
                    by_time_point[MeasureBeat.parse(measure)] = entry
                except ValueError:
                    raise RegError ("Invalid m(+b) measure/beat spec: %s", measure)
            else:
                raise RegError ("Unclear schedule format/version at supposed measure %s.", measure)

        for (point,directions) in sorted(by_time_point.items()):
            for item in self.order_reg_directions(directions):
                self.compile_schedule_item(point, item)


    def compile(self, ypiece, orgdef):
        self.orgdef = orgdef

        if "Registration" in ypiece:
            schedule = fabricate_measure_zero(ypiece["Registration"])
        else:
            schedule = ypiece.get("Measures", ypiece.get("Schedule", None))

        self.combinations = self.compile_combination_dictionary(ypiece.get("Combinations",{}))
        self.events = RegEventStack()
        self.compile_schedule(schedule)
        return self.events

def fabricate_measure_zero(registrations):
    #These 2 lines are a "dictionary comprehension" python construct:
    directives = {divname : {"Add" : commalist(stops)}
            for (divname, stops) in registrations.items()}
    return {0 : directives}

def hoist_init_regs(schedule):
    def phil(event):
        return event.tick == 0 and \
          isinstance(event, StopEvent) and \
          event.status

    output = list(filter(phil, schedule))
    for e in output:
        schedule.remove(e)

    return output
