# standard libraries
import copy
import datetime
import functools
import gettext
import numbers
import operator
import threading
import time
import typing
import uuid
import weakref

# local libraries
from nion.data import DataAndMetadata
from nion.swift.model import Cache
from nion.swift.model import DataItem
from nion.swift.model import Display
from nion.swift.model import Graphics
from nion.swift.model import Utility
from nion.utils import Event
from nion.utils import Observable
from nion.utils import Persistence


_ = gettext.gettext


class GraphicSelection:
    def __init__(self, indexes=None, anchor_index=None):
        super().__init__()
        self.__changed_event = Event.Event()
        self.__indexes = copy.copy(indexes) if indexes else set()
        self.__anchor_index = anchor_index

    def __copy__(self):
        return type(self)(self.__indexes, self.__anchor_index)

    def __eq__(self, other):
        return other is not None and self.indexes == other.indexes and self.anchor_index == other.anchor_index

    def __ne__(self, other):
        return other is None or self.indexes != other.indexes or self.anchor_index != other.anchor_index

    @property
    def changed_event(self):
        return self.__changed_event

    @property
    def current_index(self):
        if len(self.__indexes) == 1:
            for index in self.__indexes:
                return index
        return None

    @property
    def anchor_index(self):
        return self.__anchor_index

    @property
    def has_selection(self):
        return len(self.__indexes) > 0

    def contains(self, index):
        return index in self.__indexes

    @property
    def indexes(self):
        return self.__indexes

    def clear(self):
        old_index = self.__indexes.copy()
        self.__indexes = set()
        self.__anchor_index = None
        if old_index != self.__indexes:
            self.__changed_event.fire()

    def __update_anchor_index(self):
        for index in self.__indexes:
            if self.__anchor_index is None or index < self.__anchor_index:
                self.__anchor_index = index

    def add(self, index):
        assert isinstance(index, numbers.Integral)
        old_index = self.__indexes.copy()
        self.__indexes.add(index)
        if len(old_index) == 0:
            self.__anchor_index = index
        if old_index != self.__indexes:
            self.__changed_event.fire()

    def remove(self, index):
        assert isinstance(index, numbers.Integral)
        old_index = self.__indexes.copy()
        self.__indexes.remove(index)
        if not self.__anchor_index in self.__indexes:
            self.__update_anchor_index()
        if old_index != self.__indexes:
            self.__changed_event.fire()

    def add_range(self, range):
        for index in range:
            self.add(index)

    def set(self, index):
        assert isinstance(index, numbers.Integral)
        old_index = self.__indexes.copy()
        self.__indexes = set()
        self.__indexes.add(index)
        self.__anchor_index = index
        if old_index != self.__indexes:
            self.__changed_event.fire()

    def toggle(self, index):
        assert isinstance(index, numbers.Integral)
        if index in self.__indexes:
            self.remove(index)
        else:
            self.add(index)

    def insert_index(self, new_index):
        new_indexes = set()
        for index in self.__indexes:
            if index < new_index:
                new_indexes.add(index)
            else:
                new_indexes.add(index + 1)
        if self.__anchor_index is not None:
            if new_index <= self.__anchor_index:
                self.__anchor_index += 1
        if self.__indexes != new_indexes:
            self.__indexes = new_indexes
            self.changed_event.fire()

    def remove_index(self, remove_index):
        new_indexes = set()
        for index in self.__indexes:
            if index != remove_index:
                if index > remove_index:
                    new_indexes.add(index - 1)
                else:
                    new_indexes.add(index)
        if self.__anchor_index is not None:
            if remove_index == self.__anchor_index:
                self.__update_anchor_index()
            elif remove_index < self.__anchor_index:
                self.__anchor_index -= 1
        if self.__indexes != new_indexes:
            self.__indexes = new_indexes
            self.changed_event.fire()


