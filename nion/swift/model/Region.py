"""
    Contains classes related to regions of data items.
"""

# futures
from __future__ import absolute_import

# standard libraries
import math

# third party libraries
# None

# local libraries
from nion.swift.model import Graphics
from nion.ui import Binding
from nion.ui import Event
from nion.ui import Geometry
from nion.ui import Observable
from nion.ui import Persistence


class Region(Observable.Observable, Observable.Broadcaster, Persistence.PersistentObject):
    """An object to specify a region and its display properties on a data item.

    Regions are associated with exactly one data item.

    Subclasses can append closable items _close_stack and they will be automatically closed when the Region is closed.
    """

    def __init__(self, type):
        super(Region, self).__init__()
        self.define_type(type)
        self.define_property("label", changed=self._property_changed, validate=lambda s: str(s) if s else None)
        self.define_property("is_position_locked", False, changed=self._property_changed)
        self.define_property("is_shape_locked", False, changed=self._property_changed)
        self.define_property("is_bounds_constrained", False, changed=self._property_changed)
        self.remove_region_because_graphic_removed_event = Event.Event()
        self._about_to_be_removed = False
        self._closed = False
        self._about_to_be_removed_stack = list()
        self._close_stack = list()
        # TODO: add unit type to region (relative, absolute, calibrated)

    def close(self):
        for close_item in reversed(self._close_stack):
            close_item.close()
        assert self._about_to_be_removed
        assert not self._closed
        self._closed = True

    def about_to_be_removed(self):
        # called before close and before item is removed from its container
        for remove_item in reversed(self._about_to_be_removed_stack):
            remove_item.about_to_be_removed()
        assert not self._about_to_be_removed
        self._about_to_be_removed = True

    def _property_changed(self, name, value):
        self.notify_set_property(name, value)

    @property
    def graphic(self):
        return None

    def remove_region_graphic(self):
        # message from the graphic when its being removed
        self.remove_region_because_graphic_removed_event.fire()


class PointRegion(Region):

    def __init__(self):
        super(PointRegion, self).__init__("point-region")
        self.define_property("position", (0.5, 0.5), changed=self._property_changed, validate=lambda s: tuple(s))
        self.__graphic = Graphics.PointGraphic()
        self.__graphic.set_region(self)
        self.__graphic.color = "#F80"
        self._about_to_be_removed_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic.remove_region_graphic_event.listen(self.remove_region_graphic))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "position", self.__graphic, "position"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "label", self.__graphic, "label"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_position_locked", self.__graphic, "is_position_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_shape_locked", self.__graphic, "is_shape_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_bounds_constrained", self.__graphic, "is_bounds_constrained"))

    @property
    def graphic(self):
        return self.__graphic

    @property
    def _position(self):
        return Geometry.FloatPoint.make(self.position)

    @_position.setter
    def _position(self, value):
        self.position = value


