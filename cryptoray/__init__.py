bl_info = {
    "name": "CryptoRay",
    "author": "CryptoRay contributors",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > CryptoRay",
    "description": "Cryptomatte for objects seen through refractive glass, via Cycles light groups",
    "category": "Render",
    "doc_url": "https://github.com/adamykchan/cryptoRay_blender",
}

if "bpy" in locals():  # support "Reload Scripts"
    import importlib
    for _m in (core, build, render, props, ops, ui, translations):  # noqa: F821
        importlib.reload(_m)

import bpy  # noqa: E402

from . import core, build, render, props, ops, ui, translations  # noqa: E402,F401

_modules = (translations, props, ops, ui, render)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