class DisplayDataChannel:
    def __init__(self, data_item: DataItem.DataItem, display: Display.Display):
        self.__data_item = data_item
        self.__display = display
        self.modified_state = 0
        self.property_changed_event = Event.Event()
        self.display_values_changed_event = Event.Event()
        self.display_data_will_change_event = Event.Event()

        def property_changed(property_name):
            self.modified_state += 1
            self.property_changed_event.fire(property_name)

        self.__data_item_property_changed_event_listener = None
        self.__display_property_changed_event_listener = None
        self.__display_values_changed_event_listener = None
        self.__display_data_will_change_event_listener = None

        if self.__data_item:
            self.__data_item_property_changed_event_listener = self.__data_item.property_changed_event.listen(property_changed)
        if self.__display:
            self.__display_property_changed_event_listener = self.__display.property_changed_event.listen(property_changed)
            self.__display_values_changed_event_listener = self.__display.display_values_changed_event.listen(self.display_values_changed_event.fire)
            self.__display_data_will_change_event_listener = self.__display.display_data_will_change_event.listen(self.display_data_will_change_event.fire)

    def close(self):
        if self.__data_item_property_changed_event_listener:
            self.__data_item_property_changed_event_listener.close()
            self.__data_item_property_changed_event_listener = None
        if self.__display_property_changed_event_listener:
            self.__display_property_changed_event_listener.close()
            self.__display_property_changed_event_listener = None
        if self.__display_values_changed_event_listener:
            self.__display_values_changed_event_listener.close()
            self.__display_values_changed_event_listener = None
        if self.__display_data_will_change_event_listener:
            self.__display_data_will_change_event_listener.close()
            self.__display_data_will_change_event_listener = None

    @property
    def data_item(self) -> DataItem.DataItem:
        return self.__data_item

    @property
    def created_local_as_string(self) -> str:
        return self.__data_item.created_local_as_string

    @property
    def size_and_data_format_as_string(self) -> str:
        return self.__data_item.size_and_data_format_as_string

    @property
    def uuid(self) -> uuid.UUID:
        return self.__display.uuid

    @property
    def collection_index(self):
        return self.__display.collection_index

    @collection_index.setter
    def collection_index(self, value):
        self.__display.collection_index = value

    @property
    def color_map_data(self):
        return self.__display.color_map_data

    @property
    def color_map_id(self):
        return self.__display.color_map_id

    @color_map_id.setter
    def color_map_id(self, value):
        self.__display.color_map_id = value

    @property
    def complex_display_type(self):
        return self.__display.complex_display_type

    @complex_display_type.setter
    def complex_display_type(self, value):
        self.__display.complex_display_type = value

    @property
    def display_data_shape(self):
        return self.__display.display_data_shape

    @property
    def display_limits(self):
        return self.__display.display_limits

    @display_limits.setter
    def display_limits(self, value):
        self.__display.display_limits = value

    @property
    def sequence_index(self):
        return self.__display.sequence_index

    @sequence_index.setter
    def sequence_index(self, value):
        self.__display.sequence_index = value

    @property
    def slice_center(self):
        return self.__display.slice_center

    @slice_center.setter
    def slice_center(self, value):
        self.__display.slice_center = value

    @property
    def slice_interval(self):
        return self.__display.slice_interval

    @slice_interval.setter
    def slice_interval(self, value):
        self.__display.slice_interval = value

    @property
    def slice_width(self):
        return self.__display.slice_width

    @slice_width.setter
    def slice_width(self, value):
        self.__display.slice_width = value

    def save_properties(self) -> typing.Tuple:
        return (
            self.complex_display_type,
            self.display_limits,
            self.color_map_id,
            self.sequence_index,
            self.collection_index,
            self.slice_center,
            self.slice_interval,
        )

    def restore_properties(self, properties: typing.Tuple) -> None:
        self.complex_display_type = properties[0]
        self.display_limits = properties[1]
        self.color_map_id = properties[2]
        self.sequence_index = properties[3]
        self.collection_index = properties[4]
        self.slice_center = properties[5]
        self.slice_interval = properties[6]

    def add_calculated_display_values_listener(self, callback, send=True):
        return self.__display.add_calculated_display_values_listener(callback, send)

    def get_calculated_display_values(self, immediate: bool=False) -> Display.DisplayValues:
        return self.__display.get_calculated_display_values(immediate)

    def reset_display_limits(self):
        self.__display.reset_display_limits()


