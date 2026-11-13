"""Default playback device control (Windows Core Audio via ctypes).

Used so "Speaker" can hand the PC's audio to the phone *and* leave the PC quiet:
we switch the default render endpoint to VB-Cable, then restore whatever was
default before. No third-party tools, no PowerShell modules.

Two COM interfaces, hand-rolled:
  * IMMDeviceEnumerator::GetDefaultAudioEndpoint / GetDevice  -> endpoint id
  * IPolicyConfig::SetDefaultEndpoint                         -> change it

Endpoint ids look like ``{0.0.0.00000000}.{guid}``; names come from the MMDevices
registry key, which is enough for the UI.
"""

from __future__ import annotations

import ctypes
from ctypes import HRESULT, POINTER, byref, c_int, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD

import winreg

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
CLSID_PolicyConfigClient = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"

IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IPolicyConfig = "{F8679F50-850A-41CF-9C72-430F290290C8}"

eRender = 0
eConsole, eMultimedia, eCommunications = 0, 1, 2

MMDEVICES_RENDER = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
PKEY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
PKEY_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(c_wchar_p(text), byref(self))


def _co_init():
    ole32 = ctypes.oledll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    try:
        ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED
    except OSError:
        pass                             # already initialised on this thread


def _create(clsid: str, iid: str) -> c_void_p:
    """CoCreateInstance -> interface pointer (null on failure)."""
    ole32 = ctypes.oledll.ole32
    ole32.CLSIDFromString.argtypes = [c_wchar_p, POINTER(GUID)]
    ole32.CLSIDFromString.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, DWORD,
                                       POINTER(GUID), POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    ptr = c_void_p()
    try:
        ole32.CoCreateInstance(byref(GUID(clsid)), None, 1,
                               byref(GUID(iid)), byref(ptr))
    except OSError:
        return c_void_p()
    return ptr


def _call(ptr: c_void_p, index: int, restype, *argtypes):
    """Fetch vtable slot `index` and bind it."""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])
    return fn


# --------------------------------------------------------------------- names
def _device_name(guid: str) -> str:
    for pkey, fallback in ((PKEY_NAME, None), (PKEY_DESC, "audio device")):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                f"{MMDEVICES_RENDER}\\{{{guid}}}\\Properties") as k:
                return str(winreg.QueryValueEx(k, pkey)[0])
        except OSError:
            continue
    return fallback or "audio device"


def render_devices() -> list[dict]:
    """Every active playback endpoint: {'id', 'guid', 'name'}."""
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MMDEVICES_RENDER)
    except OSError:
        return out
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                guid = winreg.EnumKey(root, i)
            except OSError:
                continue
            if not guid.startswith("{"):
                continue
            try:
                with winreg.OpenKey(root, f"{guid}\\Properties") as pk:
                    state = 1
                    try:
                        state = winreg.QueryValueEx(
                            winreg.OpenKey(root, guid), "DeviceState")[0]
                    except OSError:
                        pass
                    if state == 0:          # not present
                        continue
                    name = str(winreg.QueryValueEx(pk, PKEY_NAME)[0])
            except OSError:
                continue
            out.append({"id": "{0.0.0.00000000}.%s" % guid, "guid": guid, "name": name})
    return out


# --------------------------------------------------------------- enumerator
def default_render_id() -> str | None:
    """Endpoint id of the current default playback device, or None."""
    try:
        _co_init()
        enum = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        if not enum:
            return None
        try:
            get_default = _call(enum, 4, HRESULT, c_int, c_int, POINTER(c_void_p))
            dev = c_void_p()
            if get_default(enum, eRender, eConsole, byref(dev)) != 0 or not dev:
                return None
            try:
                get_id = _call(dev, 5, HRESULT, POINTER(c_wchar_p))
                sid = c_wchar_p()
                if get_id(dev, byref(sid)) != 0 or not sid.value:
                    return None
                return sid.value
            finally:
                _call(dev, 2, ctypes.c_ulong)(dev)      # Release
        finally:
            _call(enum, 2, ctypes.c_ulong)(enum)
    except OSError:
        return None


def default_render_name() -> str | None:
    sid = default_render_id()
    if not sid:
        return None
    for dev in render_devices():
        if dev["id"].lower() == sid.lower():
            return dev["name"]
    return None


# ------------------------------------------------------------ policy config
def set_default_render(endpoint_id: str, all_roles: bool = True) -> bool:
    """Point the default playback device at `endpoint_id`."""
    try:
        _co_init()
        policy = _create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
        if not policy:
            return False
        try:
            # IPolicyConfig vtable: 0..2 IUnknown, 3 GetMixFormat, 4 GetDeviceFormat,
            # 5 ResetDeviceFormat, 6 SetDeviceFormat, 7 GetProcessingPeriod,
            # 8 SetProcessingPeriod, 9 GetShareMode, 10 SetShareMode,
            # 11 GetPropertyValue, 12 SetPropertyValue,
            # 13 SetDefaultEndpoint, 14 SetEndpointVisibility
            set_default = _call(policy, 13, HRESULT, c_wchar_p, c_int)
            roles = (eConsole, eMultimedia, eCommunications) if all_roles else (eConsole,)
            ok = False
            for role in roles:
                if set_default(policy, endpoint_id, role) == 0:
                    ok = True
            return ok
        finally:
            _call(policy, 2, ctypes.c_ulong)(policy)
    except OSError:
        return False


def find_by_name(fragment: str) -> dict | None:
    """First playback endpoint whose name contains `fragment` (case-insensitive)."""
    frag = fragment.lower()
    for dev in render_devices():
        if frag in dev["name"].lower():
            return dev
    return None
