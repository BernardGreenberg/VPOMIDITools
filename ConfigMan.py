#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

# "man" is old Multics-talk for "manager"

import os
import sys
import yaml

from BMTError import BMTError

class ConfigurationError(BMTError):
    String = "Configuration"

def CError(prefix, name):
    raise ConfigurationError("%s \"%s\" not found in configured paths.", prefix, name)

config_dir = os.path.split(__file__)[0]
config_path = os.path.join(config_dir, "MIDIAppConfig")

if os.path.isfile(config_path):
    ConfigDictionary = yaml.safe_load(open(config_path, "rb"))
else:
    ConfigDictionary = {}

PY2 = sys.version_info.major == 2
PY3 = sys.version_info.major == 3
assert PY2 or PY3
assert "midi" not in sys.modules,"ConfigMan: should not be midi loaded already"


if "MIDIPackage" in ConfigDictionary:
    path = os.path.expanduser(ConfigDictionary["MIDIPackage"])
    fpath = os.path.join(path, "__init__.py")
    if PY2:
        import imp
        midi = imp.load_source('midi', fpath)
    elif PY3:
        import importlib.util
        spec = importlib.util.spec_from_file_location("midi", fpath)
        ourmidi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ourmidi)
        sys.modules["midi"] = ourmidi  # Why does all that bullshit not do this!?!
        import midi
        assert midi == ourmidi
else:
    import midi

#sys.stdout.write("Midi package from " + midi.__file__ + "\n")


def getMidi():
    return midi

def _find_in_maybe_list(fname, key):
    assert fname,"'None' given as name to ConfigMan"
    dentry = ConfigDictionary.get(key, ["."])
    if isinstance(dentry, list):
        paths = dentry
    else:
        paths = [dentry]
    for path in paths:
        if path == ".":
            path = config_dir
        elif path.startswith("./"):
            path = config_dir + path[1:]
        #need .. handling, too
        candidate = os.path.expanduser(os.path.join(path, fname))
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    else:
        return None

def find_organ_definition(organ_name, suffix=".orgdef"):
    fname = organ_name + suffix
    value = _find_in_maybe_list(fname, "OrgansPaths")
    if not value:
        CError("Organ definition for", organ_name)
    return value
        
def find_orgdef_auxl(fname):
    value = _find_in_maybe_list(fname, "OrgansPaths")
    if not value:
        CError("Organ auxiliary file", fname)
    return value

def find_orgsys_definition(orgsys_name, suffix=".orgsysdef"):
    fname = orgsys_name + suffix
    value = _find_in_maybe_list(fname, "OrgansPaths")
    if not value:
        CError("Organ system definition for", orgsys_name)
    return value



def find_app_auxiliary(fname):
    value = _find_in_maybe_list(fname, "AppAuxiliaries")
    if not value:
        CError("App auxiliary file", fname)
    return value
