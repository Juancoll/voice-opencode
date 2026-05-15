"""linux_input backend package.

Synthesises keyboard and pointer events on Linux, regardless of display
server. The default implementation (``YdotoolInputBackend``) prefers
``wtype`` for keyboard (cleaner on Wayland) and uses ``ydotool`` for
mouse events.
"""
