def classFactory(iface):
    from .radii_plugin import RadiiPlugin
    return RadiiPlugin(iface)