class LineRegion(Region):

    def __init__(self):
        super(LineRegion, self).__init__("line-region")
        def read_vector(persistent_property, properties):
            # read the vector defined by persistent_property from the properties dict.
            start = properties.get("start", (0.0, 0.0))
            end = properties.get("end", (1.0, 1.0))
            return start, end
        def write_vector(persistent_property, properties, value):
            # write the vector (value) defined by persistent_property to the properties dict.
            properties["start"] = value[0]
            properties["end"] = value[1]
        # vector is stored in image normalized coordinates
        self.define_property("vector", ((0.0, 0.0), (1.0, 1.0)), changed=self.__vector_changed, reader=read_vector, writer=write_vector, validate=lambda s: (tuple(s[0]), tuple(s[1])))
        self.define_property("width", 1.0, changed=self._property_changed, validate=lambda s: float(s))
        self.define_property("interval_descriptors", list(), changed=self._property_changed)
        self.__graphic = Graphics.LineProfileGraphic()
        self.__graphic.set_region(self)
        self.__graphic.color = "#F80"
        self.__graphic.end_arrow_enabled = True
        self._about_to_be_removed_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic.remove_region_graphic_event.listen(self.remove_region_graphic))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "vector", self.__graphic, "vector"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "width", self.__graphic, "width"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "label", self.__graphic, "label"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "interval_descriptors", self.__graphic, "interval_descriptors"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_position_locked", self.__graphic, "is_position_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_shape_locked", self.__graphic, "is_shape_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_bounds_constrained", self.__graphic, "is_bounds_constrained"))

    def __vector_changed(self, name, value):
        self._property_changed(name, value)
        self.notify_set_property("start", value[0])
        self.notify_set_property("end", value[1])
        self.notify_set_property("length", self.length)
        self.notify_set_property("angle", self.angle)

    @property
    def start(self):
        return self.vector[0]

    @start.setter
    def start(self, value):
        self.vector = value, self.end

    @property
    def end(self):
        return self.vector[1]

    @end.setter
    def end(self, value):
        self.vector = self.start, value

    @property
    def _start(self):
        return Geometry.FloatPoint.make(self.start)

    @_start.setter
    def _start(self, value):
        self.start = value

    @property
    def _end(self):
        return Geometry.FloatPoint.make(self.end)

    @_end.setter
    def _end(self, value):
        self.end = value

    @property
    def length(self):
        return Geometry.distance(self.start, self.end)

    @length.setter
    def length(self, value):
        angle = self.angle
        self.end = self._start + value * Geometry.FloatSize(height=-math.sin(angle), width=math.cos(angle))

    @property
    def angle(self):
        delta = self._end - self._start
        return -math.atan2(delta.y, delta.x)

    @angle.setter
    def angle(self, value):
        self.end = self._start + self.length * Geometry.FloatSize(height=-math.sin(value), width=math.cos(value))

    @property
    def graphic(self):
        return self.__graphic


class RectRegion(Region):

    def __init__(self):
        super(RectRegion, self).__init__("rectangle-region")
        self.define_property("center", (0.5, 0.5), changed=self._property_changed, validate=lambda s: tuple(s))
        self.define_property("size", (1.0, 1.0), changed=self._property_changed, validate=lambda s: tuple(s))
        # TODO: add rotation property to rect region
        self.__graphic = Graphics.RectangleGraphic()
        self.__graphic.set_region(self)
        self.__graphic.color = "#F80"
        self._about_to_be_removed_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic.remove_region_graphic_event.listen(self.remove_region_graphic))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "center", self.__graphic, "center"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "size", self.__graphic, "size"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "label", self.__graphic, "label"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_position_locked", self.__graphic, "is_position_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_shape_locked", self.__graphic, "is_shape_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_bounds_constrained", self.__graphic, "is_bounds_constrained"))

    @property
    def graphic(self):
        return self.__graphic

    @property
    def bounds(self):
        center = self.center
        size = self.size
        return (center[0] - size[0] * 0.5, center[1] - size[1] * 0.5), size

    @bounds.setter
    def bounds(self, bounds):
        self.center = bounds[0][0] + bounds[1][0] * 0.5, bounds[0][1] + bounds[1][1] * 0.5
        self.size = bounds[1]

    @property
    def _bounds(self):  # useful for testing
        center = self.center
        size = self.size
        return Geometry.FloatRect(origin=(center[0] - size[0] * 0.5, center[1] - size[1] * 0.5), size=size)

    @_bounds.setter
    def _bounds(self, bounds):
        self.center = bounds[0][0] + bounds[1][0] * 0.5, bounds[0][1] + bounds[1][1] * 0.5
        self.size = bounds[1]

    def _property_changed(self, name, value):
        # override to implement dependency. argh.
        self.notify_set_property(name, value)
        self.notify_set_property("bounds", self.bounds)


