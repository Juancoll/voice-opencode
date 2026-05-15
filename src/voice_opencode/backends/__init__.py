"""``backends`` package: concrete platform implementations.

Sub-packages are named ``<os>_<system>`` so the layout is greppable
and unambiguous (``linux_hyprland``, ``linux_x11``, ``windows_uia``).
Consumers MUST NOT import from here directly — go through
``voice_opencode.platform`` instead.
"""
