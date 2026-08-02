# AlbertonIntrospect — Phase 0 tooling for Alberton MCP for Live.
#
# Stable loader: executes impl.py from disk on every instantiation, so the
# introspection code can be edited and re-run by toggling the Control Surface
# (Preferences > Link, Tempo & MIDI) to "None" and back — no Live restart.
# Keep this file frozen; iterate on impl.py.

import os
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CRASH_LOG = os.path.join(_SCRIPT_DIR, "crash.log")


class _InertSurface(object):
    """Fallback surface returned when impl.py fails, so Live keeps running."""

    def __init__(self, c_instance):
        self._c_instance = c_instance

    def disconnect(self):
        pass

    def can_lock_to_devices(self):
        return False

    def suggest_input_port(self):
        return ""

    def suggest_output_port(self):
        return ""

    def suggest_map_mode(self, *args):
        return 0

    def suggest_needs_takeover(self, *args):
        return True

    def supports_pad_translation(self):
        return False

    def set_pad_translations(self, *args):
        pass

    def receive_midi(self, midi_bytes):
        pass

    def build_midi_map(self, midi_map_handle):
        pass

    def update_display(self):
        pass

    def refresh_state(self):
        pass

    def connect_script_instances(self, instantiated_scripts):
        pass


def create_instance(c_instance):
    impl_path = os.path.join(_SCRIPT_DIR, "impl.py")
    try:
        with open(impl_path, "r") as fh:
            source = fh.read()
        namespace = {"__file__": impl_path, "__name__": "AlbertonIntrospect_impl"}
        exec(compile(source, impl_path, "exec"), namespace)
        return namespace["build"](c_instance)
    except Exception:
        try:
            with open(_CRASH_LOG, "a") as fh:
                fh.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        try:
            c_instance.log_message("AlbertonIntrospect failed to load; see " + _CRASH_LOG)
        except Exception:
            pass
        return _InertSurface(c_instance)
