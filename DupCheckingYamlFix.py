#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import yaml
from yaml.reader import Reader
from yaml.scanner import Scanner
from yaml.parser import Parser
from yaml.composer import Composer
from yaml.constructor import SafeConstructor, ConstructorError
from yaml.resolver import Resolver
from yaml.nodes import MappingNode
from six import PY2, PY3
import collections
import typing      #py 3.10, works in 3.9, too

#BSG 4 January 2018, for insreg distro 1.0.10

#Fixes bug whereby YAML fails to diagnose duplicate keys in a mapping.
#Based on https://stackoverflow.com/questions/34358974/how-to-prevent-re-definition-of-keys-in-yaml ,
#showing the basic tricks of "subclassing" YAML ...

#Note that in the current YAML source, SafeConstructor is more basic
#than "Unsafe"Constructor (just plain "Constructor"), which latter
#is based on the former (the "unsafe" frills are now an add-on).
#So we are "Safe" basing on the former.

class DupCheckingSafeConstructor(SafeConstructor):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None, None,
                "expected a mapping node, but found %s" % node.id,
                node.start_mark)
        else:
            self.flatten_mapping(node)

        mapping = {}
        for key_node, value_node in node.value:
            # keys can be list -> deep
            key = self.construct_object(key_node, deep=True)
            # lists are not hashable, but tuples are
#            if not isinstance(key, collections.Hashable):
            if not isinstance(key, typing.Hashable):
                if isinstance(key, list):
                    key = tuple(key)
            if PY2:
                try:
                    hash(key)
                except TypeError as exc:
                    raise ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        "found unacceptable key (%s)" %
                        exc, key_node.start_mark)
            else:
#                if not isinstance(key, collections.Hashable):
                 if not isinstance(key, typing.Hashable):
                    raise ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        "found unhashable key", key_node.start_mark)

            # next two lines differ from original
            if key in mapping:
                raise ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        "Found duplicate key: %s" % key, key_node.start_mark)
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


#See yaml's __init__.py
class DCSafeLoader(Reader, Scanner, Parser, Composer, DupCheckingSafeConstructor, Resolver):
    def __init__(self, stream):
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        DupCheckingSafeConstructor.__init__(self)
        Resolver.__init__(self)