class DisplayItem(Observable.Observable, Persistence.PersistentObject):
    def __init__(self, item_uuid=None):
        super().__init__()
        self.uuid = item_uuid if item_uuid else self.uuid
        self.__container_weak_ref = None
        self.define_property("created", datetime.datetime.utcnow(), converter=DataItem.DatetimeToStringConverter(), changed=self.__property_changed)
        # windows utcnow has a resolution of 1ms, this sleep can guarantee unique times for all created times during a particular test.
        # this is not my favorite solution since it limits library item creation to 1000/s but until I find a better solution, this is my compromise.
        time.sleep(0.001)
        self.define_property("display_type", changed=self.__display_type_changed)
        self.define_property("title", hidden=True, changed=self.__property_changed)
        self.define_property("caption", hidden=True, changed=self.__property_changed)
        self.define_property("description", hidden=True, changed=self.__property_changed)
        self.define_property("session_id", hidden=True, changed=self.__property_changed)
        self.define_property("data_item_references", list(), changed=self.__property_changed)
        self.define_item("display", Display.display_factory, self.__display_changed)
        self.define_relationship("graphics", Graphics.factory, insert=self.__insert_graphic, remove=self.__remove_graphic)
        self.__data_items = list()
        self.__data_item_will_change_listeners = list()
        self.__data_item_did_change_listeners = list()
        self.__data_item_item_changed_listeners = list()
        self.__data_item_data_item_changed_listeners = list()
        self.__data_item_data_changed_listeners = list()
        self.__data_item_description_changed_listeners = list()
        self.__graphic_changed_listeners = list()
        self.__suspendable_storage_cache = None
        self.__display_item_change_count = 0
        self.__display_item_change_count_lock = threading.RLock()
        self.__display_ref_count = 0
        self.graphic_selection = GraphicSelection()
        self.graphic_selection_changed_event = Event.Event()
        self.item_changed_event = Event.Event()
        self.about_to_be_removed_event = Event.Event()
        self._about_to_be_removed = False
        self._closed = False
        self.set_item("display", Display.Display())
        self.__display_data_channels = list()
        self.__display_about_to_be_removed_listener = self.display.about_to_be_removed_event.listen(self.about_to_be_removed_event.fire)

        def graphic_selection_changed():
            # relay the message
            self.graphic_selection_changed_event.fire(self.graphic_selection)

        self.__graphic_selection_changed_event_listener = self.graphic_selection.changed_event.listen(graphic_selection_changed)

    def close(self):
        self.__display_about_to_be_removed_listener.close()
        self.__display_about_to_be_removed_listener = None
        self.__graphic_selection_changed_event_listener.close()
        self.__graphic_selection_changed_event_listener = None
        self.set_item("display", None)
        for data_item_will_change_listener in self.__data_item_will_change_listeners:
            data_item_will_change_listener.close()
        self.__data_item_will_change_listeners = list()
        for data_item_did_change_listener in self.__data_item_did_change_listeners:
            data_item_did_change_listener.close()
        self.__data_item_did_change_listeners = list()
        for data_item_item_changed_listener in self.__data_item_item_changed_listeners:
            data_item_item_changed_listener.close()
        self.__data_item_item_changed_listeners = list()
        for data_item_data_item_changed_listener in self.__data_item_data_item_changed_listeners:
            data_item_data_item_changed_listener.close()
        self.__data_item_data_item_changed_listeners = list()
        for data_item_data_item_content_changed_listener in self.__data_item_data_changed_listeners:
            data_item_data_item_content_changed_listener.close()
        self.__data_item_data_changed_listeners = list()
        for data_item_description_changed_listener in self.__data_item_description_changed_listeners:
            data_item_description_changed_listener.close()
        self.__data_item_description_changed_listeners = list()
        for display_data_channel in self.__display_data_channels:
            display_data_channel.close()
        self.__display_data_channels = list()
        self.__data_items = list()
        for graphic in copy.copy(self.graphics):
            self.__disconnect_graphic(graphic, 0)
            graphic.close()
        self.graphic_selection = None
        assert self._about_to_be_removed
        assert not self._closed
        self._closed = True
        self.__container_weak_ref = None

    def __copy__(self):
        assert False

    def __deepcopy__(self, memo):
        display_item_copy = self.__class__()
        # metadata
        display_item_copy._set_persistent_property_value("title", self._get_persistent_property_value("title"))
        display_item_copy._set_persistent_property_value("caption", self._get_persistent_property_value("caption"))
        display_item_copy._set_persistent_property_value("description", self._get_persistent_property_value("description"))
        display_item_copy._set_persistent_property_value("session_id", self._get_persistent_property_value("session_id"))
        display_item_copy.created = self.created
        # display
        display_item_copy.display = copy.deepcopy(self.display)
        for graphic in self.graphics:
            display_item_copy.add_graphic(copy.deepcopy(graphic))
        display_item_copy.data_item_references = copy.deepcopy(self.data_item_references)
        memo[id(self)] = display_item_copy
        return display_item_copy

    @property
    def container(self):
        return self.__container_weak_ref()

    def about_to_close(self):
        self.__disconnect_data_sources()

    def about_to_be_inserted(self, container):
        assert self.__container_weak_ref is None
        self.__container_weak_ref = weakref.ref(container)

    def about_to_be_removed(self):
        # called before close and before item is removed from its container
        for graphic in self.graphics:
            graphic.about_to_be_removed()
        self.about_to_be_removed_event.fire()
        assert not self._about_to_be_removed
        self._about_to_be_removed = True

    def insert_model_item(self, container, name, before_index, item):
        """Insert a model item. Let this item's container do it if possible; otherwise do it directly.

        Passing responsibility to this item's container allows the library to easily track dependencies.
        However, if this item isn't yet in the library hierarchy, then do the operation directly.
        """
        if self.__container_weak_ref:
            self.container.insert_model_item(container, name, before_index, item)
        else:
            container.insert_item(name, before_index, item)

    def remove_model_item(self, container, name, item, *, safe: bool=False) -> typing.Optional[typing.Sequence]:
        """Remove a model item. Let this item's container do it if possible; otherwise do it directly.

        Passing responsibility to this item's container allows the library to easily track dependencies.
        However, if this item isn't yet in the library hierarchy, then do the operation directly.
        """
        if self.__container_weak_ref:
            return self.container.remove_model_item(container, name, item, safe=safe)
        else:
            container.remove_item(name, item)
            return None

    # call this when the listeners need to be updated (via data_item_content_changed).
    # Calling this method will send the data_item_content_changed method to each listener by using the method
    # data_item_changes.
    def _notify_display_item_content_changed(self):
        with self.display_item_changes():
            pass

    # override from storage to watch for changes to this library item. notify observers.
    def notify_property_changed(self, key):
        super().notify_property_changed(key)
        self._notify_display_item_content_changed()

    def __display_type_changed(self, name, value):
        self.__property_changed(name, value)
        self.display._display_type = value
        self.display.display_changed_event.fire()

    def __property_changed(self, name, value):
        self.notify_property_changed(name)
        if name == "title":
            self.notify_property_changed("displayed_title")

    def clone(self) -> "DisplayItem":
        display_item = self.__class__()
        display_item.uuid = self.uuid
        display_item.display = self.display.clone()
        for graphic in self.graphics:
            display_item.add_graphic(graphic.clone())
        return display_item

    def snapshot(self):
        """Return a new library item which is a copy of this one with any dynamic behavior made static."""
        display_item = self.__class__()
        # metadata
        display_item._set_persistent_property_value("title", self._get_persistent_property_value("title"))
        display_item._set_persistent_property_value("caption", self._get_persistent_property_value("caption"))
        display_item._set_persistent_property_value("description", self._get_persistent_property_value("description"))
        display_item._set_persistent_property_value("session_id", self._get_persistent_property_value("session_id"))
        display_item.created = self.created
        display_item.display = copy.deepcopy(self.display)
        for graphic in self.graphics:
            display_item.add_graphic(graphic.snapshot())
        return display_item

    def set_storage_cache(self, storage_cache):
        self.__suspendable_storage_cache = Cache.SuspendableCache(storage_cache)
        self.display.set_storage_cache(self._suspendable_storage_cache)

    @property
    def _suspendable_storage_cache(self):
        return self.__suspendable_storage_cache

    def read_from_dict(self, properties):
        super().read_from_dict(properties)
        if self.created is None:  # invalid timestamp -- set property to now but don't trigger change
            timestamp = datetime.datetime.now()
            self._get_persistent_property("created").value = timestamp

    @property
    def properties(self):
        """ Used for debugging. """
        if self.persistent_object_context:
            return self.persistent_object_context.get_properties(self)
        return dict()

    def __display_changed(self, name, old_display, new_display):
        if new_display != old_display:
            if old_display:
                if self.__display_ref_count > 0:
                    old_display._relinquish_master()
                old_display.about_to_be_removed()
                old_display.close()
            if new_display:
                new_display.about_to_be_inserted(self)
                if self.__display_ref_count > 0:
                    new_display._become_master()

    def display_item_changes(self):
        # return a context manager to batch up a set of changes so that listeners
        # are only notified after the last change is complete.
        display_item = self
        class ContextManager:
            def __enter__(self):
                display_item._begin_display_item_changes()
                return self
            def __exit__(self, type, value, traceback):
                display_item._end_display_item_changes()
        return ContextManager()

    def _begin_display_item_changes(self):
        with self.__display_item_change_count_lock:
            self.__display_item_change_count += 1

    def _end_display_item_changes(self):
        with self.__display_item_change_count_lock:
            self.__display_item_change_count -= 1
            change_count = self.__display_item_change_count
        # if the change count is now zero, it means that we're ready to notify listeners.
        if change_count == 0:
            self.__item_changed()
            self._update_displays()  # this ensures that the display will validate

    def increment_display_ref_count(self, amount: int=1):
        """Increment display reference count to indicate this library item is currently displayed."""
        display_ref_count = self.__display_ref_count
        self.__display_ref_count += amount
        if display_ref_count == 0:
            display = self.display
            if display:
                display._become_master()
        for data_item in self.data_items:
            for _ in range(amount):
                data_item.increment_data_ref_count()

    def decrement_display_ref_count(self, amount: int=1):
        """Decrement display reference count to indicate this library item is no longer displayed."""
        assert not self._closed
        self.__display_ref_count -= amount
        if self.__display_ref_count == 0:
            display = self.display
            if display:
                display._relinquish_master()
        for data_item in self.data_items:
            for _ in range(amount):
                data_item.decrement_data_ref_count()

    @property
    def _display_ref_count(self):
        return self.__display_ref_count

    def __data_item_will_change(self):
        self._begin_display_item_changes()

    def __data_item_did_change(self):
        self._end_display_item_changes()

    def __item_changed(self):
        # this event is only triggered when the data item changed live state; everything else goes through
        # the data changed messages.
        self.item_changed_event.fire()

    @property
    def display_data_channels(self) -> typing.Sequence[DisplayDataChannel]:
        return self.__display_data_channels

    @property
    def display_data_channel(self) -> DisplayDataChannel:
        return self.__display_data_channels[0] if len(self.__display_data_channels) > 0 else None

    def _update_displays(self):
        xdata_list = [data_item.xdata if data_item else None for data_item in self.data_items]
        self.display.update_xdata_list(xdata_list)

    def _description_changed(self):
        self.notify_property_changed("title")
        self.notify_property_changed("caption")
        self.notify_property_changed("description")
        self.notify_property_changed("session_id")
        self.notify_property_changed("displayed_title")

    def __get_used_value(self, key: str, default_value):
        if self._get_persistent_property_value(key) is not None:
            return self._get_persistent_property_value(key)
        if self.data_item and getattr(self.data_item, key, None):
            return getattr(self.data_item, key)
        return default_value

    def __set_cascaded_value(self, key: str, value) -> None:
        if self.data_item:
            self._set_persistent_property_value(key, None)
            setattr(self.data_item, key, value)
        else:
            self._set_persistent_property_value(key, value)
            self._description_changed()

    @property
    def text_for_filter(self) -> str:
        return " ".join([self.displayed_title, self.caption, self.description])

    @property
    def displayed_title(self):
        if self.data_item and getattr(self.data_item, "displayed_title", None):
            return self.data_item.displayed_title
        else:
            return self.title

    @property
    def title(self) -> str:
        return self.__get_used_value("title", DataItem.UNTITLED_STR)

    @title.setter
    def title(self, value: str) -> None:
        self.__set_cascaded_value("title", str(value) if value is not None else str())

    @property
    def caption(self) -> str:
        return self.__get_used_value("caption", str())

    @caption.setter
    def caption(self, value: str) -> None:
        self.__set_cascaded_value("caption", str(value) if value is not None else str())

    @property
    def description(self) -> str:
        return self.__get_used_value("description", str())

    @description.setter
    def description(self, value: str) -> None:
        self.__set_cascaded_value("description", str(value) if value is not None else str())

    @property
    def session_id(self) -> str:
        return self.__get_used_value("session_id", str())

    @session_id.setter
    def session_id(self, value: str) -> None:
        self.__set_cascaded_value("session_id", str(value) if value is not None else str())

    def connect_data_items(self, lookup_data_item):
        display = self.display
        self.__data_items = [lookup_data_item(uuid.UUID(data_item_reference)) for data_item_reference in self.data_item_references]
        self.__display_data_channels = [DisplayDataChannel(data_item, display) for data_item in self.__data_items]
        for data_item in self.__data_items:
            self.__data_item_will_change_listeners.append(data_item.will_change_event.listen(self.__data_item_will_change) if data_item else None)
            self.__data_item_did_change_listeners.append(data_item.did_change_event.listen(self.__data_item_did_change) if data_item else None)
            self.__data_item_item_changed_listeners.append(data_item.item_changed_event.listen(self.__item_changed) if data_item else None)
            self.__data_item_data_item_changed_listeners.append(data_item.data_item_changed_event.listen(self.__item_changed) if data_item else None)
            self.__data_item_data_changed_listeners.append(data_item.data_changed_event.listen(self.__item_changed) if data_item else None)
            self.__data_item_description_changed_listeners.append(data_item.description_changed_event.listen(self._description_changed) if data_item else None)
        self._update_displays()  # this ensures that the display will validate

    def append_data_item(self, data_item):
        self.insert_data_item(len(self.data_items), data_item)

    def insert_data_item(self, before_index, data_item):
        data_item_references = self.data_item_references
        data_item_references.insert(before_index, str(data_item.uuid))
        self.__data_items.insert(before_index, data_item)
        self.__display_data_channels.insert(before_index, DisplayDataChannel(data_item, self.display))
        self.__data_item_will_change_listeners.insert(before_index, data_item.will_change_event.listen(self.__data_item_will_change))
        self.__data_item_did_change_listeners.insert(before_index, data_item.did_change_event.listen(self.__data_item_did_change))
        self.__data_item_item_changed_listeners.insert(before_index, data_item.item_changed_event.listen(self.__item_changed))
        self.__data_item_data_item_changed_listeners.insert(before_index, data_item.data_item_changed_event.listen(self.__item_changed))
        self.__data_item_data_changed_listeners.insert(before_index, data_item.data_changed_event.listen(self.__item_changed))
        self.__data_item_description_changed_listeners.insert(before_index, data_item.description_changed_event.listen(self._description_changed))
        self.data_item_references = data_item_references

    def remove_data_item(self, data_item):
        data_item_references = self.data_item_references
        data_item_references.remove(str(data_item.uuid))
        index = self.__data_items.index(data_item)
        self.__data_item_will_change_listeners[index].close()
        del self.__data_item_will_change_listeners[index]
        self.__data_item_did_change_listeners[index].close()
        del self.__data_item_did_change_listeners[index]
        self.__data_item_item_changed_listeners[index].close()
        del self.__data_item_item_changed_listeners[index]
        self.__data_item_data_item_changed_listeners[index].close()
        del self.__data_item_data_item_changed_listeners[index]
        self.__data_item_data_changed_listeners[index].close()
        del self.__data_item_data_changed_listeners[index]
        self.__data_item_description_changed_listeners[index].close()
        del self.__data_item_description_changed_listeners[index]
        del self.__data_items[index]
        self.__display_data_channels[index].close()
        del self.__display_data_channels[index]
        self.data_item_references = data_item_references

    @property
    def data_items(self) -> typing.Sequence[DataItem.DataItem]:
        return self.__data_items

    @property
    def data_item(self) -> typing.Optional[DataItem.DataItem]:
        return self.__data_items[0] if len(self.__data_items) == 1 else None

    @property
    def selected_graphics(self) -> typing.Sequence[Graphics.Graphic]:
        return [self.graphics[i] for i in self.graphic_selection.indexes]

    def __insert_graphic(self, name, before_index, graphic):
        graphic.about_to_be_inserted(self)
        graphic_changed_listener = graphic.graphic_changed_event.listen(functools.partial(self.__graphic_changed, graphic))
        self.__graphic_changed_listeners.insert(before_index, graphic_changed_listener)
        self.graphic_selection.insert_index(before_index)
        self.display.display_changed_event.fire()
        self.notify_insert_item("graphics", graphic, before_index)

    def __remove_graphic(self, name, index, graphic):
        graphic.about_to_be_removed()
        self.__disconnect_graphic(graphic, index)
        graphic.close()

    def __disconnect_graphic(self, graphic, index):
        graphic_changed_listener = self.__graphic_changed_listeners[index]
        graphic_changed_listener.close()
        self.__graphic_changed_listeners.remove(graphic_changed_listener)
        self.graphic_selection.remove_index(index)
        self.display.display_changed_event.fire()
        self.notify_remove_item("graphics", graphic, index)

    def insert_graphic(self, before_index, graphic):
        """Insert a graphic before the index, but do it through the container, so dependencies can be tracked."""
        self.insert_model_item(self, "graphics", before_index, graphic)

    def add_graphic(self, graphic):
        """Append a graphic, but do it through the container, so dependencies can be tracked."""
        self.insert_model_item(self, "graphics", self.item_count("graphics"), graphic)

    def remove_graphic(self, graphic: Graphics.Graphic, *, safe: bool=False) -> typing.Optional[typing.Sequence]:
        """Remove a graphic, but do it through the container, so dependencies can be tracked."""
        return self.remove_model_item(self, "graphics", graphic, safe=safe)

    # this message comes from the graphic. the connection is established when a graphic
    # is added or removed from this object.
    def __graphic_changed(self, graphic):
        self.display.display_changed_event.fire()

    @property
    def size_and_data_format_as_string(self) -> str:
        return self.data_item.size_and_data_format_as_string

    @property
    def date_for_sorting(self):
        data_item_dates = [data_item.date_for_sorting for data_item in self.data_items]
        if len(data_item_dates):
            return max(data_item_dates)
        return self.created

    @property
    def date_for_sorting_local_as_string(self) -> str:
        return self.data_item.date_for_sorting_local_as_string

    @property
    def created_local(self) -> datetime.datetime:
        created_utc = self.created
        tz_minutes = Utility.local_utcoffset_minutes(created_utc)
        return created_utc + datetime.timedelta(minutes=tz_minutes)

    @property
    def created_local_as_string(self) -> str:
        return self.created_local.strftime("%c")

    @property
    def is_live(self) -> bool:
        return any(data_item.is_live for data_item in self.data_items)

    @property
    def category(self) -> str:
        return "temporary" if any(data_item.category == "temporary" for data_item in self.data_items) else "persistent"

    @property
    def status_str(self) -> str:
        if self.data_item.is_live:
            live_metadata = self.data_item.metadata.get("hardware_source", dict())
            frame_index_str = str(live_metadata.get("frame_index", str()))
            partial_str = "{0:d}/{1:d}".format(live_metadata.get("valid_rows"), self.data_item.dimensional_shape[0]) if "valid_rows" in live_metadata else str()
            return "{0:s} {1:s} {2:s}".format(_("Live"), frame_index_str, partial_str)
        return str()

    @property
    def display_type(self) -> str:
        return self.display.display_type

    @display_type.setter
    def display_type(self, value: str) -> None:
        self.display.display_type = value

    @property
    def used_display_type(self) -> str:
        display_type = self.display_type
        if not display_type in ("line_plot", "image", "display_script"):
            data_item = self.data_item
            display_data_shape = data_item.display_data_shape
            valid_data = (data_item is not None) and (functools.reduce(operator.mul, display_data_shape) > 0 if display_data_shape else False)
            if valid_data:
                if data_item.collection_dimension_count == 2 and data_item.datum_dimension_count == 1:
                    display_type = "image"
                elif data_item.datum_dimension_count == 1:
                    display_type = "line_plot"
                elif data_item.datum_dimension_count == 2:
                    display_type = "image"
                # override
                if self.display.display_script:
                    display_type = "display_script"
        return display_type

    @property
    def legend_labels(self) -> typing.Sequence[str]:
        return self.display.legend_labels

    @legend_labels.setter
    def legend_labels(self, value: typing.Sequence[str]) -> None:
        self.display.legend_labels = value

    def view_to_intervals(self, data_and_metadata: DataAndMetadata.DataAndMetadata, intervals: typing.List[typing.Tuple[float, float]]) -> None:
        self.display.view_to_intervals(data_and_metadata, intervals)