class EllipseRegion(Region):

    def __init__(self):
        super(EllipseRegion, self).__init__("ellipse-region")
        self.define_property("center", (0.0, 0.0), changed=self._property_changed, validate=lambda s: tuple(s))
        self.define_property("size", (1.0, 1.0), changed=self._property_changed, validate=lambda s: tuple(s))
        self.define_property("angle", 0.0, changed=self._property_changed, validate=lambda s: float(s))
        self.__graphic = Graphics.EllipseGraphic()
        self.__graphic.set_region(self)
        self.__graphic.color = "#F80"
        self._about_to_be_removed_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic.remove_region_graphic_event.listen(self.remove_region_graphic))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "center", self.__graphic, "center"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "size", self.__graphic, "size"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "label", self.__graphic, "label"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_position_locked", self.__graphic, "is_position_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_shape_locked", self.__graphic, "is_shape_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_bounds_constrained", self.__graphic, "is_bounds_constrained"))

    @property
    def graphic(self):
        return self.__graphic

    def __get_bounds(self):
        center = self.center
        size = self.size
        return (center[0] - size[0] * 0.5, center[1] - size[1] * 0.5), size
    def __set_bounds(self, bounds):
        self.center = bounds[0][0] + bounds[1][0] * 0.5, bounds[0][1] + bounds[1][1] * 0.5
        self.size = bounds[1]
    bounds = property(__get_bounds, __set_bounds)

    def _property_changed(self, name, value):
        # override to implement dependency. argh.
        self.notify_set_property(name, value)
        self.notify_set_property("bounds", self.bounds)


class IntervalRegion(Region):

    def __init__(self):
        super(IntervalRegion, self).__init__("interval-region")
        def read_interval(persistent_property, properties):
            # read the interval defined by persistent_property from the properties dict.
            start = properties.get("start", 0.0)
            end = properties.get("end", 1.0)
            return start, end
        def write_interval(persistent_property, properties, value):
            # write the interval (value) defined by persistent_property to the properties dict.
            properties["start"] = value[0]
            properties["end"] = value[1]
        self.define_property("interval", (0.0, 1.0), changed=self.__interval_changed, reader=read_interval, writer=write_interval, validate=lambda s: tuple(s))
        self.__graphic = Graphics.IntervalGraphic()
        self.__graphic.set_region(self)
        self.__graphic.color = "#F80"
        self._about_to_be_removed_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic)
        self._close_stack.append(self.__graphic.remove_region_graphic_event.listen(self.remove_region_graphic))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "interval", self.__graphic, "interval"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "label", self.__graphic, "label"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_position_locked", self.__graphic, "is_position_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_shape_locked", self.__graphic, "is_shape_locked"))
        self._close_stack.append(RegionPropertyToGraphicBinding(self, "is_bounds_constrained", self.__graphic, "is_bounds_constrained"))

    @property
    def graphic(self):
        return self.__graphic

    def __interval_changed(self, name, value):
        self._property_changed(name, value)
        self.notify_set_property("start", value[0])
        self.notify_set_property("end", value[1])

    def __get_start(self):
        return self.interval[0]
    def __set_start(self, start):
        self.interval = start, self.end
    start = property(__get_start, __set_start)

    def __get_end(self):
        return self.interval[1]
    def __set_end(self, end):
        self.interval = self.start, end
    end = property(__get_end, __set_end)




class RegionPropertyToGraphicBinding(Binding.PropertyBinding):

    """
        Binds a property of an operation item to a property of a graphic item.
    """

    def __init__(self, region, region_property_name, graphic, graphic_property_name):
        super(RegionPropertyToGraphicBinding, self).__init__(region, region_property_name)
        self.__graphic = graphic
        self.__graphic_property_changed_listener = graphic.property_changed_event.listen(self.__property_changed)
        self.__graphic_property_name = graphic_property_name
        self.__region_property_name = region_property_name
        self.target_setter = lambda value: setattr(self.__graphic, graphic_property_name, value)

    def close(self):
        self.__graphic_property_changed_listener.close()
        self.__graphic_property_changed_listener = None
        self.__graphic = None
        super(RegionPropertyToGraphicBinding, self).close()

    # watch for property changes on the graphic.
    def __property_changed(self, property_name, property_value):
        if property_name == self.__graphic_property_name:
            old_property_value = getattr(self.source, self.__region_property_name)
            # to prevent message loops, check to make sure it changed
            if property_value != old_property_value:
                self.update_source(property_value)


def region_factory(lookup_id):
    build_map = {
        "point-region": PointRegion,
        "line-region": LineRegion,
        "rectangle-region": RectRegion,
        "ellipse-region": EllipseRegion,
        "interval-region": IntervalRegion,
    }
    type = lookup_id("type")
    return build_map[type]() if type in build_map else None